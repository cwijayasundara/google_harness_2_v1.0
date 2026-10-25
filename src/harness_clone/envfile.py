"""Load credential variables from a local .env file without overriding the shell."""

import os
from pathlib import Path

# Only these names are copied into the process. Other .env entries stay unread
# so a local file cannot change interpreter, proxy, or harness settings.
CREDENTIAL_KEYS = frozenset({"FIREWORKS_API_KEY"})


def load_env_file(path: Path | None = None) -> list[str]:
    """Fill unset credential variables from ``path`` (default: ``./.env``).

    Existing nonempty environment values win. Quoted values are unwrapped.
    A missing file is ignored. Returns the names that were set.
    """
    path = Path.cwd() / ".env" if path is None else Path(path)
    if not path.is_file():
        return []
    loaded = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip().removeprefix("\ufeff")
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line[len("export ") :].strip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        if key not in CREDENTIAL_KEYS or os.environ.get(key):
            continue
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        if not value:
            continue
        os.environ[key] = value
        loaded.append(key)
    return loaded
