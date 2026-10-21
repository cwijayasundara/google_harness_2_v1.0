from pathlib import Path

for path in sorted(Path('/output').rglob('*')):
    if path.is_file():
        print(path.name, path.stat().st_size)
