"""Print a CSV's columns and numbered data rows without interpreting policy."""

import csv
import json
import sys
from pathlib import Path


def inspect(path):
    with Path(path).open(newline="") as stream:
        reader = csv.DictReader(stream)
        return {
            "columns": reader.fieldnames,
            "rows": [{"row": i, "values": row} for i, row in enumerate(reader, 2)],
        }


if __name__ == "__main__":
    print(json.dumps(inspect(sys.argv[1]), indent=2))
