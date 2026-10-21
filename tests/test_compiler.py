import shutil
from pathlib import Path

from deepagents.backends import LocalShellBackend
from langchain_core.messages import AIMessage

from harness_clone.contracts import ExperimentConfig
from fakes import ScriptedModel


def setup_agent(tmp_path, monkeypatch, replies=None):
    from harness_clone import compiler
    bundle=tmp_path/"bundle"
    shutil.copytree(Path(__file__).parents[1]/"examples/base-harness",bundle)
    runtime=tmp_path/"runtime"
    runtime.mkdir()
    model=ScriptedModel(**({"replies":replies} if replies else {}))
    monkeypatch.setattr(compiler,"make_model",lambda config,role:model)
    backend=LocalShellBackend(root_dir=runtime,virtual_mode=False,env={"PATH":"/usr/bin:/bin:/usr/local/bin"})
    return compiler,bundle,runtime,model,backend


def test_prompt_memory_skills_and_middleware_affect_execution(tmp_path,monkeypatch):
    compiler,bundle,runtime,model,backend=setup_agent(tmp_path,monkeypatch)
    (bundle/"middleware/audit.py").write_text('from langchain.agents.middleware import AgentMiddleware\nclass Audit(AgentMiddleware):\n def before_model(self,state,runtime):\n  open("'+str(runtime/'audit.txt')+'","w").write("called")\ndef create_middleware(config): return Audit()\n')
    agent=compiler.compile_agent(bundle,backend,ExperimentConfig())
    result=agent.invoke({"messages":[{"role":"user","content":"hello"}]})
    system=str(model.seen[-1][0].content)
    assert "Complete the task" in system and "Report uncertainty" in system
    assert "Prior examples do not establish facts" in system
    assert "verify" in system
    assert (runtime/"audit.txt").read_text()=="called"
    assert result["messages"][-1].content == "done"


def test_tool_guidance_cannot_change_tool_schema(tmp_path,monkeypatch):
    compiler,bundle,runtime,model,backend=setup_agent(tmp_path,monkeypatch)
    agent=compiler.compile_agent(bundle,backend,ExperimentConfig())
    agent.invoke({"messages":[{"role":"user","content":"hello"}]})
    tools={t.name:t for t in model.bound[-1]}
    assert tools["execute"].description.startswith("Execute a command in this task")
    assert "command" in tools["execute"].args


def test_scripts_are_copied_and_executable_from_private_workspace(tmp_path,monkeypatch):
    replies=[AIMessage(content="",tool_calls=[{"name":"execute","args":{"command":"python3 scripts/probe.py"},"id":"call-script","type":"tool_call"}]),AIMessage(content="verified")]
    compiler,bundle,runtime,model,backend=setup_agent(tmp_path,monkeypatch,replies)
    (bundle/"scripts/probe.py").write_text('from pathlib import Path\nPath("script-result.txt").write_text("executed")\n')
    agent=compiler.compile_agent(bundle,backend,ExperimentConfig())
    agent.invoke({"messages":[{"role":"user","content":"run the probe"}]})
    assert (runtime/"script-result.txt").read_text()=="executed"
    assert not (bundle/"script-result.txt").exists()


def test_every_subagent_and_summarizer_use_explicit_model(tmp_path,monkeypatch):
    compiler,bundle,runtime,model,backend=setup_agent(tmp_path,monkeypatch)
    roles=[]
    monkeypatch.setattr(compiler,"make_model",lambda config,role:(roles.append(role) or model))
    compiler.compile_agent(bundle,backend,ExperimentConfig())
    assert {"executor","subagent","summarizer"} <= set(roles)
