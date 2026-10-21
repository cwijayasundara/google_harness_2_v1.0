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


class InferenceGateway:
    def __init__(self, config: ExperimentConfig, ledger: BudgetLedger, token: str):
        self.config, self.ledger, self.token = config, ledger, token

    def prepare(self, payload: dict, token: str = "") -> tuple[str, dict]:
        if payload.get("model") != self.config.model:
            raise ValueError("gateway permits only the configured model")
        if not hmac.compare_digest(token, self.token):
            raise ValueError("invalid gateway token")
        if len(json.dumps(payload).encode()) > 2_000_000:
            raise ValueError("request too large")
        payload = dict(payload)
        payload.pop("api_key", None)
        payload["max_tokens"] = min(int(payload.get("max_tokens") or self.config.max_output_tokens), self.config.max_output_tokens)
        if payload["max_tokens"] <= 0 or payload.get("n",1) != 1:
            raise ValueError("invalid output limits")
        if payload.get("stream"):
            payload["stream_options"] = {"include_usage":True}
        # UTF-8 bytes conservatively bound text tokens including tool definitions.
        upper_tokens = len(json.dumps(payload).encode()) + 2048
        if upper_tokens + payload["max_tokens"] > self.config.max_context_tokens:
            raise ValueError("request exceeds conservative context limit")
        ceiling = (Decimal(upper_tokens)*Decimal("0.30")+Decimal(payload["max_tokens"])*Decimal("1.20"))/Decimal(1000000)
        identity = str(uuid.uuid4())
        self.ledger.reserve(identity, ceiling)
        return identity, payload

    def finish(self, identity: str, response: dict):
        self.ledger.settle(identity, normalize_usage(response))


def gateway_server(config: ExperimentConfig, ledger_path: Path, token: str, port: int = 0, host: str = "127.0.0.1"):
    gateway = InferenceGateway(config, BudgetLedger(ledger_path,config.max_usd),token)
    key = os.environ["FIREWORKS_API_KEY"]
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            identity = None
            try:
                if self.path != "/v1/chat/completions":
                    raise ValueError("unsupported gateway path")
                length = int(self.headers.get("Content-Length",0))
                if not 0 < length <= 2_000_000:
                    raise ValueError("invalid body length")
                payload = json.loads(self.rfile.read(length))
                identity,payload = gateway.prepare(payload,self.headers.get("Authorization","").removeprefix("Bearer "))
                response = httpx.post("https://api.fireworks.ai/inference/v1/chat/completions",headers={"Authorization":f"Bearer {key}"},json=payload,timeout=config.timeout_seconds)
                if response.status_code >= 400:
                    # Server errors may have performed work; retain unknown accounting.
                    gateway.ledger.settle(identity,Usage(known=False))
                    self.send_response(response.status_code)
                    body = json.dumps({"error":{"message":"upstream request failed", "type":"upstream_error"}}).encode()
                    content_type = "application/json"
                else:
                    if payload.get("stream"):
                        usage = None
                        for line in response.text.splitlines():
                            if line.startswith("data: ") and line != "data: [DONE]":
                                chunk = json.loads(line[6:])
                                if chunk.get("usage"):
                                    usage = chunk
                        gateway.finish(identity,usage or {})
                        content_type = "text/event-stream"
                    else:
                        gateway.finish(identity,response.json())
                        content_type = "application/json"
                    self.send_response(200)
                    body = response.content
                self.send_header("Content-Type",content_type)
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            except Exception:
                if identity:
                    try:
                        gateway.ledger.settle(identity,Usage(known=False))
                    except ValueError:
                        pass
                body=b'{"error":{"message":"gateway rejected request or upstream failed","type":"gateway_error"}}'
                self.send_response(400)
                self.send_header("Content-Type","application/json")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)
    return ThreadingHTTPServer((host,port),Handler)


if __name__ == "__main__":
    gateway_server(ExperimentConfig.model_validate_json(Path("/control/config.json").read_text()),Path("/ledger/budget.sqlite"),os.environ["HARNESS_GATEWAY_TOKEN"],8080,"0.0.0.0").serve_forever()
