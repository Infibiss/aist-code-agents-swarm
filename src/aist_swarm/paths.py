"""Where runtime data lives. Everything heavy goes under one root (default `<repo>/var`), set by AIST_SWARM_HOME."""

import os
from pathlib import Path

HOME = Path(os.environ.get("AIST_SWARM_HOME", Path(__file__).resolve().parents[2] / "var"))
TASKS = HOME / "tasks"
ENVS = HOME / "envs"
RUNS = HOME / "runs"
MODELS = HOME / "models"
