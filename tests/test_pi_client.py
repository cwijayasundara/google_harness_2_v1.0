import httpx


def test_lost_admission_response_retries_same_identity():
    from harness_clone.pi_client import PiClient
    requests=[]
    def handle(request):
        import json
        body=json.loads(request.content)
        requests.append(body["request_id"])
        if len(requests)==1:
            raise httpx.ReadTimeout("lost response")
        return httpx.Response(200,json={"id":"job-1","status":"pending"})
    client=PiClient("http://pi",httpx.Client(transport=httpx.MockTransport(handle)))
    assert client.submit({"request_id":"stable"})["id"]=="job-1"
    assert requests==["stable","stable"]


def test_client_does_not_return_partial_or_failed_candidate():
    import pytest
    from harness_clone.pi_client import PiClient
    def handle(request):
        return httpx.Response(200,json={"id":"job","status":"failed","error":"invalid edit"})
    client=PiClient("http://pi",httpx.Client(transport=httpx.MockTransport(handle)))
    with pytest.raises(RuntimeError,match="failed"):
        client.wait("job",timeout=1)


def test_pi_candidate_export_is_a_validated_patch(tmp_path,monkeypatch):
    from pathlib import Path
    from harness_clone.pi_editor import PiEditor
    from harness_clone.storage import RunStore
    from harness_clone.contracts import ExperimentConfig,TaskSpec,ExecutionRecord
    from harness_clone.evidence import build_evidence
    from harness_clone.bundles import apply_patch
    store=RunStore(tmp_path/"runs")
    digest=store.publish_bundle(Path(__file__).parents[1]/"examples/base-harness")
    source=tmp_path/"source";source.mkdir()
    task=TaskSpec(id="t",domain="code",prompt="x",source_root=source,output_paths=["x.txt"])
    editor=PiEditor(task,ExperimentConfig(max_usd="1",editor_policy="pi"),store)
    monkeypatch.setenv("FIREWORKS_API_KEY","not-a-key")
    def edit(work,job):
        (work/"candidate/scripts/new.py").write_text('print("checked")\n')
        return {"rationale":"added check","evidence_refs":["base:trace:0"]}
    monkeypatch.setattr(editor,"_launch_edit",edit)
    evidence=build_evidence(task,[ExecutionRecord(execution_id="base",task_id="t",bundle_digest=digest,trace=[{"id":"0","content":"check missing"}])])
    proposal=editor.propose("INV",digest,evidence)
    assert proposal.patch.upserts=={"scripts/new.py":'print("checked")\n'}
    apply_patch(store.bundle_path(digest),proposal.patch,tmp_path/"adoptable")
    assert not (store.bundle_path(digest)/"scripts/new.py").exists()
