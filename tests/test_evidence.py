from harness_clone.contracts import ExecutionRecord, TaskSpec


def test_only_whitelisted_records_enter_evidence(tmp_path):
    from harness_clone.evidence import build_evidence
    (tmp_path/"rubrics.json").write_text('{"score":100}')
    task=TaskSpec(id="one",domain="code",prompt="make x",source_root=tmp_path,output_paths=["x.txt"])
    record=ExecutionRecord(execution_id="base",task_id="one",bundle_digest="0"*64,artifacts={"x.txt":"output","rubrics.json":"private"},trace=[{"id":"0","content":"ran script","score":100}])
    evidence=build_evidence(task,[record])
    assert evidence.records[0].artifacts=={"x.txt":"output"}
    assert "score" not in evidence.records[0].trace[0]
    assert "private" not in evidence.model_dump_json()


def test_edit_references_must_resolve_to_evidence(tmp_path):
    import pytest
    from harness_clone.evidence import build_evidence,validate_refs
    task=TaskSpec(id="one",domain="code",prompt="make x",source_root=tmp_path,output_paths=["x.txt"])
    evidence=build_evidence(task,[ExecutionRecord(execution_id="base",task_id="one",bundle_digest="0"*64,trace=[{"id":"0","content":"checked"}])])
    validate_refs(["base:trace:0"],evidence)
    with pytest.raises(ValueError):
        validate_refs(["base:trace:999"],evidence)
