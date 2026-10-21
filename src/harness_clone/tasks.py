"""Local tasks with explicit sources and output contracts."""
from pathlib import Path

from .bundles import safe_path
from .contracts import TaskSpec


def validate_task(task: TaskSpec) -> TaskSpec:
    for rel in task.output_paths:
        safe_path(rel)
    if len(set(task.output_paths)) != len(task.output_paths):
        raise ValueError("duplicate output paths")
    if not task.source_root.is_dir() or task.source_root.is_symlink():
        raise ValueError("task source_root must be a real directory")
    if any(p.is_symlink() for p in task.source_root.rglob("*")):
        raise ValueError("task sources cannot contain symlinks")
    return task


def load_task(path: Path) -> TaskSpec:
    task = TaskSpec.model_validate_json(path.read_text())
    root = task.source_root
    if not root.is_absolute():
        root = (path.parent / root).resolve()
    return validate_task(task.model_copy(update={"source_root":root}))
