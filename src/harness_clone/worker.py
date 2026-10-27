"""Container-only task execution entrypoint."""

import json
import os
from pathlib import Path


def main():
    if not Path("/control/request.json").is_file() or not os.environ.get("HARNESS_GATEWAY_URL"):
        raise RuntimeError("worker requires a configured container environment")
    from deepagents.backends import LocalShellBackend
    import time
    import httpx
    from .compiler import compile_agent
    from .contracts import ExperimentConfig, TaskSpec

    request = json.loads(Path("/control/request.json").read_text())
    config = ExperimentConfig.model_validate(request["config"])
    task = TaskSpec.model_validate(request["task"])
    deadline = time.monotonic() + min(30, config.timeout_seconds)
    while True:
        try:
            httpx.get(
                os.environ["HARNESS_GATEWAY_URL"].removesuffix("/v1") + "/health", timeout=2
            ).raise_for_status()
            break
        except httpx.HTTPError:
            if time.monotonic() > deadline:
                raise RuntimeError("inference gateway did not become ready")
            time.sleep(0.2)
    from deepagents.backends.protocol import GrepResult

    class BoundedBackend(LocalShellBackend):
        # The Python grep fallback reads whole files into this process, so a
        # search rooted at / (proc, sys, site-packages) exhausts container memory.
        def grep(self, pattern, path=None, *args, **kwargs):
            if not path or os.path.normpath(path) == "/":
                return GrepResult(
                    error="grep path must be a directory such as /source, /workspace or /output",
                    matches=[],
                )
            return super().grep(pattern, path, *args, **kwargs)

    backend = BoundedBackend(
        root_dir="/workspace",
        virtual_mode=False,
        env={"PATH": "/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"},
        timeout=config.timeout_seconds,
    )
    agent = compile_agent(Path("/bundle"), backend, config)
    result = agent.invoke(
        {
            "messages": [
                {
                    "role": "user",
                    "content": task.prompt
                    + "\nRead sources in /source/. Write exactly these outputs under /output/: "
                    + ", ".join(task.output_paths),
                }
            ]
        },
        config={
            "configurable": {"thread_id": request["execution_id"]},
            "recursion_limit": config.max_tool_calls * 3 + 10,
        },
    )
    trace = []
    for i, message in enumerate(result["messages"]):
        trace.append(
            {
                "id": str(i),
                "type": message.type,
                "content": str(message.content)[:16000],
                "tool_calls": getattr(message, "tool_calls", []),
            }
        )
    Path("/workspace/terminal.json").write_text(json.dumps({"trace": trace}))


if __name__ == "__main__":
    main()
