"""Bug-fix tasks in the SWE-bench shape: a repo at a commit, an issue, a hidden test patch and the tests that decide."""

from __future__ import annotations

import ast
import json
import re
from pathlib import Path

from pydantic import BaseModel, Field


class EnvSpec(BaseModel):
    """How to build the task's Python environment."""

    python: str = "3.11"
    requirements: list[str] = Field(default_factory=list)
    install: str = "pip install --no-deps -e ."


class Task(BaseModel):
    instance_id: str
    repo: str
    base_commit: str
    problem_statement: str
    hints_text: str = ""
    patch: str = ""
    test_patch: str = ""
    fail_to_pass: list[str] = Field(default_factory=list)
    pass_to_pass: list[str] = Field(default_factory=list)
    created_at: str = ""
    env: EnvSpec = Field(default_factory=EnvSpec)
    source: str = ""

    def test_files(self) -> list[str]:
        """Files the test patch touches; used to run tests when no test ids are given."""
        return sorted(set(re.findall(r"^\+\+\+ b/(\S+)", self.test_patch, flags=re.M)) - {"/dev/null"})

    def patch_files(self) -> list[str]:
        return sorted(set(re.findall(r"^\+\+\+ b/(\S+)", self.patch, flags=re.M)) - {"/dev/null"})


def _id_list(value) -> list[str]:
    match value:
        case None | "":
            return []
        case list():
            return [str(v) for v in value]
        case str():
            try:
                return [str(v) for v in json.loads(value)]
            except json.JSONDecodeError:
                return [str(v) for v in ast.literal_eval(value)]
    raise ValueError(f"unexpected test id list: {value!r}")


def from_swe_row(row: dict, env: dict | None = None, source: str = "") -> Task:
    """One SWE-bench / SWE-rebench row (FAIL_TO_PASS etc. may be JSON strings) plus an optional env entry."""
    spec = EnvSpec(**{k: v for k, v in (env or {}).items() if k in EnvSpec.model_fields})
    return Task(
        instance_id=row["instance_id"],
        repo=row["repo"],
        base_commit=row["base_commit"],
        problem_statement=row["problem_statement"],
        hints_text=row.get("hints_text") or "",
        patch=row.get("patch") or "",
        test_patch=row.get("test_patch") or "",
        fail_to_pass=_id_list(row.get("FAIL_TO_PASS")),
        pass_to_pass=_id_list(row.get("PASS_TO_PASS")),
        created_at=str(row.get("created_at") or ""),
        env=spec,
        source=source,
    )


def load_swe_jsonl(path: Path, env_json: Path | None = None, ids: set[str] | None = None) -> list[Task]:
    envs = json.loads(env_json.read_text(encoding="utf-8")) if env_json else {}
    tasks = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        if ids is not None and row["instance_id"] not in ids:
            continue
        tasks.append(from_swe_row(row, envs.get(row["instance_id"]), source=path.parent.name))
    return tasks


def save(tasks: list[Task], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(t.model_dump_json() + "\n" for t in tasks), encoding="utf-8")


def load(path: Path) -> list[Task]:
    return [Task.model_validate_json(l) for l in path.read_text(encoding="utf-8").splitlines() if l.strip()]
