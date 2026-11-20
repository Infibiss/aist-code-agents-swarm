"""Pick an easy tier from SWE-style rows: SWE-bench Lite-like criteria on the gold patch, plus a light environment.

    python scripts/select_easy.py tasks.jsonl env.json --exclude var/tasks/rebench83.jsonl > easy_ids.txt
    aist-swarm import tasks.jsonl rebench-easy --env-json env.json --ids-file easy_ids.txt

A small gold patch is a proxy for difficulty, not a measure of it; eligibility still comes from the controls.
"""

import argparse
import json
import re
import sys
from collections import Counter

from aist_swarm.tasks import _id_list

DOCS = (".md", ".rst", ".txt")


def patch_stats(patch: str) -> tuple[list[str], int, int]:
    files = re.findall(r"^diff --git a/(\S+)", patch, re.M)
    hunks = len(re.findall(r"^@@", patch, re.M))
    changed = sum(1 for line in patch.splitlines()
                  if line[:1] in "+-" and not line.startswith(("+++", "---")))
    return files, hunks, changed


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("tasks")
    ap.add_argument("env")
    ap.add_argument("--exclude", action="append", default=[], help="task sets whose ids to skip")
    ap.add_argument("--max-hunks", type=int, default=2)
    ap.add_argument("--max-lines", type=int, default=12, help="added plus removed lines in the gold patch")
    ap.add_argument("--max-f2p", type=int, default=5)
    ap.add_argument("--max-reqs", type=int, default=60, help="pinned requirements in the environment")
    a = ap.parse_args()

    skip = {json.loads(line)["instance_id"] for path in a.exclude for line in open(path, encoding="utf-8")}
    env = json.load(open(a.env, encoding="utf-8"))
    why = Counter()
    for line in open(a.tasks, encoding="utf-8"):
        r = json.loads(line)
        e = env.get(r["instance_id"])
        files, hunks, changed = patch_stats(r["patch"])
        f2p = _id_list(r["FAIL_TO_PASS"])
        py = tuple(int(x) for x in (e or {}).get("python", "0.0").split(".")[:2])
        reason = ("excluded" if r["instance_id"] in skip else
                  "no env" if not e else
                  "not one code file" if len(files) != 1 or files[0].endswith(DOCS) else
                  "patch too big" if hunks > a.max_hunks or changed > a.max_lines else
                  "f2p count" if not 1 <= len(f2p) <= a.max_f2p else
                  "statement length" if not 150 <= len(r["problem_statement"]) <= 4000 else
                  "heavy env" if len(e.get("requirements", [])) > a.max_reqs else
                  "python < 3.8" if py < (3, 8) else "")
        why[reason or "kept"] += 1
        if not reason:
            print(r["instance_id"])
    print(dict(why), file=sys.stderr)


if __name__ == "__main__":
    main()
