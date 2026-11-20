"""HTTP API for the viewer, plus the built front end from web/dist."""

from __future__ import annotations

from pathlib import Path

from aiohttp import web

from aist_swarm import paths, traces
from aist_swarm import tasks as T

DIST = Path(__file__).resolve().parents[2] / "web" / "dist"


def _task_meta(taskset: str | None) -> dict:
    p = paths.TASKS / f"{taskset}.jsonl" if taskset else None
    if not p or not p.exists():
        return {}
    return {t.instance_id: {"repo": t.repo, "created_at": t.created_at, "patch_lines": t.patch.count("\n"),
                            "files": len(t.patch_files())} for t in T.load(p)}


async def list_experiments(_):
    return web.json_response(traces.experiments())


async def get_experiment(request):
    data = traces.experiment(request.match_info["name"])
    data["tasks"] = _task_meta(data["config"].get("tasks"))
    return web.json_response(data)


async def get_run(request):
    m = request.match_info
    return web.json_response(traces.run(m["name"], m["task"], m["attempt"]))


async def index(_):
    page = DIST / "index.html"
    if not page.exists():
        return web.Response(text="front end not built: cd web && npm run build", status=503)
    return web.FileResponse(page)


def make_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/experiments", list_experiments)
    app.router.add_get("/api/experiments/{name}", get_experiment)
    app.router.add_get("/api/runs/{name}/{task}/{attempt}", get_run)
    if (DIST / "assets").exists():
        app.router.add_static("/assets", DIST / "assets")
    app.router.add_get("/", index)
    return app


def serve(host: str = "127.0.0.1", port: int = 8700) -> None:
    web.run_app(make_app(), host=host, port=port, print=None)
