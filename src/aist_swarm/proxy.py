"""Metering proxy in front of OpenAI-compatible model servers.

Every system, whatever framework it is built on, talks to models only through this proxy, at
`http://host:port/r/<run>/<role>/v1`. The proxy forwards the call, logs it (role, tokens, latency, full messages)
to `<runs>/<run>/calls.jsonl`, and refuses calls once the run's budget is spent. That makes budgets comparable
across a single agent, a swarm, or a black-box agent we cannot instrument.

Budgets: POST /budget/<run> {"tokens": int, "requests": int, "seconds": float}. Usage: GET /usage/<run>.
Upstreams: model name -> base URL (`--upstream qwen-0.5b=http://127.0.0.1:8080/v1`); `*` is the fallback.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path

from aiohttp import ClientSession, ClientTimeout, web

from aist_swarm import paths


@dataclass
class Budget:
    tokens: int | None = None
    requests: int | None = None
    seconds: float | None = None


@dataclass
class Usage:
    requests: int = 0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    seconds: float = 0.0
    refused: int = 0
    by_role: dict = field(default_factory=lambda: defaultdict(lambda: defaultdict(float)))

    @property
    def tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens

    def as_dict(self) -> dict:
        return {"requests": self.requests, "prompt_tokens": self.prompt_tokens,
                "completion_tokens": self.completion_tokens, "tokens": self.tokens,
                "seconds": round(self.seconds, 2), "refused": self.refused,
                "by_role": {r: dict(v) for r, v in self.by_role.items()}}


class Proxy:
    def __init__(self, upstreams: dict[str, str], runs_dir: Path = paths.RUNS):
        self.upstreams = {k: v.rstrip("/") for k, v in upstreams.items()}
        self.runs_dir = runs_dir
        self.budgets: dict[str, Budget] = {}
        self.usage: dict[str, Usage] = defaultdict(Usage)
        self.session: ClientSession | None = None

    def upstream(self, model: str) -> str:
        return self.upstreams.get(model) or self.upstreams["*"]

    def over(self, run: str) -> str | None:
        b, u = self.budgets.get(run), self.usage[run]
        if not b:
            return None
        if b.tokens is not None and u.tokens >= b.tokens:
            return "token budget spent"
        if b.requests is not None and u.requests >= b.requests:
            return "request budget spent"
        if b.seconds is not None and u.seconds >= b.seconds:
            return "model time budget spent"
        return None

    def log(self, run: str, record: dict) -> None:
        d = self.runs_dir.joinpath(*run.split("~"))  # run key "exp~task~a0" -> runs/exp/task/a0
        d.mkdir(parents=True, exist_ok=True)
        with open(d / "calls.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")

    async def forward(self, request: web.Request) -> web.Response:
        run = request.match_info.get("run", "default")
        role = request.match_info.get("role", "default")
        tail = request.match_info["tail"]
        body = await request.read()
        payload = json.loads(body) if body and request.content_type == "application/json" else {}
        model = payload.get("model", "")
        if tail.endswith("chat/completions") or tail.endswith("completions"):
            reason = self.over(run)
            if reason:
                self.usage[run].refused += 1
                self.log(run, {"t": time.time(), "role": role, "model": model, "refused": reason})
                return web.json_response({"error": {"message": f"budget: {reason}", "type": "budget"}}, status=429)
        payload.pop("stream_options", None)
        streaming = bool(payload.get("stream"))
        t0 = time.time()
        async with self.session.request(request.method, f"{self.upstream(model)}/{tail}",
                                        data=json.dumps(payload) if payload else body,
                                        headers={"Content-Type": "application/json"}) as up:
            raw = await up.read()
            status, ctype = up.status, up.headers.get("Content-Type", "application/json")
        dt = time.time() - t0
        if tail.endswith("completions") and not streaming:
            self.account(run, role, model, payload, raw, status, t0, dt)
        return web.Response(body=raw, status=status, content_type=ctype.split(";")[0])

    def account(self, run, role, model, payload, raw, status, t0, dt) -> None:
        try:
            resp = json.loads(raw)
        except json.JSONDecodeError:
            resp = {"raw": raw.decode("utf-8", "replace")[:2000]}
        usage = resp.get("usage") or {}
        pt, ct = int(usage.get("prompt_tokens") or 0), int(usage.get("completion_tokens") or 0)
        u = self.usage[run]
        u.requests += 1
        u.prompt_tokens += pt
        u.completion_tokens += ct
        u.seconds += dt
        r = u.by_role[role]
        r["requests"] += 1
        r["prompt_tokens"] += pt
        r["completion_tokens"] += ct
        r["seconds"] += dt
        choice = (resp.get("choices") or [{}])[0]
        self.log(run, {"t": t0, "seconds": round(dt, 3), "role": role, "model": model, "status": status,
                       "prompt_tokens": pt, "completion_tokens": ct,
                       "messages": payload.get("messages"), "params": {k: v for k, v in payload.items()
                                                                       if k not in ("messages", "model")},
                       "response": (choice.get("message") or {}).get("content") or choice.get("text"),
                       "finish_reason": choice.get("finish_reason")})

    async def set_budget(self, request: web.Request) -> web.Response:
        data = await request.json()
        self.budgets[request.match_info["run"]] = Budget(**data)
        return web.json_response({"ok": True})

    async def get_usage(self, request: web.Request) -> web.Response:
        return web.json_response(self.usage[request.match_info["run"]].as_dict())

    def app(self) -> web.Application:
        async def start(_):
            self.session = ClientSession(timeout=ClientTimeout(total=1800))

        async def stop(_):
            await self.session.close()

        app = web.Application(client_max_size=64 * 1024 * 1024)
        app.on_startup.append(start)
        app.on_cleanup.append(stop)
        app.router.add_post("/budget/{run}", self.set_budget)
        app.router.add_get("/usage/{run}", self.get_usage)
        app.router.add_route("*", "/r/{run}/{role}/v1/{tail:.*}", self.forward)
        app.router.add_route("*", "/v1/{tail:.*}", self.forward)
        return app


def serve(upstreams: dict[str, str], host: str = "127.0.0.1", port: int = 8090) -> None:
    web.run_app(Proxy(upstreams).app(), host=host, port=port, print=None)
