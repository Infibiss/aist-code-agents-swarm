"""Run an experiment: every (task, attempt) gets a fresh sandbox, a metered budget, the system, then the judge.

Results land in `<runs>/<experiment>/<task>/a<i>/` (result.json, trajectory, calls.jsonl from the proxy) and one
line per attempt in `<runs>/<experiment>/results.jsonl`.
"""

from __future__ import annotations

import json
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import httpx
import yaml
from pydantic import BaseModel, Field

from aist_swarm import judge, paths, systems
from aist_swarm.sandbox import Attempt
from aist_swarm.tasks import Task


class Experiment(BaseModel):
    name: str
    tasks: str
    ids: list[str] = Field(default_factory=list)
    limit: int = 0
    eligible_only: bool = True
    model: str
    system: dict
    budget: dict = Field(default_factory=dict)
    attempts: int = 1
    workers: int = 1
    proxy: str = "http://127.0.0.1:8090"
    hardware: dict = Field(default_factory=dict)

    @classmethod
    def load(cls, path: Path) -> Experiment:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def run_one(exp: Experiment, task: Task, i: int) -> dict:
    run_key = f"{exp.name}~{task.instance_id}~a{i}"
    out = paths.RUNS / exp.name / task.instance_id / f"a{i}"
    out.mkdir(parents=True, exist_ok=True)
    if exp.budget:
        httpx.post(f"{exp.proxy}/budget/{run_key}", json=exp.budget, timeout=10)
    system = systems.make(exp.system)
    t0 = time.time()
    with Attempt(task) as attempt:
        ctx = systems.Context(task=task, attempt=attempt, run_key=run_key, out_dir=out, proxy=exp.proxy,
                              model=exp.model)
        outcome = system.solve(ctx)
    solve_s = time.time() - t0
    verdict = judge.evaluate(task, outcome.patch)
    usage = httpx.get(f"{exp.proxy}/usage/{run_key}", timeout=10).json()
    result = {"experiment": exp.name, "task": task.instance_id, "attempt": i, "system": system.name,
              "model": exp.model, "resolved": verdict.resolved, "applied": verdict.applied,
              "exit_status": outcome.exit_status, "solve_seconds": round(solve_s, 1),
              "patch_lines": outcome.patch.count("\n"), "usage": usage, "extra": outcome.extra}
    (out / "result.json").write_text(json.dumps({**result, "verdict": verdict.model_dump()}, indent=1),
                                     encoding="utf-8")
    (out / "patch.diff").write_text(outcome.patch, encoding="utf-8")
    (out / "workspace.diff").write_text(outcome.workspace_diff, encoding="utf-8")
    return result


def run(exp: Experiment, items: list[Task], on_result=print) -> list[dict]:
    root = paths.RUNS / exp.name
    root.mkdir(parents=True, exist_ok=True)
    (root / "experiment.yaml").write_text(yaml.safe_dump(exp.model_dump(), allow_unicode=True), encoding="utf-8")
    jobs = [(t, i) for i in range(exp.attempts) for t in items]
    done = set()
    results_path = root / "results.jsonl"
    if results_path.exists():
        for line in results_path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            done.add((r["task"], r["attempt"]))
    jobs = [(t, i) for t, i in jobs if (t.instance_id, i) not in done]
    results = []

    def one(job):
        t, i = job
        try:
            r = run_one(exp, t, i)
        except Exception as e:
            r = {"experiment": exp.name, "task": t.instance_id, "attempt": i, "resolved": False,
                 "exit_status": f"harness error: {type(e).__name__}: {e}"[:300]}
        with open(results_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(r, ensure_ascii=False) + "\n")
        on_result(r)
        return r

    with ThreadPoolExecutor(exp.workers) as pool:
        results = list(pool.map(one, jobs))
    return results
