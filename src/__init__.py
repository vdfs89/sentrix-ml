"""SENTRIX — Continuous ML Observability & Governance for Credit Risk."""

import os

# Project version (SemVer), kept in sync with pyproject.toml and the git tags.
# The model has its own version in models/model_metadata.json.
__version__ = "1.5.0"

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
