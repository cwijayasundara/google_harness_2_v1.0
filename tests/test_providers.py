from decimal import Decimal

import pytest
from langchain_core.messages import AIMessage, AIMessageChunk

from harness_clone.contracts import MODEL, ExperimentConfig


@pytest.mark.parametrize(
    "role", ["executor", "INV", "DOM", "COMPOSE", "SELECT", "REFLECT", "subagent", "summarizer"]
)
def test_explicit_model_routing(role, monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-not-a-key")
    from harness_clone.providers import make_model

    model = make_model(ExperimentConfig(), role)
    assert model.model_name == MODEL
    assert model.max_tokens == 4096
    assert model.max_retries == 0


def test_sdk_base_url_has_no_doubled_v1(monkeypatch):
    # The Fireworks SDK appends /v1/chat/completions itself; passing a base
    # that already ends in /v1 produces /v1/v1/chat/completions (live 404).
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-not-a-key")
    from harness_clone.providers import make_model

    direct = make_model(ExperimentConfig(), "executor")
    assert str(direct._sdk_client.base_url).rstrip("/") == "https://api.fireworks.ai/inference"
    via_gateway = make_model(
        ExperimentConfig(), "executor", gateway_url="http://gw:8080/v1", gateway_token="t"
    )
    assert str(via_gateway._sdk_client.base_url).rstrip("/") == "http://gw:8080"


def test_unknown_profile_and_role_rejected(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "test-not-a-key")
    from harness_clone.providers import make_model

    with pytest.raises(ValueError):
        make_model(ExperimentConfig(harness_profile="unknown"), "executor")
    with pytest.raises(ValueError):
        make_model(ExperimentConfig(), "unknown")


def test_usage_cache_and_absent_metadata():
    from harness_clone.providers import normalize_usage

    msg = AIMessage(
        content="ok",
        usage_metadata={
            "input_tokens": 1000,
            "output_tokens": 100,
            "total_tokens": 1100,
            "input_token_details": {"cache_read": 400},
        },
    )
    u = normalize_usage(msg)
    assert u.input_tokens == 1000
    assert u.cached_tokens == 400
    assert u.usd == Decimal("0.0003024")
    assert normalize_usage(AIMessage(content="ok")).known is False


def test_streaming_tool_arguments_and_reasoning():
    from harness_clone.providers import assemble_stream

    chunks = [
        AIMessageChunk(
            content="",
            additional_kwargs={"reasoning_content": "inspect"},
            tool_call_chunks=[{"name": "read", "args": '{"path":', "id": "call-1", "index": 0}],
        ),
        AIMessageChunk(
            content="", tool_call_chunks=[{"name": None, "args": '"file"}', "id": None, "index": 0}]
        ),
    ]
    message = assemble_stream(chunks)
    assert message.tool_calls[0]["args"] == {"path": "file"}
    assert message.additional_kwargs["reasoning_content"] == "inspect"


def test_live_preflight_zero_cap_rejected():
    from harness_clone.preflight import run_preflight

    with pytest.raises(ValueError, match="cap"):
        run_preflight(ExperimentConfig(), live=True)
    report = run_preflight(ExperimentConfig(), live=False)
    assert report["status"] == "offline-pass"
    assert report["live_verified"] is False


@pytest.mark.parametrize("status,want", [(429, True), (503, True), (401, False), (400, False)])
def test_retryable_errors(status, want):
    from harness_clone.providers import retryable_status

    assert retryable_status(status) is want
