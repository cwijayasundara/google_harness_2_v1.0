"""One durable Pi editing job per isolated container and private workspace."""
import json
import os
import secrets
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from .bundles import read_files
from .contracts import EditProposal, HarnessPatch
from .editors import DeepAgentEditor
from .evidence import read_sources
from .pi_client import PiClient
from .storage import fingerprint


class PiEditor(DeepAgentEditor):
    def propose(self,scope,parent_digest,evidence):
        if self.config.max_usd<=0 or not os.environ.get("FIREWORKS_API_KEY"):
            raise ValueError("Pi editing requires credentials and a nonzero cap")
        request_id=fingerprint([self.stage,scope,parent_digest,evidence.digest])
        work=self.store.root/"pi"/request_id
        candidate=work/"candidate"
        for name in ["context","data"]:
            (work/name).mkdir(parents=True,exist_ok=True)
            (work/name).chmod(0o777)
        if not candidate.exists():
            shutil.copytree(self.store.bundle_path(parent_digest),candidate)
            for path in [candidate,*candidate.rglob("*")]:
                path.chmod(0o777 if path.is_dir() else 0o666)
        job={"request_id":request_id,"parent_digest":parent_digest,"evidence_digest":evidence.digest,"scope":scope,"allowed_paths":["system.md","guardrails.md","config.json","memory/*.md","tools/descriptions.json","subagents/*.json","skills/*/SKILL.md","scripts/*.py","middleware/*.py"],"budget_usd":str(self.config.stage_usd),"routing_policy":"pi-all-stages-v1"}
        context={"task":self.task.model_dump(mode="json"),"sources":read_sources(self.task),"scope":scope,"evidence":evidence.model_dump(mode="json"),"decisions":self.decisions,"validation_error":self.error,"parent":read_files(self.store.bundle_path(parent_digest)),"candidate_harnesses":{digest:read_files(self.store.bundle_path(digest)) for digest in {r.bundle_digest for r in evidence.records}},"library_harnesses":{item["bundle_digest"]:read_files(self.store.bundle_path(item["bundle_digest"])) for item in evidence.library},"instructions":"INV improves general execution procedure; DOM improves practitioner knowledge; COMPOSE selectively merges/revises edits justified by execution contrasts. KEEP_BASE is valid. Do not change the task or bake its facts into reusable rules."}
        (work/"context/context.json").write_text(json.dumps(context))
        (work/"context/job.json").write_text(json.dumps(job))
        (work/"context/config.json").write_text(self.config.model_dump_json())
        result=self._launch_edit(work,job)
        (candidate/"proposal.json").unlink(missing_ok=True)
        before=read_files(self.store.bundle_path(parent_digest))
        after=read_files(candidate)
        patch=HarnessPatch(parent_digest=parent_digest,upserts={p:v for p,v in after.items() if before.get(p)!=v},deletions=sorted(set(before)-set(after)))
        return EditProposal(patch=patch,rationale=result["rationale"],evidence_refs=result["evidence_refs"],keep_base=not patch.upserts and not patch.deletions)

    def _launch_edit(self,work: Path,job: dict):
        suffix=job["request_id"][:16]
        network,gateway,worker=f"hc-pi-net-{suffix}",f"hc-pi-gw-{suffix}",f"hc-pi-{suffix}"
        gateway_token,worker_token=secrets.token_urlsafe(32),secrets.token_urlsafe(32)
        def docker(args):
            return subprocess.run(["docker",*args],capture_output=True,text=True,timeout=30,check=True)
        try:
            docker(["network","create","--internal",network])
            docker(["run","-d","--name",gateway,"--network","bridge","--read-only","--cap-drop=ALL","--env","FIREWORKS_API_KEY","--env",f"HARNESS_GATEWAY_TOKEN={gateway_token}","--mount",f"type=bind,src={self.store.root},dst=/ledger","--mount",f"type=bind,src={work/'context'},dst=/control,readonly",self.config.image,"python","-m","harness_clone.gateway"])
            docker(["network","connect",network,gateway])
            docker(["run","-d","--name",worker,"--network",network,"--read-only","--user","65532:65532","--cap-drop=ALL","--security-opt=no-new-privileges","--pids-limit=128","--memory",f"{self.config.max_memory_mb}m","--cpus=1","--tmpfs","/tmp:rw,nosuid,nodev,size=256m","--publish","127.0.0.1::8081","--env",f"HARNESS_GATEWAY_URL=http://{gateway}:8080/v1","--env",f"HARNESS_GATEWAY_TOKEN={gateway_token}","--env",f"HARNESS_WORKER_TOKEN={worker_token}","--mount",f"type=bind,src={work/'candidate'},dst=/candidate","--mount",f"type=bind,src={work/'context'},dst=/context,readonly","--mount",f"type=bind,src={work/'data'},dst=/data","harness-clone-pi:0.1"])
            address=docker(["port",worker,"8081/tcp"]).stdout.strip()
            with httpx.Client(timeout=5) as http:
                client=PiClient(f"http://{address}",http,worker_token)
                deadline=time.monotonic()+min(30,self.config.timeout_seconds)
                while True:
                    try:
                        admitted=client.submit(job)
                        break
                    except httpx.TransportError:
                        if time.monotonic()>=deadline:
                            raise
                        time.sleep(0.2)
                self.store.put("pi-admission",job["request_id"],{"job_id":admitted["id"],"request_id":job["request_id"]})
                return client.wait(admitted["id"],self.config.timeout_seconds)
        finally:
            for name in [worker,gateway]:
                subprocess.run(["docker","rm","-f",name],capture_output=True,timeout=30)
            subprocess.run(["docker","network","rm",network],capture_output=True,timeout=30)
