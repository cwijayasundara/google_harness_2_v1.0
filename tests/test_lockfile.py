"""uv.lock must cover every declared dependency so ``uv sync --frozen`` works."""

import re
import tomllib
from pathlib import Path

ROOT = Path(__file__).parents[1]


def _name(requirement: str) -> str:
    return re.split(r"[<>=!~\[; ]", requirement, maxsplit=1)[0].strip().lower().replace("_", "-")


def test_lockfile_covers_every_declared_dependency():
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    locked = {pkg["name"].lower() for pkg in lock["package"]}
    root = next(pkg for pkg in lock["package"] if pkg["name"] == project["name"])
    root_deps = {dep["name"].lower() for dep in root.get("dependencies", [])}
    for requirement in project["dependencies"]:
        name = _name(requirement)
        assert name in locked, f"{name} missing from uv.lock; run `uv lock`"
        assert name in root_deps, f"{name} not a locked dependency of {project['name']}"
