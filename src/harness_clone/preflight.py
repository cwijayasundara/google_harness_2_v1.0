"""Capabilities are reported as verified only after the actual check succeeds."""

import importlib.metadata
import tempfile
from pathlib import Path

from langchain_core.messages import ToolMessage
from langchain_core.tools import tool

from .contracts import ExperimentConfig
from .providers import assemble_stream, make_model, normalize_usage
from .storage import BudgetLedger


def run_preflight(config: ExperimentConfig, live: bool = False) -> dict:
    versions = {
        p: importlib.metadata.version(p) for p in ["deepagents", "langgraph", "langchain-fireworks"]
    }
    if not live:
        return {
            "status": "offline-pass",
            "live_verified": False,
            "model": config.model,
            "versions": versions,
        }
    if config.max_usd <= 0:
        raise ValueError("live preflight requires an explicit nonzero spending cap")

    @tool
    def add(a: int, b: int) -> int:
        """Add two integers."""
        return a + b

    checks = {}
    with tempfile.TemporaryDirectory() as tmp:
        budget = BudgetLedger(Path(tmp) / "budget.sqlite", config.max_usd)
        model = make_model(config, "executor")

        def call(label, messages, target=model):
            budget.reserve(label, config.stage_usd)
            try:
                result = target.invoke(messages)
            except Exception:
                from .contracts import Usage

                budget.settle(label, Usage(known=False))
                raise
            budget.settle(label, normalize_usage(result))
            return result

        try:
            first = call(
                "tool",
                [("human", "Use add to calculate 17+25.")],
                model.bind_tools([add], tool_choice="add"),
            )
            checks["tool_call"] = bool(first.tool_calls)
            if not first.tool_calls:
                raise ValueError("provider did not return requested tool call")
            tc = first.tool_calls[0]
            follow = call(
                "follow",
                [
                    ("human", "Use add to calculate 17+25."),
                    first,
                    ToolMessage(content=str(add.invoke(tc["args"])), tool_call_id=tc["id"]),
                ],
            )
            checks["tool_roundtrip"] = "42" in str(follow.content)
            checks["usage"] = normalize_usage(first).known and normalize_usage(follow).known
            checks["reasoning_history"] = (
                "observed" if first.additional_kwargs.get("reasoning_content") else "not-observed"
            )
            budget.reserve("stream", config.stage_usd)
            stream = assemble_stream(model.stream("Reply with OK."))
            budget.settle("stream", normalize_usage(stream))
            checks["stream"] = bool(stream.content)
            structured = call(
                "structured", [("human", 'Return only JSON with key "status" and value "ok".')]
            )
            import json

            checks["structured"] = json.loads(str(structured.content))["status"] == "ok"
            # Full executor compaction/restart check is separate: do not claim it here.
            checks["executor_compaction"] = "unverified"
            return {
                "status": "live-pass"
                if all(
                    checks[k]
                    for k in ("tool_call", "tool_roundtrip", "usage", "stream", "structured")
                )
                else "failed",
                "live_verified": True,
                "checks": checks,
                "versions": versions,
                "budget": budget.summary(),
            }
        except Exception as exc:
            return {
                "status": "failed",
                "live_verified": False,
                "checks": checks,
                "error": type(exc).__name__,
                "budget": budget.summary(),
                "versions": versions,
            }
