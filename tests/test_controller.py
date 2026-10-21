from pathlib import Path

import pytest

from harness_clone.contracts import EditProposal, ExecutionRecord, ExperimentConfig, HarnessPatch, TaskSpec


class FixtureExecutor:
    def __init__(self):
        self.calls=[]

    def execute(self,task,bundle_digest,execution_id):
        self.calls.append((task.id,bundle_digest,execution_id))
        return ExecutionRecord(execution_id=execution_id,task_id=task.id,bundle_digest=bundle_digest,delivered=True,artifacts={"answer.txt":"verified output"},trace=[{"id":"0","type":"tool","content":"checked source"}])


class FixtureEditor:
    def __init__(self):
        self.calls=[]
        self.parents=[]

    def propose(self,scope,parent_digest,evidence):
        self.calls.append(scope)
        self.parents.append(parent_digest)
        return EditProposal(patch=HarnessPatch(parent_digest=parent_digest,upserts={"memory/lesson.md":f"Check source evidence before completing {len(self.calls)}"}),rationale="Source was inspected",evidence_refs=[evidence.records[0].execution_id+":trace:0"])

    def choose(self,task,records):
        return records[0].execution_id,"first valid output"


def run(tmp_path,config=None,editor=None):
    from harness_clone.controller import run_task
    from harness_clone.storage import RunStore
    config=config or ExperimentConfig()
    store=RunStore(tmp_path/"runs")
    digest=store.publish_bundle(Path(__file__).parents[1]/"examples/base-harness")
    source=tmp_path/"source"
    source.mkdir(exist_ok=True)
    task=TaskSpec(id="one",domain="code",prompt="Produce answer",source_root=source,output_paths=["answer.txt"])
    executor=FixtureExecutor()
    result=run_task(task,config,store,executor,editor or FixtureEditor(),digest)
    return result,executor,store,task,digest


@pytest.mark.parametrize("mode,k",[("parallel",1),("parallel",2),("sequential",1),("sequential",2)])
def test_exact_schedule_and_selection_pool(tmp_path,mode,k):
    result,executor,store,task,digest=run(tmp_path,ExperimentConfig(mode=mode,k=k))
    assert len(executor.calls)==4*k
    assert len(result.stages)==3*k
    eligible=result.selection.eligible
    assert len(eligible)==k
    assert all(("/agg" in label if mode=="parallel" else "/c" in label) for label in eligible)
    assert result.selection.chosen_execution_id in eligible
    assert not result.errors


def test_sequential_parent_is_previous_checkpoint(tmp_path):
    editor=FixtureEditor()
    result,executor,store,task,digest=run(tmp_path,ExperimentConfig(mode="sequential",k=2),editor)
    c1=result.executions["c1"].bundle_digest
    assert c1 != digest
    assert editor.parents[3:5] == [c1,c1]


def test_parallel_candidates_share_base_parent(tmp_path):
    editor=FixtureEditor()
    _,_,_,_,digest=run(tmp_path,ExperimentConfig(k=2),editor)
    assert editor.parents[:4] == [digest]*4


def test_invalid_proposals_repair_once_then_fallback(tmp_path):
    class Broken(FixtureEditor):
        def propose(self,scope,parent_digest,evidence):
            self.calls.append(scope)
            return EditProposal(patch=HarnessPatch(parent_digest=parent_digest,upserts={"system.md":"missing skill trigger"}),rationale="bad",evidence_refs=[])
    editor=Broken()
    result,*_=run(tmp_path,editor=editor)
    assert len(editor.calls)==6
    assert all(s["status"]=="fallback" for s in result.stages)


def test_completed_stages_not_reexecuted(tmp_path):
    from harness_clone.controller import run_task
    editor=FixtureEditor()
    result,executor,store,task,digest=run(tmp_path,editor=editor)
    again=run_task(task,ExperimentConfig(),store,executor,editor,digest)
    assert len(executor.calls)==4
    assert len(editor.calls)==3
    assert again==result


def test_practical_mode_can_retain_baseline(tmp_path):
    result,*_=run(tmp_path,ExperimentConfig(profile="practical"))
    assert "/base" in result.selection.chosen_execution_id
