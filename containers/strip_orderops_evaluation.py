"""Remove host-only answer generation/scoring from executor image trees."""

import sys
from pathlib import Path


def strip_evaluation(root):
    for package in Path(root).rglob("harness_clone/orderops"):
        for module in ("cases", "score"):
            (package / f"{module}.py").unlink(missing_ok=True)
            (package / f"{module}.pyc").unlink(missing_ok=True)
            for bytecode in (package / "__pycache__").glob(f"{module}.*.pyc"):
                bytecode.unlink()


if __name__ == "__main__":
    for root in sys.argv[1:]:
        strip_evaluation(root)
