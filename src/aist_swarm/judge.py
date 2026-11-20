"""The judge: apply a patch and the hidden test patch in a fresh attempt, run the deciding tests.

With FAIL_TO_PASS / PASS_TO_PASS ids (SWE-bench style) every F2P test must pass and no P2P test may break.
Without ids, the test files touched by the test patch must pass as a whole (Kaggle Gemma style).
Tests the agent wrote never count: its patch is applied first and the hidden test patch overwrites test files.
"""

from __future__ import annotations

import re
import shlex
import time

from pydantic import BaseModel, Field

from aist_swarm.sandbox import Attempt
from aist_swarm.tasks import Task

LINE = re.compile(r"^(PASSED|FAILED|ERROR|SKIPPED|XFAIL|XPASS) (.+?)(?: - .*)?$")
F2P_OK = {"PASSED", "XPASS"}
P2P_OK = {"PASSED", "XPASS", "XFAIL", "SKIPPED"}


class Verdict(BaseModel):
    resolved: bool
    applied: bool = True
    f2p_failed: list[str] = Field(default_factory=list)
    p2p_failed: list[str] = Field(default_factory=list)
    returncode: int | None = None
    seconds: float = 0.0
    log_tail: str = ""


ANSI = re.compile(r"\x1b\[[0-9;]*m")


def parse_report(output: str) -> dict[str, str]:
    status: dict[str, str] = {}
    for line in ANSI.sub("", output).splitlines():
        m = LINE.match(line.strip())
        if m:
            status.setdefault(m.group(2).strip(), m.group(1))
    return status


def lookup(status: dict[str, str], test_id: str, ok: set[str]) -> bool:
    """Exact id, or (for ids the dataset cut at the first space inside a parameter) every id with that prefix."""
    if test_id in status:
        return status[test_id] in ok
    if "[" in test_id and not test_id.endswith("]"):
        hits = [s for k, s in status.items() if k.startswith(test_id)]
        return bool(hits) and all(s in ok for s in hits)
    return False


def evaluate(task: Task, patch: str, timeout: float = 900) -> Verdict:
    t0 = time.time()
    with Attempt(task) as a:
        if not a.apply(patch).returncode == 0:
            return Verdict(resolved=False, applied=False, seconds=time.time() - t0)
        test_apply = a.apply(task.test_patch)
        if test_apply.returncode != 0:
            return Verdict(resolved=False, applied=False, seconds=time.time() - t0, log_tail=test_apply.output[-2000:])
        ids = task.fail_to_pass + task.pass_to_pass
        files = sorted({i.split("::")[0] for i in ids}) if ids else task.test_files()
        cmd = "python -m pytest -rA -p no:cacheprovider --tb=short --color=no " + " ".join(
            shlex.quote(f) for f in files)
        run = a.exec(cmd, timeout=timeout, limit=None)
    seconds = time.time() - t0
    tail = run.output[-3000:]
    if not ids:
        return Verdict(resolved=run.returncode == 0, returncode=run.returncode, seconds=seconds, log_tail=tail)
    status = parse_report(run.output)
    f2p_failed = [i for i in task.fail_to_pass if not lookup(status, i, F2P_OK)]
    p2p_failed = [i for i in task.pass_to_pass if not lookup(status, i, P2P_OK)]
    return Verdict(resolved=not f2p_failed and not p2p_failed, f2p_failed=f2p_failed, p2p_failed=p2p_failed,
                   returncode=run.returncode, seconds=seconds, log_tail=tail)
