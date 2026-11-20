"""Two roles on shared weights: a read-only scout writes a short note, a fixer repairs the bug from a clean context.

The question it serves (PLAN, experiment D): does a short message from a role that read the code help more than
one agent carrying the whole exploration in its own context? The note width is the knob (`note_words`). Both
roles share one run budget at the proxy, so the comparison with a single agent is at equal spend.
Orchestration is a FEDOT.Swarm flow, `scout + fixer`; parallel fixers become `gather` later.
"""

from __future__ import annotations

import asyncio
import json
import time

from aist_swarm.systems import Context, Outcome
from aist_swarm.systems.miniswe import MiniSwe

SCOUT_TEMPLATE = """<pr_description>
{{task}}
</pr_description>

<instructions>
You are the SCOUT in a two-person team. A teammate will fix the bug; your job is only to find where it lives.

- Work in /testbed. Read code with commands like `grep -rn`, `sed -n '10,40p' file`, `ls`. Do NOT edit any file.
- Find the files, functions and line numbers involved, what is wrong, and how it should be fixed.
- Then write a note of at most {{note_words}} words to /tmp/note.md (create it with `cat <<'EOF' > /tmp/note.md`).
- Finally submit with exactly this command, on its own:

```mswea_bash_command
echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT && cat /tmp/note.md
```

Every response has a THOUGHT and exactly ONE bash block, as in the format example.
</instructions>
"""

FIXER_SUFFIX = """

<scout_note>
A teammate already explored the repository for this issue. Their note (may be incomplete or wrong):
{note}
</scout_note>
"""


class ScoutFixer(MiniSwe):
    def __init__(self, scout_steps: int = 10, fixer_steps: int = 30, note_words: int = 150,
                 name: str = "scout-fixer", **kwargs):
        super().__init__(name=name, **kwargs)
        self.scout_steps = scout_steps
        self.fixer_steps = fixer_steps
        self.note_words = note_words

    def solve(self, ctx: Context) -> Outcome:
        from fedotmas import action

        traj = ctx.out_dir / "trajectories"
        traj.mkdir(parents=True, exist_ok=True)
        stats: dict = {}

        @action
        async def scout(issue: str) -> str:
            return await asyncio.to_thread(self._scout, ctx, issue, stats)

        @action
        async def fixer(note: str) -> dict:
            return await asyncio.to_thread(self._fix, ctx, note, stats)

        run = asyncio.run((scout + fixer).run(ctx.task.problem_statement))
        info = run.value or {"exit_status": f"flow {run.reason}: {run.errors}"[:300], "submission": ""}
        return Outcome(patch=info.get("submission", "") or "", exit_status=info.get("exit_status", ""),
                       workspace_diff=ctx.attempt.diff(), extra={"steps": stats.get("steps", 0), **stats})

    def _scout(self, ctx: Context, issue: str, stats: dict) -> str:
        template = SCOUT_TEMPLATE.replace("{{note_words}}", str(self.note_words))
        info, steps = self.run_role(ctx, "scout", issue, self.scout_steps,
                                    ctx.out_dir / "trajectories" / "scout.json", instance_template=template)
        ctx.attempt.exec("git checkout -q -- . && git clean -fdq", timeout=60)  # the scout must not leave edits
        words = (info.get("submission") or "").split()
        note = " ".join(words[: self.note_words]) or "(the scout found nothing useful)"
        stats.update(steps=steps, scout_steps=steps, scout_exit=info.get("exit_status", ""), note_words=len(words))
        with open(ctx.out_dir / "messages.jsonl", "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": time.time(), "from": "scout", "to": "fixer", "text": note,
                                "tokens": int(len(note.split()) * 1.4)}, ensure_ascii=False) + "\n")
        return note

    def _fix(self, ctx: Context, note: str, stats: dict) -> dict:
        info, steps = self.run_role(ctx, "fixer", ctx.task.problem_statement + FIXER_SUFFIX.format(note=note),
                                    self.fixer_steps, ctx.out_dir / "trajectories" / "fixer.json")
        stats["steps"] = stats.get("steps", 0) + steps
        stats["fixer_steps"] = steps
        return info
