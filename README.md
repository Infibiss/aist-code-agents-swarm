# aist-swarm

Fit and run swarms of small local language models for bug fixing under a hardware budget, and measure honestly
whether they beat one larger model on the same machine. Research stand of the AIST lab; plan in [PLAN.md](PLAN.md).

## What is here

| Module | Role |
|---|---|
| `tasks` | Bug-fix tasks in the SWE-bench shape (repo at a commit, issue, hidden tests) |
| `sandbox` | Task environments in Docker volumes; every attempt is a fresh offline container |
| `judge` | Applies a patch and the hidden test patch, runs FAIL_TO_PASS / PASS_TO_PASS |
| `proxy` | Metering proxy for any OpenAI-compatible server: tokens, calls, time per run and role, budgets |
| `systems` | Systems under test; `miniswe` is the mini-SWE-agent baseline |
| `runner` | Experiments from YAML: tasks × attempts → sandbox → system → judge |
| `traces`, `web` | Normalized run timelines and the API behind the viewer in `web/` |

## Quick start (local GPU)

```bash
uv sync
uv run aist-swarm import <tasks.jsonl> rebench --env-json <env.json>   # SWE-bench style rows
uv run aist-swarm build rebench --jobs 3                               # environments, needs network
uv run aist-swarm controls rebench                                     # gold must pass, empty must fail

var/bin/llama/llama-server -m var/models/Qwen3.5-0.8B-Q8_0.gguf --alias qwen3.5-0.8b-q8 -ngl 99 -c 32768 -np 2 --jinja --port 8080
uv run aist-swarm proxy --upstream http://127.0.0.1:8080/v1
uv run aist-swarm run configs/e001-qwen08-single.yaml

uv run aist-swarm ui            # API on :8700, built viewer from web/dist
cd web && npm install && npm run dev   # viewer in dev mode on :5173
```

Docker Hub and deb.debian.org are slow from some networks: the sandbox image uses `mirror.gcr.io` and
`mirror.yandex.ru` by default (see `docker/sandbox.Dockerfile`).
