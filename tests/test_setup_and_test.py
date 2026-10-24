"""The entry point must reject unsafe paid options before installing anything."""

import subprocess
from pathlib import Path

import pytest


SCRIPT = Path(__file__).parents[1] / "scripts/setup-and-test.sh"


@pytest.mark.parametrize(
    "arguments",
    [
        ["--live"],
        ["--live", "--max-usd", "0"],
        ["--live", "--max-usd", "-1"],
        ["--live", "--max-usd", "NaN"],
        ["--live", "--max-usd", "Infinity"],
        ["--max-usd", "2"],
        ["--max-usd"],
        ["--unknown"],
    ],
)
def test_invalid_options_fail_before_setup(arguments, tmp_path):
    result = subprocess.run(
        ["bash", str(SCRIPT), *arguments], cwd=tmp_path, capture_output=True, text=True
    )
    assert result.returncode == 2
    assert "error:" in result.stderr
    assert "Installing" not in result.stdout


def test_help_works_outside_repository():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"], cwd="/tmp", capture_output=True, text=True
    )
    assert result.returncode == 0
    assert "--live --max-usd" in result.stdout
