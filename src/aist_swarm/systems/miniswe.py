"""mini-SWE-agent as the single-agent baseline, unchanged except that it runs inside our sandbox.

Uses the upstream SWE-bench text-based config (one fenced bash command per turn), which small models follow more
reliably than JSON tool calls. The submitted patch is what the agent submits, as in upstream SWE-bench runs.
"""

from __future__ import annotations

import importlib.resources
import os
import platform
from typing import Any

import yaml

from aist_swarm.systems import Context, Outcome

os.environ.setdefault("MSWEA_COST_TRACKING", "ignore_errors")
os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
os.environ.setdefault("LITELLM_LOG", "ERROR")

try:
    import litellm

    litellm.suppress_debug_info = True
except ImportError:
    pass


class SandboxEnvironment:
    """mini-SWE-agent environment backed by an aist_swarm Attempt."""

    def __init__(self, ctx: Context, timeout: int = 60, env: dict[str, str] | None = None):
        self.ctx = ctx
        self.timeout = timeout
        self.env = env or {}

    def execute(self, action: dict, cwd: str = "", *, timeout: int | None = None) -> dict[str, Any]:
        from minisweagent.exceptions import Submitted

        command = action.get("command", "")
        prefix = " ".join(f"{k}={v}" for k, v in self.env.items())
        res = self.ctx.attempt.exec(f"{prefix} {command}" if prefix else command, timeout=timeout or self.timeout)
        output = {"output": res.output, "returncode": res.returncode,
                  "exception_info": "command timed out" if res.timed_out else ""}
        lines = output["output"].lstrip().splitlines(keepends=True)
        if lines and lines[0].strip() == "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" and res.returncode == 0:
            submission = "".join(lines[1:])
            raise Submitted({"role": "exit", "content": submission,
                             "extra": {"exit_status": "Submitted", "submission": submission}})
        return output

    def get_template_vars(self, **kwargs) -> dict[str, Any]:
        return {"system": "Linux", "release": "", "version": "", "machine": platform.machine(), "cwd": "/testbed",
                **kwargs}

    def serialize(self) -> dict:
        return {"info": {"config": {"environment_type": "aist_swarm.SandboxEnvironment"}}}


def upstream_config(name: str = "swebench_backticks") -> dict:
    files = importlib.resources.files("minisweagent.config.benchmarks")
    return yaml.safe_load((files / f"{name}.yaml").read_text(encoding="utf-8"))


class MiniSwe:
    def __init__(self, config: str = "swebench_backticks", step_limit: int = 50, wall_time: int = 0,
                 temperature: float | None = None, max_tokens: int | None = 2048, thinking: bool | None = None,
                 name: str = "miniswe"):
        self.name = name
        self.config = upstream_config(config)
        self.step_limit = step_limit
        self.wall_time = wall_time
        self.temperature = temperature
        self.max_tokens = max_tokens
        self.thinking = thinking

    def solve(self, ctx: Context) -> Outcome:
        from minisweagent.agents.default import DefaultAgent
        from minisweagent.models.litellm_textbased_model import LitellmTextbasedModel

        mcfg = {k: v for k, v in self.config["model"].items() if k not in ("model_name", "model_kwargs")}
        kwargs = {"api_base": ctx.llm_base("agent"), "api_key": "local", "drop_params": True}
        if self.temperature is not None:
            kwargs["temperature"] = self.temperature
        if self.max_tokens:
            kwargs["max_tokens"] = self.max_tokens
        if self.thinking is not None:  # hybrid reasoning models (Qwen3.5, Gemma 4): on/off is an experiment axis
            kwargs["extra_body"] = {"chat_template_kwargs": {"enable_thinking": self.thinking}}
        model = LitellmTextbasedModel(model_name=f"openai/{ctx.model}", model_kwargs=kwargs,
                                      cost_tracking="ignore_errors", **mcfg)
        env = SandboxEnvironment(ctx, timeout=self.config["environment"].get("timeout", 60),
                                 env=self.config["environment"].get("env", {}))
        acfg = dict(self.config["agent"])
        acfg.update(step_limit=self.step_limit, cost_limit=0, wall_time_limit_seconds=self.wall_time,
                    output_path=ctx.out_dir / "trajectory.json")
        agent = DefaultAgent(model, env, **acfg)
        try:
            info = agent.run(ctx.task.problem_statement)
        except Exception as e:  # a crashed agent is a failed attempt, not a crashed experiment
            info = {"exit_status": f"error: {type(e).__name__}: {e}"[:300], "submission": ""}
        return Outcome(patch=info.get("submission", "") or "", exit_status=info.get("exit_status", ""),
                       workspace_diff=ctx.attempt.diff(), extra={"steps": agent.n_calls})
