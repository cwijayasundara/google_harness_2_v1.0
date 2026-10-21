"""Container-only task execution entrypoint."""
import json
import os
from pathlib import Path


def main():
    if not Path("/control/request.json").is_file() or not os.environ.get("HARNESS_GATEWAY_URL"):
        raise RuntimeError("worker requires a configured container environment")
    from deepagents.backends import LocalShellBackend
    from .compiler import compile_agent
    from .contracts import ExperimentConfig, TaskSpec
    request=json.loads(Path("/control/request.json").read_text())
    config=ExperimentConfig.model_validate(request["config"])
    task=TaskSpec.model_validate(request["task"])
    backend=LocalShellBackend(root_dir="/",virtual_mode=False,env={"PATH":"/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"},timeout=config.timeout_seconds)
    agent=compile_agent(Path("/bundle"),backend,config)
    result=agent.invoke({"messages":[{"role":"user","content":task.prompt+"\nRead sources in /source/. Write exactly these outputs under /output/: "+", ".join(task.output_paths)}]},config={"configurable":{"thread_id":request["execution_id"]},"recursion_limit":config.max_tool_calls*3+10})
    trace=[]
    for i,message in enumerate(result["messages"]):
        trace.append({"id":str(i),"type":message.type,"content":str(message.content)[:16000],"tool_calls":getattr(message,"tool_calls",[])})
    Path("/workspace/terminal.json").write_text(json.dumps({"trace":trace}))


if __name__=="__main__":
    main()
