"""Command line: tasks, environments, controls, proxy, runs."""

from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import typer

from aist_swarm import paths, tasks as T

app = typer.Typer(no_args_is_help=True, add_completion=False)


def taskset_path(name: str) -> Path:
    p = Path(name)
    return p if p.suffix == ".jsonl" else paths.TASKS / f"{name}.jsonl"


def pick(taskset: str, ids: str | None, eligible_only: bool = False) -> list[T.Task]:
    items = T.load(taskset_path(taskset))
    if ids:
        wanted = set(ids.split(","))
        items = [t for t in items if t.instance_id in wanted]
    if eligible_only:
        ok = set(json.loads(controls_path(taskset).read_text())["eligible"])
        items = [t for t in items if t.instance_id in ok]
    return items


def controls_path(taskset: str) -> Path:
    return taskset_path(taskset).with_suffix(".controls.json")


@app.command("import")
def import_tasks(jsonl: Path, name: str, env_json: Path = typer.Option(None), ids_file: Path = typer.Option(None),
                 limit: int = 0):
    """Import SWE-bench style rows (FAIL_TO_PASS etc.) into a named task set."""
    ids = set(ids_file.read_text().split()) if ids_file else None
    items = T.load_swe_jsonl(jsonl, env_json, ids)
    if limit:
        items = items[:limit]
    out = taskset_path(name)
    T.save(items, out)
    typer.echo(f"{len(items)} tasks -> {out}")


@app.command()
def build(taskset: str, ids: str = typer.Option(None), jobs: int = 2, force: bool = False):
    """Build task environments (Docker volumes). Needs network."""
    from aist_swarm import sandbox

    items = pick(taskset, ids)

    def one(t: T.Task):
        ok = sandbox.build_env(t, log=paths.ENVS / "logs" / f"{t.instance_id}.log", force=force)
        typer.echo(f"{'ok  ' if ok else 'FAIL'} {t.instance_id}")
        return t.instance_id, ok

    with ThreadPoolExecutor(jobs) as pool:
        results = dict(pool.map(one, items))
    typer.echo(f"built {sum(results.values())}/{len(results)}")


@app.command()
def controls(taskset: str, ids: str = typer.Option(None), jobs: int = 2):
    """Gold patch must pass and empty patch must fail; tasks where both hold are eligible."""
    from aist_swarm import judge, sandbox

    items = [t for t in pick(taskset, ids) if sandbox.env_ready(t)]

    def one(t: T.Task):
        gold = judge.evaluate(t, t.patch)
        empty = judge.evaluate(t, "")
        ok = gold.resolved and not empty.resolved
        typer.echo(f"{'ok  ' if ok else 'drop'} {t.instance_id}  gold={gold.resolved} empty={empty.resolved} "
                   f"({gold.seconds:.0f}s)")
        return t.instance_id, {"gold": gold.model_dump(), "empty": empty.model_dump(), "eligible": ok}

    with ThreadPoolExecutor(jobs) as pool:
        results = dict(pool.map(one, items))
    path = controls_path(taskset)
    old = json.loads(path.read_text()) if path.exists() else {"tasks": {}}
    old["tasks"].update(results)
    old["eligible"] = sorted(k for k, v in old["tasks"].items() if v["eligible"])
    path.write_text(json.dumps(old, indent=1), encoding="utf-8")
    typer.echo(f"eligible {len(old['eligible'])}/{len(old['tasks'])} -> {path}")


@app.command()
def proxy(upstream: list[str] = typer.Option(..., help="name=url or url (fallback)"), port: int = 8090,
          host: str = "127.0.0.1"):
    """Run the metering proxy in front of model servers."""
    from aist_swarm.proxy import serve

    ups = dict(u.split("=", 1) if "=" in u else ("*", u) for u in upstream)
    ups.setdefault("*", next(iter(ups.values())))
    typer.echo(f"proxy on http://{host}:{port}  upstreams={ups}")
    serve(ups, host, port)


@app.command("fake-llm")
def fake_llm(taskset: str, port: int = 8099):
    """Scripted oracle model for end-to-end tests without a GPU."""
    from aist_swarm.fake_llm import serve

    serve(T.load(taskset_path(taskset)), port)


@app.command()
def run(config: Path, limit: int = 0):
    """Run an experiment from a YAML config."""
    from aist_swarm import runner

    exp = runner.Experiment.load(config)
    items = pick(exp.tasks, ",".join(exp.ids) if exp.ids else None, eligible_only=exp.eligible_only)
    n = limit or exp.limit
    if n:
        items = items[:n]
    typer.echo(f"{exp.name}: {len(items)} tasks x {exp.attempts} attempts, model={exp.model}")

    def show(r):
        mark = "PASS" if r.get("resolved") else "fail"
        u = r.get("usage", {})
        typer.echo(f"{mark} {r['task']} a{r['attempt']} {r.get('exit_status', '')} "
                   f"{r.get('solve_seconds', '?')}s tokens={u.get('tokens', '?')} calls={u.get('requests', '?')}")

    results = runner.run(exp, items, on_result=show)
    ok = sum(1 for r in results if r.get("resolved"))
    typer.echo(f"resolved {ok}/{len(results)}")


if __name__ == "__main__":
    app()
