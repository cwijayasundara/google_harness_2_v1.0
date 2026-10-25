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
from .contracts import PI_IMAGE, EditProposal, HarnessPatch
from .editors import DeepAgentEditor
from .evidence import citeable_refs, read_sources
from .gateway import stage_group
from .pi_client import DockerHTTPTransport, PiClient
from .storage import BudgetLedger, fingerprint


PI_OUTPUT_TOKENS = "16384"


def _clip(value, limit: int = 600) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    encoded = text.encode()
    if len(encoded) <= limit:
        return text
    return encoded[:limit].decode(errors="ignore") + " [excerpt]"


def editor_brief(task, evidence, parent_files: dict, scope: str, decisions, error: str) -> dict:
    """Evidence the editor can cite without rereading full rollouts into context."""
    records = []
    for record in evidence.records:
        indices = sorted(
            {
                *range(min(2, len(record.trace))),
                *range(max(0, len(record.trace) - 2), len(record.trace)),
            }
        )
        records.append(
            {
                "execution_id": record.execution_id,
                "delivered": record.delivered,
                "error": record.error,
                "artifacts": {
                    path: _clip(body, 400) for path, body in list(record.artifacts.items())[:4]
                },
                "trace": [
                    {
                        **{key: row[key] for key in ("id", "type") if key in row},
                        "content": _clip(row.get("content", ""), 400),
                    }
                    for row in (record.trace[i] for i in indices)
                ],
            }
        )
    refs = citeable_refs(evidence)
    if len(refs) > 80:
        refs = [ref for ref in refs if ":artifact:" in ref] + [
            ref for ref in refs if ":trace:" in ref
        ][:40]
    return {
        "scope": scope,
        "instructions": (
            "Edit files under /candidate only. This brief is the evidence; do not search "
            "other directories or node_modules. Write /candidate/proposal.json with rationale "
            "and evidence_refs copied exactly from valid_evidence_refs. KEEP_BASE is valid "
            "and then proposal.json is the only change. Make the smallest edit the evidence supports."
        ),
        "task": {
            "id": task.id,
            "domain": task.domain,
            "prompt": task.prompt,
            "output_paths": task.output_paths,
        },
        "sources": {name: _clip(body) for name, body in list(read_sources(task).items())[:8]},
        "candidate_paths": sorted(parent_files),
        "evidence_records": records,
        "valid_evidence_refs": refs,
        "decisions": decisions,
        "validation_error": error,
    }


def _docker_call(run, args):
    try:
        return run(args)
    except subprocess.CalledProcessError as exc:
        raise RuntimeError(f"docker {' '.join(args[:2])} failed: {exc.stderr or exc}") from exc


def _checked(run, args):
    result = _docker_call(run, args)
    if result.returncode:
        raise RuntimeError(
            f"docker {' '.join(args[:2])} failed: {(result.stderr or '').strip()[:300]}"
        )
    return result


def ensure_network(run, name: str) -> None:
    if _docker_call(run, ["network", "inspect", name]).returncode != 0:
        _checked(run, ["network", "create", "--internal", name])


def ensure_container(run, name: str, create_args: list[str]) -> bool:
    """Reuse a running container; recreate a stopped one. Returns True if reused."""
    inspect = _docker_call(run, ["inspect", "--format", "{{.State.Running}}", name])
    if inspect.returncode == 0:
        if (inspect.stdout or "").strip() == "true":
            return True
        _docker_call(run, ["rm", "-f", name])
    _checked(run, list(create_args))
    return False


class PiEditor(DeepAgentEditor):
    def propose(self, scope, parent_digest, evidence):
        if self.config.max_usd <= 0 or not os.environ.get("FIREWORKS_API_KEY"):
            raise ValueError("Pi editing requires credentials and a nonzero cap")
        request_id = fingerprint([self.stage, scope, parent_digest, evidence.digest])
        work = self.store.root / "pi" / request_id
        candidate = work / "candidate"
        for name in ["context", "data"]:
            (work / name).mkdir(parents=True, exist_ok=True)
            (work / name).chmod(0o777)
        if not candidate.exists():
            shutil.copytree(self.store.bundle_path(parent_digest), candidate)
            for path in [candidate, *candidate.rglob("*")]:
                path.chmod(0o777 if path.is_dir() else 0o666)
        job = {
            "request_id": request_id,
            "parent_digest": parent_digest,
            "evidence_digest": evidence.digest,
            "scope": scope,
            "allowed_paths": [
                "system.md",
                "guardrails.md",
                "config.json",
                "memory/*.md",
                "tools/descriptions.json",
                "subagents/*.json",
                "skills/*/SKILL.md",
                "scripts/*.py",
                "middleware/*.py",
            ],
            "budget_usd": str(self.config.stage_usd),
            "routing_policy": "pi-all-stages-v1",
        }
        context = editor_brief(
            self.task,
            evidence,
            read_files(self.store.bundle_path(parent_digest)),
            scope,
            self.decisions,
            self.error,
        )
        (work / "context/context.json").write_text(json.dumps(context))
        (work / "context/job.json").write_text(json.dumps(job))
        (work / "context/config.json").write_text(self.config.model_dump_json())
        result = self._launch_edit(work, job)
        (candidate / "proposal.json").unlink(missing_ok=True)
        before = read_files(self.store.bundle_path(parent_digest))
        after = read_files(candidate)
        patch = HarnessPatch(
            parent_digest=parent_digest,
            upserts={p: v for p, v in after.items() if before.get(p) != v},
            deletions=sorted(set(before) - set(after)),
        )
        return EditProposal(
            patch=patch,
            rationale=result["rationale"],
            evidence_refs=result["evidence_refs"],
            keep_base=not patch.upserts and not patch.deletions,
        )

    def _launch_edit(self, work: Path, job: dict):
        suffix = job["request_id"][:16]
        network, gateway, worker = f"hc-pi-net-{suffix}", f"hc-pi-gw-{suffix}", f"hc-pi-{suffix}"
        stage_id = self.stage or job["request_id"]
        # Persist scoped credentials before launching so a resume after
        # controller death can reconnect to resources that are still running.
        session = self.store.get("pi-session", job["request_id"]) or {}
        gateway_token = session.get("gateway_token") or secrets.token_urlsafe(32)
        worker_token = session.get("worker_token") or secrets.token_urlsafe(32)
        self.store.put(
            "pi-session",
            job["request_id"],
            {
                "gateway_token": gateway_token,
                "worker_token": worker_token,
                "network": network,
                "gateway": gateway,
                "worker": worker,
            },
        )

        def run(args):
            return subprocess.run(["docker", *args], capture_output=True, text=True, timeout=30)

        try:
            ensure_network(run, network)
            reused_gateway = ensure_container(
                run,
                gateway,
                [
                    "run",
                    "-d",
                    "--name",
                    gateway,
                    "--network",
                    "bridge",
                    "--read-only",
                    "--cap-drop=ALL",
                    "--env",
                    "FIREWORKS_API_KEY",
                    "--env",
                    f"HARNESS_GATEWAY_TOKEN={gateway_token}",
                    "--env",
                    f"HARNESS_STAGE_ID={stage_id}",
                    "--env",
                    f"HARNESS_MAX_OUTPUT_TOKENS={PI_OUTPUT_TOKENS}",
                    "--mount",
                    f"type=bind,src={self.store.root},dst=/ledger",
                    "--mount",
                    f"type=bind,src={work / 'context'},dst=/control,readonly",
                    self.config.image,
                    "python",
                    "-m",
                    "harness_clone.gateway",
                ],
            )
            connect = run(["network", "connect", network, gateway])
            if not reused_gateway and connect.returncode:
                raise RuntimeError("docker network connect failed for Pi gateway")
            ensure_container(
                run,
                worker,
                [
                    "run",
                    "-d",
                    "--name",
                    worker,
                    "--network",
                    network,
                    "--read-only",
                    "--user",
                    "65532:65532",
                    "--cap-drop=ALL",
                    "--security-opt=no-new-privileges",
                    "--pids-limit=128",
                    "--memory",
                    f"{self.config.max_memory_mb}m",
                    "--cpus=1",
                    "--tmpfs",
                    "/tmp:rw,nosuid,nodev,size=256m",
                    "--env",
                    f"HARNESS_GATEWAY_URL=http://{gateway}:8080/v1",
                    "--env",
                    f"HARNESS_GATEWAY_TOKEN={gateway_token}",
                    "--env",
                    f"HARNESS_WORKER_TOKEN={worker_token}",
                    "--mount",
                    f"type=bind,src={work / 'candidate'},dst=/candidate",
                    "--mount",
                    f"type=bind,src={work / 'context'},dst=/context,readonly",
                    "--mount",
                    f"type=bind,src={work / 'data'},dst=/data",
                    PI_IMAGE,
                ],
            )
            with httpx.Client(transport=DockerHTTPTransport(worker), timeout=5) as http:
                client = PiClient("http://pi", http, worker_token)
                deadline = time.monotonic() + min(30, self.config.timeout_seconds)
                while True:
                    try:
                        # Pi admission is idempotent by request_id: resubmitting
                        # after a lost response or controller death returns the
                        # same job instead of starting a second edit.
                        admitted = client.submit(job)
                        break
                    except httpx.TransportError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.2)
                self.store.put(
                    "pi-admission",
                    job["request_id"],
                    {"job_id": admitted["id"], "request_id": job["request_id"]},
                )
                return client.wait(admitted["id"], self.config.timeout_seconds)
        finally:
            for name in [worker, gateway]:
                subprocess.run(["docker", "rm", "-f", name], capture_output=True, timeout=30)
            subprocess.run(["docker", "network", "rm", network], capture_output=True, timeout=30)
            try:
                BudgetLedger(self.store.root / "budget.sqlite", self.config.max_usd).mark_unknown(
                    stage_group(stage_id)
                )
            except (OSError, ValueError):
                pass
