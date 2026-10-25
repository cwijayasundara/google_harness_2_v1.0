import json
import os

from harness_clone.envfile import load_env_file


def test_env_file_fills_only_the_fireworks_key(tmp_path, monkeypatch):
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    monkeypatch.setenv("FIREWORKS_API_KEY", "from-shell")
    env = tmp_path / ".env"
    env.write_text(
        "\n".join(
            [
                "# comment",
                "export FIREWORKS_API_KEY='fw-secret'",
                "OTHER_SECRET=do-not-load",
                "",
            ]
        )
    )
    assert load_env_file(env) == []
    assert os.environ["FIREWORKS_API_KEY"] == "from-shell"
    monkeypatch.delenv("FIREWORKS_API_KEY")
    assert load_env_file(env) == ["FIREWORKS_API_KEY"]
    assert os.environ["FIREWORKS_API_KEY"] == "fw-secret"
    assert "OTHER_SECRET" not in os.environ


def test_missing_env_file_is_ignored(tmp_path):
    assert load_env_file(tmp_path / ".env") == []


def test_doctor_reads_fireworks_key_from_env_file_without_printing_it(tmp_path, monkeypatch, capsys):
    from harness_clone.cli import main

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("FIREWORKS_API_KEY", raising=False)
    (tmp_path / ".env").write_text("FIREWORKS_API_KEY=fw-secret-value\n")
    assert main(["doctor"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["fireworks_key_configured"] is True
    assert "fw-secret-value" not in json.dumps(report)
