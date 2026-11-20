"""Pack an experiment job for a Kaggle GPU session and merge its results back into the local runs."""

from __future__ import annotations

import base64
import io
import json
import shutil
import tarfile
from pathlib import Path

import yaml

from aist_swarm import paths

ROOT = Path(__file__).resolve().parents[2]


def _payload(taskset: Path, controls: Path | None = None) -> str:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as t:
        for p in [ROOT / "pyproject.toml", ROOT / "README.md"]:
            t.add(p, arcname=p.name)
        t.add(ROOT / "src" / "aist_swarm", arcname="src/aist_swarm",
              filter=lambda i: None if "__pycache__" in i.name else i)
        t.add(taskset, arcname=f"tasks/{taskset.name}")
        if controls:  # eligibility measured on the same kind of host: the job skips its own controls
            t.add(controls, arcname=f"tasks/{taskset.stem}.controls.json")
    return base64.b64encode(buf.getvalue()).decode()


def _script(job_yaml: Path, out: Path, hardware: dict | None = None, controls: Path | None = None) -> dict:
    """Write <out>/run.py: the batch job with this package, the task set and the job spec inside."""
    job = yaml.safe_load(job_yaml.read_text(encoding="utf-8"))
    if hardware:
        job["hardware"] = hardware
    for exp in job["experiments"]:
        exp.setdefault("tasks", job["taskset"])
        exp.setdefault("hardware", {}).update(job.get("hardware", {}))
    taskset = paths.TASKS / f"{job['taskset']}.jsonl"
    code = (Path(__file__).parent / "kaggle_job.py").read_text(encoding="utf-8")
    code = code.replace("__PAYLOAD__", _payload(taskset, controls)).replace("__JOB__", json.dumps(job, ensure_ascii=False))
    out.mkdir(parents=True, exist_ok=True)
    (out / "run.py").write_text(code, encoding="utf-8")
    return job


def pack(job_yaml: Path, out: Path, slug: str, gpu: bool = True) -> Path:
    """Write <out>/run.py and kernel-metadata.json for `kaggle kernels push`."""
    _script(job_yaml, out)
    meta = {"id": slug, "title": slug.split("/")[-1], "code_file": "run.py", "language": "python",
            "kernel_type": "script", "is_private": True, "enable_gpu": gpu, "enable_internet": True,
            "dataset_sources": [], "competition_sources": [], "kernel_sources": [], "model_sources": []}
    (out / "kernel-metadata.json").write_text(json.dumps(meta, indent=1), encoding="utf-8")
    return out


DS_GPUS = {"gt4.1": "DataSphere gt4.1, T4 16GB, 4 vCPU", "gt4i.1": "DataSphere gt4i.1, T4i 24GB, 8 vCPU",
           "g1.1": "DataSphere g1.1, V100 32GB, 8 vCPU", "g2.1": "DataSphere g2.1, A100 80GB, 28 vCPU"}


def ds_pack(job_yaml: Path, out: Path, instances: list[str], llama_bin: Path | None = None,
            controls: Path | None = None) -> Path:
    """Write <out>/run.py and config.yaml for `datasphere project job execute -c config.yaml`."""
    job = yaml.safe_load(job_yaml.read_text(encoding="utf-8"))
    hw = dict(job.get("hardware", {}), gpu=DS_GPUS.get(instances[0], instances[0]))
    _script(job_yaml, out, hw, controls)
    inputs = ["run.py"]
    if llama_bin:  # skips the 20-minute CUDA build; must come from the same image and GPU generation
        shutil.copy(llama_bin, out / "llama-server")
        inputs.append("llama-server")
    config = {"name": f"aist-{job_yaml.stem}", "desc": f"aist-swarm job {job_yaml.name}",
              "cmd": "python3 run.py", "inputs": inputs, "outputs": ["results.tar.gz"],
              "cloud-instance-types": instances,
              "graceful-shutdown": {"signal": "SIGTERM", "timeout": "60s"}}
    (out / "config.yaml").write_text(yaml.safe_dump(config, sort_keys=False), encoding="utf-8")
    return out


def merge(archive: Path) -> list[str]:
    """Unpack results.tar.gz from a job; experiments go to the local runs dir, controls next to the task set."""
    tmp = paths.HOME / "tmp-merge"
    shutil.rmtree(tmp, ignore_errors=True)
    with tarfile.open(archive) as t:
        t.extractall(tmp, filter="data")
    res = tmp / "results"
    merged = []
    for exp in (res / "runs").iterdir() if (res / "runs").exists() else []:
        dest = paths.RUNS / exp.name
        shutil.rmtree(dest, ignore_errors=True)
        shutil.copytree(exp, dest)
        merged.append(exp.name)
    for p in (res / "tasks").glob("*.controls.json") if (res / "tasks").exists() else []:
        shutil.copy(p, paths.TASKS / p.name.replace(".controls.json", ".kaggle.controls.json"))
    keep = paths.HOME / "kaggle-logs" / archive.parent.name
    keep.mkdir(parents=True, exist_ok=True)
    for p in res.glob("*.log"):
        shutil.copy(p, keep / p.name)
    for p in res.glob("llama-server-*.bin"):  # reuse with `ds-pack --llama-bin`
        (paths.HOME / "bin").mkdir(exist_ok=True)
        shutil.copy(p, paths.HOME / "bin" / p.name)
    if (res / "env-logs").exists():
        shutil.copytree(res / "env-logs", keep / "env-logs", dirs_exist_ok=True)
    shutil.rmtree(tmp, ignore_errors=True)
    return merged
