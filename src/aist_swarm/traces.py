"""Read experiment results and turn a run's raw logs into a normalized timeline of events.

The timeline is what the viewer replays and what paper figures are drawn from. One event is one thing that
happened between two nodes of the run graph: a role asked a model, a role ran a command in the sandbox, a role
messaged another role, a patch went to the judge. Nodes are roles plus the fixed `issue`, `model:*`, `sandbox`
and `judge`, so a single agent and a swarm share one picture.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import yaml

from aist_swarm import paths

COMMAND = re.compile(r"```(?:mswea_bash_command|bash|sh|shell)?[ \t]*\n(.*?)\n```", re.S)
OUTPUT_LIMIT = 6000


def _jsonl(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]


def experiments() -> list[dict]:
    out = []
    for d in sorted(paths.RUNS.glob("*/experiment.yaml")):
        root = d.parent
        cfg = yaml.safe_load(d.read_text(encoding="utf-8"))
        rows = _jsonl(root / "results.jsonl")
        out.append(summary(cfg, rows, root))
    return sorted(out, key=lambda e: e["updated"], reverse=True)


def summary(cfg: dict, rows: list[dict], root: Path) -> dict:
    done = [r for r in rows if "usage" in r]
    tokens = [r["usage"].get("tokens", 0) for r in done]
    secs = [r.get("solve_seconds", 0) for r in done]
    statuses: dict[str, int] = {}
    for r in rows:
        key = (r.get("exit_status") or "?").split(":")[0]
        statuses[key] = statuses.get(key, 0) + 1
    return {
        "name": cfg["name"], "model": cfg.get("model"), "system": cfg.get("system", {}),
        "budget": cfg.get("budget", {}), "hardware": cfg.get("hardware", {}),
        "question": cfg.get("question", ""), "hypothesis": cfg.get("hypothesis", ""),
        "decision": cfg.get("decision", ""), "tasks": cfg.get("tasks"), "attempts": cfg.get("attempts", 1),
        "runs": len(rows), "resolved": sum(1 for r in rows if r.get("resolved")),
        "tokens_mean": round(sum(tokens) / len(tokens)) if tokens else 0,
        "seconds_mean": round(sum(secs) / len(secs), 1) if secs else 0,
        "statuses": statuses,
        "updated": max((p.stat().st_mtime for p in root.rglob("result.json")), default=root.stat().st_mtime),
    }


def experiment(name: str) -> dict:
    root = paths.RUNS / name
    cfg = yaml.safe_load((root / "experiment.yaml").read_text(encoding="utf-8"))
    rows = _jsonl(root / "results.jsonl")
    return {"summary": summary(cfg, rows, root), "config": cfg, "rows": rows}


def run(name: str, task: str, attempt: str) -> dict:
    d = paths.RUNS / name / task / attempt
    result = json.loads((d / "result.json").read_text(encoding="utf-8")) if (d / "result.json").exists() else {}
    trajs = role_trajectories(d)
    calls = _jsonl(d / "calls.jsonl")
    issue = ""
    for traj in trajs.values():
        for m in traj.get("messages", []):
            if m.get("role") == "user":
                issue = m.get("content", "")
                break
        if issue:
            break
    exp_cfg = paths.RUNS / name / "experiment.yaml"
    budget = (yaml.safe_load(exp_cfg.read_text(encoding="utf-8")) or {}).get("budget", {}) if exp_cfg.exists() else {}
    return {
        "result": result,
        "budget": budget,
        "issue": _issue_text(issue),
        "timeline": swarm_timeline(trajs, calls, result, _jsonl(d / "messages.jsonl")),
        "patch": _read(d / "patch.diff"),
        "workspace_diff": _read(d / "workspace.diff"),
    }


def _read(p: Path) -> str:
    return p.read_text(encoding="utf-8") if p.exists() else ""


def _issue_text(prompt: str) -> str:
    m = re.search(r"<pr_description>(.*?)</pr_description>", prompt, re.S)
    text = m.group(1) if m else prompt
    return text.replace("Consider the following PR description:", "").strip()


def _clip(s: str, n: int = OUTPUT_LIMIT) -> str:
    return s if len(s) <= n else s[: n // 2] + "\n…\n" + s[-n // 2:]


def role_trajectories(d: Path) -> dict[str, dict]:
    """`trajectory.json` is the single agent; a swarm writes `trajectories/<role>.json`, one per role."""
    out = {}
    if (d / "trajectory.json").exists():
        out["agent"] = json.loads((d / "trajectory.json").read_text(encoding="utf-8"))
    for p in sorted((d / "trajectories").glob("*.json")) if (d / "trajectories").exists() else []:
        out[p.stem] = json.loads(p.read_text(encoding="utf-8"))
    return out


def swarm_timeline(trajs: dict[str, dict], calls: list[dict], result: dict, messages: list[dict]) -> dict:
    """Merge every role's events on one clock; inter-role messages (`messages.jsonl`) become message events."""
    t0 = min((c["t"] for c in calls), default=0.0)
    events: list[dict] = []
    models: set[str] = set()
    for role, traj in trajs.items():
        role_calls = [c for c in calls if c.get("role") == role] or (calls if len(trajs) == 1 else [])
        part = timeline(traj, role_calls, result, role=role, t0=t0)
        events += part["events"]
        models |= {n["id"] for n in part["nodes"] if n["kind"] == "model"}
    for m in messages:
        events.append({"kind": "message", "from": m["from"], "to": m["to"], "t": round(m["t"] - t0, 2), "dur": 0,
                       "text": _clip(m.get("text", ""), 3000), "tokens_out": m.get("tokens", 0)})
    exits = [e for e in events if e["kind"] == "exit"]
    events = [e for e in events if e["kind"] != "exit"]
    events.sort(key=lambda e: e["t"])
    if exits:  # one verdict per run, from the role that stopped last, after everything else
        end = max((e["t"] + e["dur"] for e in events), default=0)
        final = max(exits, key=lambda e: e["t"])
        events.append(final | {"t": round(max(end, final["t"]), 2)})
    roles: list[str] = []
    for e in events:  # in order of first appearance: the role that starts is the one the issue goes to
        for r in (e["from"], e["to"]):
            if r not in roles and r not in models and r not in ("sandbox", "judge", "issue"):
                roles.append(r)
    nodes = [{"id": "issue", "kind": "issue"}] + [{"id": r, "kind": "role"} for r in roles] + [
        {"id": m, "kind": "model"} for m in sorted(models)] + [{"id": "sandbox", "kind": "sandbox"},
                                                             {"id": "judge", "kind": "judge"}]
    edges = [{"from": "issue", "to": roles[0]}] if roles else []
    seen = set()
    for e in events:
        key = tuple(sorted((e["from"], e["to"])))
        if e["from"] != e["to"] and key not in seen:
            seen.add(key)
            edges.append({"from": e["from"], "to": e["to"]})
    return {"nodes": nodes, "edges": edges, "events": events,
            "duration": round(max((e["t"] + e["dur"] for e in events), default=0), 2)}


def timeline(traj: dict, calls: list[dict], result: dict, role: str = "agent", t0: float | None = None) -> dict:
    """Events of one role from a mini-SWE-agent trajectory plus the proxy's call log (timings and tokens)."""
    msgs = traj.get("messages", [])
    if t0 is None:
        t0 = calls[0]["t"] if calls else 0.0
    events: list[dict] = []
    call_i = 0
    model = (calls[0].get("model") if calls else None) or result.get("model") or "model"
    for m in msgs:
        kind = m.get("role")
        extra = m.get("extra") or {}
        if kind == "assistant":
            c = calls[call_i] if call_i < len(calls) else {}
            call_i += 1
            content = m.get("content") or ""
            cmd = COMMAND.search(content)
            thought = COMMAND.sub("", content).strip()
            start = (c.get("t", t0) - t0)
            events.append({"kind": "llm", "from": role, "to": f"model:{model}", "t": round(start, 2),
                           "dur": c.get("seconds", 0), "tokens_in": c.get("prompt_tokens", 0),
                           "tokens_out": c.get("completion_tokens", 0), "text": _clip(thought, 3000),
                           "finish": c.get("finish_reason")})
            if cmd:
                events.append({"kind": "exec", "from": role, "to": "sandbox",
                               "t": round(start + c.get("seconds", 0), 2), "dur": 0,
                               "command": cmd.group(1).strip()})
            elif not extra.get("actions"):
                events.append({"kind": "format_error", "from": role, "to": role,
                               "t": round(start + c.get("seconds", 0), 2), "dur": 0})
        elif kind == "user" and "response" in extra:  # format error: the model's reply lives in extra
            c = calls[call_i] if call_i < len(calls) else {}
            call_i += 1
            resp = extra.get("response") or {}
            content = ""
            if isinstance(resp, dict):
                content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
            start = (c.get("t", t0) - t0)
            events.append({"kind": "llm", "from": role, "to": f"model:{model}", "t": round(start, 2),
                           "dur": c.get("seconds", 0), "tokens_in": c.get("prompt_tokens", 0),
                           "tokens_out": c.get("completion_tokens", 0), "text": _clip(content, 3000),
                           "finish": c.get("finish_reason")})
            events.append({"kind": "format_error", "from": role, "to": role,
                           "t": round(start + c.get("seconds", 0), 2), "dur": 0})
        elif kind == "user" and "returncode" in extra and events:
            last = next((e for e in reversed(events) if e["kind"] == "exec" and "output" not in e), None)
            if last:
                ts = extra.get("timestamp")
                if ts and t0:
                    last["dur"] = max(0.0, round(ts - t0 - last["t"], 2))
                last["returncode"] = extra.get("returncode")
                last["output"] = _clip(extra.get("raw_output") or m.get("content", ""))
        elif kind == "exit":
            end = events[-1]["t"] + events[-1]["dur"] if events else 0
            events.append({"kind": "exit", "from": role, "to": "judge", "t": round(end, 2), "dur": 0,
                           "status": extra.get("exit_status", m.get("content", ""))[:200],
                           "resolved": result.get("resolved")})
    roles = sorted({e["from"] for e in events if e["from"] not in ("issue",)})
    nodes = [{"id": "issue", "kind": "issue"}] + [{"id": r, "kind": "role"} for r in roles] + [
        {"id": f"model:{model}", "kind": "model"}, {"id": "sandbox", "kind": "sandbox"},
        {"id": "judge", "kind": "judge"}]
    edges = [{"from": "issue", "to": r} for r in roles]
    seen = set()
    for e in events:
        key = (e["from"], e["to"])
        if e["from"] != e["to"] and key not in seen:
            seen.add(key)
            edges.append({"from": e["from"], "to": e["to"]})
    return {"nodes": nodes, "edges": edges, "events": events,
            "duration": round(max((e["t"] + e["dur"] for e in events), default=0), 2)}
