"""Systems under test: anything that turns a task and a sandbox into a patch, calling models only via the proxy."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

from aist_swarm.sandbox import Attempt
from aist_swarm.tasks import Task


@dataclass
class Context:
    task: Task
    attempt: Attempt
    run_key: str
    out_dir: Path
    proxy: str
    model: str
    params: dict = field(default_factory=dict)

    def llm_base(self, role: str = "agent") -> str:
        """OpenAI-compatible base URL that meters this run under `role`."""
        return f"{self.proxy.rstrip('/')}/r/{self.run_key}/{role}/v1"


@dataclass
class Outcome:
    patch: str
    exit_status: str
    workspace_diff: str = ""
    extra: dict = field(default_factory=dict)


class System(Protocol):
    name: str

    def solve(self, ctx: Context) -> Outcome: ...


def make(spec: dict) -> System:
    match spec.get("type"):
        case "miniswe":
            from aist_swarm.systems.miniswe import MiniSwe

            return MiniSwe(**{k: v for k, v in spec.items() if k != "type"})
        case "scout_fixer":
            from aist_swarm.systems.scout_fixer import ScoutFixer

            return ScoutFixer(**{k: v for k, v in spec.items() if k != "type"})
    raise ValueError(f"unknown system type: {spec.get('type')}")
