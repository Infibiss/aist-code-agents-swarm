"""Batch job for a Kaggle GPU session. `aist-swarm kaggle-pack` fills PAYLOAD (this package, configs, tasks) and JOB.

Everything runs inside the session: llama.cpp is built for the GPU it gets, task environments are venvs
(no Docker on Kaggle), models come from Hugging Face, each GPU serves one model at a time behind the metering
proxy, and results are written to /kaggle/working/results.tar.gz for `aist-swarm kaggle-merge`.
"""

import base64
import io
import json
import os
import pathlib
import shutil
import subprocess
import sys
import tarfile
import threading
import time

import yaml

PAYLOAD = "__PAYLOAD__"
JOB = json.loads(r"""__JOB__""")

WORK = pathlib.Path("/kaggle/working")
SRC = pathlib.Path("/tmp/aist")
HOME = pathlib.Path("/tmp/aist-var")
T0 = time.time()


def log(*a):
    print(f"[{(time.time() - T0) / 60:6.1f} min]", *a, flush=True)


def sh(cmd, check=True, **kw):
    log("+", cmd[:200])
    return subprocess.run(cmd, shell=True, check=check, **kw)


def build_llama():
    cap = subprocess.run("nvidia-smi --query-gpu=compute_cap --format=csv,noheader", shell=True,
                         capture_output=True, text=True).stdout.split()[0].replace(".", "")
    sh(f"git clone -q --depth 1 --branch {JOB['llama_tag']} https://github.com/ggml-org/llama.cpp /tmp/llama")
    sh(f"cmake -S /tmp/llama -B /tmp/llama/build -DGGML_CUDA=ON -DCMAKE_CUDA_ARCHITECTURES={cap} "
       "-DLLAMA_CURL=OFF -DCMAKE_BUILD_TYPE=Release > /tmp/llama-cmake.log 2>&1")
    sh("cmake --build /tmp/llama/build --target llama-server -j 4 > /tmp/llama-build.log 2>&1")
    log("llama-server built for sm", cap)


def fetch_models():
    from huggingface_hub import hf_hub_download

    for name, m in JOB["models"].items():
        m["path"] = hf_hub_download(m["repo"], m["file"], local_dir="/tmp/models")
        log("model", name, m["path"])


def env():
    return os.environ | {"AIST_SANDBOX": "local", "AIST_SWARM_HOME": str(HOME), "PYTHONUNBUFFERED": "1",
                         "MSWEA_COST_TRACKING": "ignore_errors"}


def cli(*args, check=False):
    return sh(" ".join([sys.executable, "-m", "aist_swarm.cli", *args]), check=check, env=env())


def gpu_worker(gpu: int, port: int, models: list[str]):
    for name in models:
        m = JOB["models"][name]
        server = subprocess.Popen(
            f"/tmp/llama/build/bin/llama-server -m {m['path']} --alias {name} -ngl 99 -c {m.get('ctx', 32768)} "
            f"-np {m.get('slots', 2)} --jinja --host 127.0.0.1 --port {port} > /tmp/llama-{name}.log 2>&1",
            shell=True, env=os.environ | {"CUDA_VISIBLE_DEVICES": str(gpu)})
        for _ in range(180):
            if subprocess.run(f"curl -sf http://127.0.0.1:{port}/health", shell=True,
                              capture_output=True).returncode == 0:
                break
            time.sleep(5)
        log(f"gpu{gpu}: serving {name}")
        for exp in [e for e in JOB["experiments"] if e["model"] == name]:
            if time.time() - T0 > JOB["deadline_hours"] * 3600:
                log("deadline reached, skipping", exp["name"])
                continue
            path = HOME / "configs" / f"{exp['name']}.yaml"
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(yaml.safe_dump(exp, allow_unicode=True), encoding="utf-8")
            cli("run", str(path))
        server.terminate()
        server.wait()


def collect():
    out = WORK / "results"
    if out.exists():
        shutil.rmtree(out)
    shutil.copytree(HOME / "runs", out / "runs", dirs_exist_ok=True) if (HOME / "runs").exists() else None
    (out / "tasks").mkdir(parents=True, exist_ok=True)
    for p in (HOME / "tasks").glob("*.controls.json"):
        shutil.copy(p, out / "tasks" / p.name)
    if (HOME / "envs" / "logs").exists():
        shutil.copytree(HOME / "envs" / "logs", out / "env-logs", dirs_exist_ok=True)
    for p in pathlib.Path("/tmp").glob("llama-*.log"):
        shutil.copy(p, out / p.name)
    with tarfile.open(WORK / "results.tar.gz", "w:gz") as t:
        t.add(out, arcname="results")
    shutil.rmtree(out)
    log("results.tar.gz written")


def main():
    tarfile.open(fileobj=io.BytesIO(base64.b64decode(PAYLOAD)), mode="r:gz").extractall(SRC)
    sh("pip install -q uv")
    sh(f"uv pip install --system -q -e {SRC} mini-swe-agent "
       "'fedotmas @ git+https://github.com/ITMO-NSS-team/FEDOT.Swarm#subdirectory=packages/fedotmas'")
    (HOME / "tasks").mkdir(parents=True, exist_ok=True)
    for p in (SRC / "tasks").glob("*.jsonl"):
        shutil.copy(p, HOME / "tasks" / p.name)

    builder = threading.Thread(target=build_llama)
    builder.start()
    cli("build", JOB["taskset"], "--jobs", str(JOB.get("env_jobs", 4)))
    cli("controls", JOB["taskset"], "--jobs", str(JOB.get("env_jobs", 4)))
    fetch_models()
    builder.join()

    gpus = int(subprocess.run("nvidia-smi -L | wc -l", shell=True, capture_output=True, text=True).stdout or 1)
    names = list(JOB["models"])
    plan = {g: names[g::gpus] for g in range(gpus)}
    ups = [f"--upstream {n}=http://127.0.0.1:{8080 + g}/v1" for g, ns in plan.items() for n in ns]
    proxy = subprocess.Popen(" ".join([sys.executable, "-m", "aist_swarm.cli", "proxy", *ups, "--port", "8090"]),
                             shell=True, env=env())
    time.sleep(5)
    workers = [threading.Thread(target=gpu_worker, args=(g, 8080 + g, ns)) for g, ns in plan.items() if ns]
    for w in workers:
        w.start()
    for w in workers:
        w.join()
    proxy.terminate()
    collect()


if __name__ == "__main__":
    try:
        main()
    finally:
        if not (WORK / "results.tar.gz").exists():
            collect()
