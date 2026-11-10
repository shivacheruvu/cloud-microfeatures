"""Day 36 (Databricks) job task: receives the job's dynamic value references, already resolved by Lakeflow Jobs, as
`name=value` arguments after the scratch folder, and writes them to <folder>/params.json (also printed). Stdlib only."""
import json
import os
import sys


def main(argv: list[str]) -> dict:
    folder, pairs = argv[0], argv[1:]
    values = dict(p.split("=", 1) for p in pairs)
    os.makedirs(folder, exist_ok=True)
    with open(os.path.join(folder, "params.json"), "w") as f:
        json.dump(values, f)
    print("DAY36_PARAMS " + json.dumps(values))
    return values


if __name__ == "__main__":
    main(sys.argv[1:])
