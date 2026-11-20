"""Task environments and isolated attempts: Docker (default) or plain venvs (`AIST_SANDBOX=local`).

A task environment is built once into a named volume: `/env/repo` is the repo at the base commit (history removed,
so later commits cannot leak the fix) and `/env/venv` holds its dependencies, installed in editable mode against
`/testbed`. Every attempt (agent run or judge run) is a fresh container that copies `/env/repo` to `/testbed`, so
attempts never see each other's edits and the editable install points at the attempt's own code.
"""

from __future__ import annotations

import os
import re
import shlex
import shutil
import subprocess
import threading
import uuid
from dataclasses import dataclass
from pathlib import Path

from aist_swarm.tasks import Task

DOCKERFILE = Path(__file__).resolve().parents[2] / "docker" / "sandbox.Dockerfile"
OUTPUT_LIMIT = 20_000


@dataclass
class ExecResult:
    returncode: int
    output: str
    timed_out: bool = False


def docker(*args: str, timeout: float | None = None, input: str | None = None) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["docker", *args], capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=timeout, input=input,
    )


def image(python: str) -> str:
    """Base image for a Python version, built on first use."""
    tag = f"aist-sandbox:py{python}"
    if docker("image", "inspect", tag).returncode != 0:
        r = docker("build", "-t", tag, "--build-arg", f"PY={python}", "-f", str(DOCKERFILE), str(DOCKERFILE.parent),
                   timeout=1800)
        if r.returncode != 0:
            raise RuntimeError(f"image build failed for python {python}:\n{r.stderr[-3000:]}")
    return tag


def volume(task: Task) -> str:
    return "aist-env-" + re.sub(r"[^a-z0-9_.-]", "-", task.instance_id.lower())


def docker_env_ready(task: Task) -> bool:
    if volume(task) not in existing_volumes():
        return False
    r = docker("run", "--rm", "-v", f"{volume(task)}:/env:ro", image(task.env.python), "test", "-f", "/env/READY")
    return r.returncode == 0


def existing_volumes() -> set[str]:
    return set(docker("volume", "ls", "-q").stdout.split())


def docker_build_env(task: Task, log: Path | None = None, force: bool = False) -> bool:
    """Clone the repo at the base commit, strip history, install dependencies. Needs network; returns success."""
    if not force and docker_env_ready(task):
        return True
    vol = volume(task)
    docker("volume", "rm", "-f", vol)
    docker("volume", "create", vol)
    reqs = " ".join(shlex.quote(r) for r in task.env.requirements)
    script = f"""
set -e
git init -q /testbed && cd /testbed
git fetch -q --depth 1 https://github.com/{task.repo}.git {task.base_commit}
git checkout -q FETCH_HEAD
rm -rf .git && git init -q && git add -A && git commit -qm base
python -m venv /env/venv
. /env/venv/bin/activate
pip install -q --upgrade pip wheel setuptools
{"pip install -q " + reqs if reqs else "true"}
pip install -q pytest
{task.env.install}
git add -A && git commit -qm env --allow-empty
cp -a /testbed /env/repo
touch /env/READY
"""
    r = docker("run", "--rm", "-v", f"{vol}:/env", "-v", "aist-pip-cache:/root/.cache/pip", image(task.env.python), "bash", "-c", script, timeout=3600)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(r.stdout + "\n--- stderr ---\n" + r.stderr, encoding="utf-8")
    return r.returncode == 0


class DockerAttempt:
    """A running container with its own copy of the task repo at /testbed and no network."""

    def __init__(self, task: Task, network: bool = False, memory: str = "4g", cpus: str = "2"):
        self.task = task
        self.name = f"aist-{uuid.uuid4().hex[:12]}"
        args = ["run", "-d", "--name", self.name, "-v", f"{volume(task)}:/env:ro", "--memory", memory, "--cpus", cpus]
        if not network:
            args += ["--network", "none"]
        r = docker(*args, image(task.env.python), "sleep", "infinity")
        if r.returncode != 0:
            raise RuntimeError(f"container start failed: {r.stderr}")
        setup = self.exec("cp -a /env/repo/. /testbed/ && git status --short | head -5", timeout=600)
        if setup.returncode != 0:
            self.close()
            raise RuntimeError(f"workspace copy failed: {setup.output}")

    def exec(self, command: str, timeout: float = 120, input: str | None = None, limit: int | None = OUTPUT_LIMIT,
             stderr: bool = True) -> ExecResult:
        wrapped = f"source /env/venv/bin/activate && cd /testbed && {command}"
        args = ["exec", "-i", self.name, "timeout", "-k", "5", str(int(timeout)), "bash", "-c", wrapped]
        try:
            r = docker(*args, timeout=timeout + 30, input=input)
        except subprocess.TimeoutExpired:
            return ExecResult(124, "", timed_out=True)
        out = r.stdout + (("\n" + r.stderr) if stderr and r.stderr else "")
        if limit and len(out) > limit:
            out = out[: limit // 2] + "\n... [output truncated] ...\n" + out[-limit // 2:]
        return ExecResult(r.returncode, out, timed_out=r.returncode == 124)

    def write(self, path: str, content: str) -> ExecResult:
        return self.exec(f"cat > {shlex.quote(path)}", input=content, timeout=60)

    def apply(self, patch: str) -> ExecResult:
        """Apply a unified diff to /testbed: strict git apply first, then fuzzy patch(1)."""
        if not patch.strip():
            return ExecResult(0, "empty patch")
        self.write("/tmp/apply.diff", patch if patch.endswith("\n") else patch + "\n")
        return self.exec("git apply --whitespace=nowarn /tmp/apply.diff "
                         "|| patch -p1 --forward --batch --fuzz=3 < /tmp/apply.diff", timeout=120)

    def diff(self) -> str:
        """The attempt's change against the base, new files included, ignored files excluded."""
        return self.exec("git add -A >/dev/null 2>&1; git diff --cached --binary HEAD", timeout=120,
                         limit=None, stderr=False).output

    def close(self) -> None:
        docker("rm", "-f", self.name)

    def __enter__(self) -> DockerAttempt:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


# Local backend: for Linux hosts without Docker (Kaggle, Colab). No network isolation, so the agent could reach
# the internet there; the judge still runs the hidden tests on a clean reset of the repository.

def _local_dir(task: Task) -> Path:
    from aist_swarm import paths

    return paths.ENVS / re.sub(r"[^A-Za-z0-9_.-]", "-", task.instance_id)


def local_env_ready(task: Task) -> bool:
    return (_local_dir(task) / "READY").exists()


def local_build_env(task: Task, log: Path | None = None, force: bool = False) -> bool:
    """Repo at the base commit (history removed) plus a uv venv with the task's Python and pinned requirements."""
    if not force and local_env_ready(task):
        return True
    d = _local_dir(task)
    subprocess.run(["rm", "-rf", str(d)])
    d.mkdir(parents=True)
    reqs = " ".join(shlex.quote(r) for r in task.env.requirements)
    install = task.env.install.replace("pip install", "uv pip install")
    script = f"""
set -e
git init -q testbed && cd testbed
git fetch -q --depth 1 https://github.com/{task.repo}.git {task.base_commit}
git checkout -q FETCH_HEAD
rm -rf .git && git init -q && git add -A && git -c user.email=s@l -c user.name=s commit -qm base
uv venv -q --seed --python {task.env.python} ../venv
. ../venv/bin/activate
{"uv pip install -q " + reqs if reqs else "true"}
uv pip install -q pytest
{install}
../venv/bin/python -c "import pytest"
git add -A && git -c user.email=s@l -c user.name=s commit -qm env --allow-empty
touch ../READY
"""
    # Kaggle images set UV_SYSTEM_PYTHON, which sends every `uv pip install` to /usr instead of the task venv
    env = {k: v for k, v in os.environ.items() if k not in ("UV_SYSTEM_PYTHON", "VIRTUAL_ENV", "PYTHONPATH")}
    r = subprocess.run(["bash", "-c", script], cwd=d, capture_output=True, text=True, timeout=3600, env=env)
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        log.write_text(r.stdout + "\n--- stderr ---\n" + r.stderr, encoding="utf-8")
    return r.returncode == 0


_locks: dict[str, threading.Lock] = {}
_locks_guard = threading.Lock()


class LocalAttempt:
    """The task's own checkout, reset to the env commit; one attempt per task at a time (the venv's editable
    install points at this path)."""

    def __init__(self, task: Task, network: bool = False, **_):
        self.task = task
        self.dir = _local_dir(task)
        self.tmp = self.dir / "tmp"  # stands in for /tmp: attempts on other tasks run on the same host at once
        with _locks_guard:
            self.lock = _locks.setdefault(task.instance_id, threading.Lock())
        self.lock.acquire()
        self._reset()

    def _reset(self) -> None:
        subprocess.run(["bash", "-c", "git reset -q --hard HEAD && git clean -fdq"], cwd=self.dir / "testbed")
        shutil.rmtree(self.tmp, ignore_errors=True)
        self.tmp.mkdir()

    def exec(self, command: str, timeout: float = 120, input: str | None = None, limit: int | None = OUTPUT_LIMIT,
             stderr: bool = True) -> ExecResult:
        venv = self.dir / "venv"
        command = command.replace("/tmp/", f"{self.tmp}/")
        wrapped = (f"export TMPDIR={shlex.quote(str(self.tmp))} && source {shlex.quote(str(venv))}/bin/activate && "
                   f"cd {shlex.quote(str(self.dir / 'testbed'))} && {command}")
        try:
            r = subprocess.run(["timeout", "-k", "5", str(int(timeout)), "bash", "-c", wrapped], capture_output=True,
                               text=True, encoding="utf-8", errors="replace", input=input, timeout=timeout + 30)
        except subprocess.TimeoutExpired:
            return ExecResult(124, "", timed_out=True)
        out = r.stdout + (("\n" + r.stderr) if stderr and r.stderr else "")
        if limit and len(out) > limit:
            out = out[: limit // 2] + "\n... [output truncated] ...\n" + out[-limit // 2:]
        return ExecResult(r.returncode, out, timed_out=r.returncode == 124)

    write = DockerAttempt.write
    apply = DockerAttempt.apply
    diff = DockerAttempt.diff

    def close(self) -> None:
        try:
            self._reset()
        finally:
            self.lock.release()

    def __enter__(self) -> LocalAttempt:
        return self

    def __exit__(self, *exc) -> None:
        self.close()


BACKEND = os.environ.get("AIST_SANDBOX", "docker")


def build_env(task: Task, log: Path | None = None, force: bool = False) -> bool:
    return (local_build_env if BACKEND == "local" else docker_build_env)(task, log, force)


def env_ready(task: Task) -> bool:
    return (local_env_ready if BACKEND == "local" else docker_env_ready)(task)


def Attempt(task: Task, **kwargs):  # noqa: N802 - a factory that reads like the class it returns
    return (LocalAttempt if BACKEND == "local" else DockerAttempt)(task, **kwargs)
