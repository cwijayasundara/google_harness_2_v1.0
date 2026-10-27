"""Credential-holding, fixed-upstream inference gateway. No arbitrary URL forwarding."""

import hmac
import json
import os
import uuid
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import httpx

from .contracts import ExperimentConfig, Usage
from .providers import normalize_usage
from .storage import BudgetLedger


EDIT_OUTPUT_TOKENS = 16384


class GatewayPolicyError(ValueError):
    """A controlled rejection message that is safe to return to the caller."""


def stage_group(stage_id: str) -> str:
    """Repair attempts of one stage share its ceiling: strip /attempt-N."""
    return stage_id.rsplit("/attempt-", 1)[0] if "/attempt-" in stage_id else stage_id


class InferenceGateway:
    def __init__(
        self, config: ExperimentConfig, ledger: BudgetLedger, token: str, stage_id: str = "request"
    ):
        self.config, self.ledger, self.token = config, ledger, token
        self.stage_id = stage_id

    def prepare(self, payload: dict, token: str = "") -> tuple[str, dict]:
        if payload.get("model") != self.config.model:
            raise GatewayPolicyError("gateway permits only the configured model")
        if not hmac.compare_digest(token, self.token):
            raise GatewayPolicyError("invalid gateway token")
        allowed = {
            "model",
            "messages",
            "max_tokens",
            "temperature",
            "top_p",
            "top_k",
            "tools",
            "tool_choice",
            "parallel_tool_calls",
            "stream",
            "stream_options",
            "response_format",
            "stop",
            "frequency_penalty",
            "presence_penalty",
            "repetition_penalty",
            "logprobs",
            "top_logprobs",
            "seed",
            "user",
            "n",
            "reasoning_effort",
            "prompt_cache_key",
        }
        if set(payload) - allowed:
            raise GatewayPolicyError("unsupported unmetered request options")
        if "prompt_cache_key" in payload and not (
            isinstance(payload["prompt_cache_key"], str)
            and 1 <= len(payload["prompt_cache_key"]) <= 256
        ):
            raise GatewayPolicyError("invalid prompt cache key")
        if len(json.dumps(payload).encode()) > 2_000_000:
            raise GatewayPolicyError("request too large")
        payload = dict(payload)
        payload.pop("api_key", None)
        payload["max_tokens"] = min(
            int(payload.get("max_tokens") or self._output_limit()),
            self._output_limit(),
        )
        if payload["max_tokens"] <= 0 or payload.get("n", 1) != 1:
            raise GatewayPolicyError("invalid output limits")
        if payload.get("stream"):
            payload["stream_options"] = {"include_usage": True}
        encoded = len(json.dumps(payload).encode())
        # Two bytes per token still overestimates English, JSON, and code.
        # Counting every byte as a token rejected prompts that fit the window.
        context_tokens = encoded // 2 + 2048
        if context_tokens + payload["max_tokens"] > self.config.max_context_tokens:
            raise GatewayPolicyError("request exceeds conservative context limit")
        # The reservation stays a one-byte-per-token upper bound on cost.
        upper_tokens = encoded + 2048
        ceiling = (
            Decimal(upper_tokens) * Decimal("0.30")
            + Decimal(payload["max_tokens"]) * Decimal("1.20")
        ) / Decimal(1000000)
        identity = self.stage_id + "/" + str(uuid.uuid4())
        group = stage_group(self.stage_id)
        try:
            self.ledger.allocate(group, self.config.stage_usd)
            self.ledger.reserve(identity, ceiling, allocation_id=group)
        except ValueError as exc:
            # Ledger refusals (cap, unknown usage) carry controlled messages; show them.
            raise GatewayPolicyError(str(exc)) from exc
        return identity, payload

    def _output_limit(self) -> int:
        """Edit attempts may spend output on reasoning and still emit a tool call."""
        if "/attempt-" not in self.stage_id:
            return self.config.max_output_tokens
        raw = os.environ.get("HARNESS_MAX_OUTPUT_TOKENS")
        if not raw:
            return max(self.config.max_output_tokens, EDIT_OUTPUT_TOKENS)
        if raw:
            try:
                limit = int(raw)
            except ValueError:
                raise GatewayPolicyError("invalid output cap") from None
            if not 64 <= limit <= 32768:
                raise GatewayPolicyError("invalid output cap")
            return limit
        return self.config.max_output_tokens

    def finish(self, identity: str, response: dict):
        self.ledger.settle(identity, normalize_usage(response))


def gateway_server(
    config: ExperimentConfig,
    ledger_path: Path,
    token: str,
    port: int = 0,
    host: str = "127.0.0.1",
    stage_id: str = "request",
):
    gateway = InferenceGateway(config, BudgetLedger(ledger_path, config.max_usd), token, stage_id)
    key = os.environ["FIREWORKS_API_KEY"]

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.send_response(200 if self.path == "/health" else 404)
            self.end_headers()

        def do_POST(self):
            identity = None
            try:
                if self.path != "/v1/chat/completions":
                    raise GatewayPolicyError("unsupported gateway path")
                length = int(self.headers.get("Content-Length", 0))
                if not 0 < length <= 2_000_000:
                    raise GatewayPolicyError("invalid body length")
                payload = json.loads(self.rfile.read(length))
                identity, payload = gateway.prepare(
                    payload, self.headers.get("Authorization", "").removeprefix("Bearer ")
                )
                response = httpx.post(
                    "https://api.fireworks.ai/inference/v1/chat/completions",
                    headers={"Authorization": f"Bearer {key}"},
                    json=payload,
                    timeout=config.timeout_seconds,
                )
                if response.status_code >= 400:
                    # Server errors may have performed work; retain unknown accounting.
                    gateway.ledger.settle(identity, Usage(known=False))
                    self.send_response(response.status_code)
                    body = json.dumps(
                        {"error": {"message": "upstream request failed", "type": "upstream_error"}}
                    ).encode()
                    content_type = "application/json"
                else:
                    if payload.get("stream"):
                        usage = None
                        for line in response.text.splitlines():
                            if line.startswith("data: ") and line != "data: [DONE]":
                                chunk = json.loads(line[6:])
                                if chunk.get("usage"):
                                    usage = chunk
                        gateway.finish(identity, usage or {})
                        content_type = "text/event-stream"
                    else:
                        gateway.finish(identity, response.json())
                        content_type = "application/json"
                    self.send_response(200)
                    body = response.content
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception as exc:
                if identity:
                    try:
                        gateway.ledger.settle(identity, Usage(known=False))
                    except ValueError:
                        pass
                # Policy rejections happen before provider admission and have
                # controlled messages. Persist those diagnostics without keys,
                # headers, or task contents; keep upstream details private.
                reason = (
                    str(exc)
                    if identity is None and isinstance(exc, GatewayPolicyError)
                    else ("gateway rejected request or upstream failed")
                )
                gateway.ledger.put(
                    "gateway-error",
                    str(uuid.uuid4()),
                    {
                        "stage_id": stage_id,
                        "exception": type(exc).__name__,
                        "reason": reason,
                    },
                )
                body = json.dumps({"error": {"message": reason, "type": "gateway_error"}}).encode()
                self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    gateway_server(
        ExperimentConfig.model_validate_json(Path("/control/config.json").read_text()),
        Path("/ledger/budget.sqlite"),
        os.environ["HARNESS_GATEWAY_TOKEN"],
        8080,
        "0.0.0.0",
        os.environ.get("HARNESS_STAGE_ID", "request"),
    ).serve_forever()
