import os
import subprocess
from pathlib import Path

import pytest


@pytest.mark.docker
def test_container_cannot_write_bundle_or_reach_public_network():
    if os.environ.get("HARNESS_TEST_DOCKER") != "1":
        pytest.skip("set HARNESS_TEST_DOCKER=1 after building the executor image")
    # This is a real container test, not a simulated boundary assertion.
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--user=65532:65532",
            "harness-clone-executor:0.1",
            "python",
            "-c",
            "import socket; from pathlib import Path; denied=False\ntry: Path('/app/escape').write_text('bad')\nexcept OSError: denied=True\nassert denied\ntry: socket.create_connection(('1.1.1.1',443),timeout=1); raise AssertionError('egress allowed')\nexcept OSError: pass",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert result.returncode == 0, result.stderr


@pytest.mark.docker
def test_real_deep_agents_graph_executes_candidate_script_in_container():
    if os.environ.get("HARNESS_TEST_DOCKER") != "1":
        pytest.skip("requires built images and Docker")
    code = """
import sys,shutil
from pathlib import Path
sys.path.insert(0,"/tests")
from fakes import ScriptedModel
from langchain_core.messages import AIMessage
from deepagents.backends import LocalShellBackend
from harness_clone import compiler
from harness_clone.contracts import ExperimentConfig
shutil.copytree("/app/examples/base-harness","/tmp/bundle")
Path("/tmp/bundle/scripts/probe.py").write_text('from pathlib import Path\\nPath("/output/answer.txt").write_text("isolated execution")\\nPath("/workspace/memory/AGENTS.md").write_text("private update")\\n')
model=ScriptedModel(replies=[AIMessage(content="",tool_calls=[{"name":"execute","args":{"command":"python scripts/probe.py"},"id":"probe","type":"tool_call"}]),AIMessage(content="done")])
compiler.make_model=lambda config,role:model
backend=LocalShellBackend(root_dir="/workspace",virtual_mode=False,env={"PATH":"/opt/venv/bin:/usr/local/bin:/usr/bin:/bin"})
agent=compiler.compile_agent(Path("/tmp/bundle"),backend,ExperimentConfig())
agent.invoke({"messages":[{"role":"user","content":"run the probe"}]})
assert Path("/output/answer.txt").read_text()=="isolated execution"
assert Path("/workspace/memory/AGENTS.md").read_text()=="private update"
assert Path("/tmp/bundle/memory/AGENTS.md").read_text()!="private update"
"""
    tests = Path(__file__).resolve().parents[1]
    result = subprocess.run(
        [
            "docker",
            "run",
            "--rm",
            "--network=none",
            "--read-only",
            "--user=65532:65532",
            "--cap-drop=ALL",
            "--tmpfs",
            "/tmp:rw,mode=1777",
            "--tmpfs",
            "/workspace:rw,mode=1777",
            "--tmpfs",
            "/output:rw,mode=1777",
            "--mount",
            f"type=bind,src={tests},dst=/tests,readonly",
            "harness-clone-executor:0.1",
            "python",
            "-c",
            code,
        ],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
