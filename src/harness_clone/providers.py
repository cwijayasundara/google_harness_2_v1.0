"""Explicit Fireworks construction; no implicit provider fallbacks."""
import os
from decimal import Decimal
from functools import reduce

from langchain_core.messages import AIMessage, message_chunk_to_message
from langchain_fireworks import ChatFireworks

from .contracts import ExperimentConfig, Usage

ROLES = {"executor", "INV", "DOM", "COMPOSE", "SELECT", "REFLECT", "subagent", "summarizer"}


def make_model(config: ExperimentConfig, role: str) -> ChatFireworks:
    if role not in ROLES or config.harness_profile != "harness-clone-v1":
        raise ValueError("unknown role or harness profile")
    gateway = os.environ.get("HARNESS_GATEWAY_URL")
    key = os.environ.get("HARNESS_GATEWAY_TOKEN") if gateway else os.environ.get("FIREWORKS_API_KEY")
    if not key:
        raise ValueError("configure FIREWORKS_API_KEY or the harness inference gateway")
    return ChatFireworks(
        model=config.model,
        api_key=key,
        base_url=gateway or "https://api.fireworks.ai/inference/v1",
        temperature=0,
        max_tokens=config.max_output_tokens,
        timeout=config.timeout_seconds,
        max_retries=0,
        profile={"max_input_tokens":config.max_context_tokens, "tool_calling":True, "structured_output":True},
    )


def normalize_usage(response: AIMessage | dict) -> Usage:
    data = response.get("usage") if isinstance(response, dict) else response.usage_metadata
    if not data:
        return Usage(known=False)
    incoming = int(data.get("input_tokens", data.get("prompt_tokens", 0)))
    outgoing = int(data.get("output_tokens", data.get("completion_tokens", 0)))
    details = data.get("input_token_details") or data.get("prompt_tokens_details") or {}
    cached = int(details.get("cache_read", details.get("cached_tokens", 0)))
    if min(incoming, outgoing, cached) < 0 or cached > incoming:
        return Usage(known=False)
    price = (Decimal(incoming-cached)*Decimal("0.30") + Decimal(cached)*Decimal("0.006") + Decimal(outgoing)*Decimal("1.20"))/Decimal(1000000)
    return Usage(input_tokens=incoming, cached_tokens=cached, output_tokens=outgoing, usd=price)


def assemble_stream(chunks) -> AIMessage:
    chunks = list(chunks)
    if not chunks:
        raise ValueError("empty provider stream")
    return message_chunk_to_message(reduce(lambda a,b: a+b, chunks))


def retryable_status(status: int) -> bool:
    return status in {408, 409, 429} or 500 <= status <= 599
