import json


def test_demo_runs_offline_and_resumes(tmp_path, capsys, monkeypatch):
    from harness_clone.cli import main
    import httpx

    def deny(*a, **kw):
        raise AssertionError("offline demo made network request")

    monkeypatch.setattr(httpx.Client, "send", deny)
    root = tmp_path / "run with spaces"
    assert main(["demo", "--run-dir", str(root), "--k", "2"]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["backend"] == "deterministic-demo"
    assert result["executions"] == 16
    assert result["editing_stages"] == 12
    assert main(["resume", "--run-dir", str(root)]) == 0
    again = json.loads(capsys.readouterr().out)
    assert again["executions"] == 16


def test_live_run_requires_spending_cap(tmp_path, capsys):
    from harness_clone.cli import main

    assert (
        main(["run", "--task", str(tmp_path / "task.json"), "--run-dir", str(tmp_path / "run")])
        == 2
    )
    assert "spending cap" in capsys.readouterr().err


def test_inspect_and_evaluate_do_not_modify_online_evidence(tmp_path, capsys):
    from harness_clone.cli import main

    root = tmp_path / "run"
    assert main(["demo", "--run-dir", str(root)]) == 0
    capsys.readouterr()
    assert main(["inspect", "--run-dir", str(root)]) == 0
    inspected = json.loads(capsys.readouterr().out)
    assert inspected["tasks"] == 2
    expectations = tmp_path / "private-expectations.json"
    expectations.write_text(
        json.dumps(
            {
                "demo-a": {"answer.json": {"count": 3, "total": 10}},
                "demo-b": {"answer.json": {"count": 3, "total": 18}},
            }
        )
    )
    output = tmp_path / "offline-results.json"
    assert (
        main(
            [
                "evaluate",
                "--run-dir",
                str(root),
                "--expectations",
                str(expectations),
                "--output",
                str(output),
            ]
        )
        == 0
    )
    evaluation = json.loads(output.read_text())
    assert evaluation["passed"] == 2 and evaluation["coverage"] == 1.0
    from harness_clone.storage import RunStore

    assert RunStore(root).get("evaluation", "results") is None


def test_doctor_marks_live_checks_unverified(capsys):
    from harness_clone.cli import main

    assert main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["live_verified"] is False
