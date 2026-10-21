"""Docker is the isolation boundary. No host-shell execution fallback."""
import json
import os
import secrets
import shutil
import subprocess
import tempfile
import uuid
from pathlib import Path

from .bundles import safe_path
from .contracts import ExecutionRecord, ExperimentConfig, TaskSpec
from .storage import RunStore
from .tasks import validate_task


def collect_artifacts(root: Path, task: TaskSpec) -> tuple[dict[str,str], bool, str | None]:
    artifacts = {}
    for rel in task.output_paths:
        safe_path(rel)
        path = root / rel
        if any(p.is_symlink() for p in [path,*path.parents] if p != root.parent):
            raise ValueError("symlink in output path")
        if not path.is_file() or (not task.allow_empty and path.stat().st_size == 0):
            return {},False,"missing-or-empty-output"
        if path.stat().st_size > 1_000_000:
            return {},False,"output-too-large"
        artifacts[rel] = path.read_text(encoding="utf-8")
    return artifacts,True,None


def worker_command(name: str, network: str, gateway: str, token: str, work: Path, config: ExperimentConfig) -> list[str]:
    return ["docker","run","--rm","--name",name,"--network",network,"--read-only","--user","65532:65532","--cap-drop=ALL","--security-opt=no-new-privileges","--pids-limit=128","--memory",f"{config.max_memory_mb}m","--cpus=1","--tmpfs","/tmp:rw,nosuid,nodev,size=256m", "--mount",f"type=bind,src={work/'source'},dst=/source,readonly", "--mount",f"type=bind,src={work/'bundle'},dst=/bundle,readonly", "--mount",f"type=bind,src={work/'control'},dst=/control,readonly", "--mount",f"type=bind,src={work/'output'},dst=/output", "--mount",f"type=bind,src={work/'scratch'},dst=/workspace", "--env",f"HARNESS_GATEWAY_URL=http://{gateway}:8080/v1","--env",f"HARNESS_GATEWAY_TOKEN={token}", config.image,"python","-m","harness_clone.worker"]


class DockerExecutor:
    def __init__(self, store: RunStore, config: ExperimentConfig):
        self.store,self.config = store,config

    def execute(self, task: TaskSpec, bundle_digest: str, execution_id: str) -> ExecutionRecord:
        validate_task(task)
        record = ExecutionRecord(execution_id=execution_id,task_id=task.id,bundle_digest=bundle_digest)
        try:
            with tempfile.TemporaryDirectory(prefix="harness-run-") as temp:
                work=Path(temp)
                shutil.copytree(task.source_root,work/"source")
                shutil.copytree(self.store.bundle_path(bundle_digest),work/"bundle")
                for part in ["control","output","scratch"]:
                    (work/part).mkdir(mode=0o777)
                    (work/part).chmod(0o777)
                (work/"control/request.json").write_text(json.dumps({"task":task.model_dump(mode="json"),"config":self.config.model_dump(mode="json"),"execution_id":execution_id,"bundle_digest":bundle_digest}))
                (work/"control/config.json").write_text(self.config.model_dump_json())
                process=self._launch(work)
                if process.returncode:
                    record.status,record.error="failed","worker-exit"
                else:
                    terminal=work/"scratch/terminal.json"
                    if not terminal.is_file() or terminal.is_symlink():
                        raise ValueError("worker returned no terminal record")
                    result=json.loads(terminal.read_text())
                    record.trace=result.get("trace",[])
                    record.artifacts,record.delivered,record.error=collect_artifacts(work/"output",task)
        except subprocess.TimeoutExpired:
            record.status,record.error="failed","timeout"
        except Exception as exc:
            record.status,record.error="failed",type(exc).__name__
        self.store.save_execution(record)
        return record

    def _launch(self, work: Path):
        if self.config.max_usd <= 0 or not os.environ.get("FIREWORKS_API_KEY"):
            raise ValueError("live execution requires a key and nonzero spending cap")
        suffix=uuid.uuid4().hex[:12]
        network=f"hc-{suffix}"
        gateway=f"hc-gateway-{suffix}"
        worker=f"hc-worker-{suffix}"
        token=secrets.token_urlsafe(32)
        def docker(args,**kwargs):
            return subprocess.run(["docker",*args],capture_output=True,text=True,check=True,timeout=30,**kwargs)
        try:
            docker(["network","create","--internal",network])
            # The gateway has upstream egress; the executor has only the internal network.
            docker(["run","-d","--name",gateway,"--network","bridge","--read-only","--cap-drop=ALL","--security-opt=no-new-privileges","--env","FIREWORKS_API_KEY","--env",f"HARNESS_GATEWAY_TOKEN={token}","--mount",f"type=bind,src={self.store.root},dst=/ledger","--mount",f"type=bind,src={work/'control'},dst=/control,readonly", self.config.image,"python","-m","harness_clone.gateway"])
            docker(["network","connect",network,gateway])
            return subprocess.run(worker_command(worker,network,gateway,token,work,self.config),capture_output=True,text=True,timeout=self.config.timeout_seconds,check=False)
        finally:
            for name in [worker,gateway]:
                subprocess.run(["docker","rm","-f",name],capture_output=True,timeout=30)
            subprocess.run(["docker","network","rm",network],capture_output=True,timeout=30)
