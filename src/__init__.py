"""SENTRIX — Continuous ML Observability & Governance for Credit Risk."""
import os

__version__ = "1.1.0"  # project version (SemVer). The model has its own version in models/model_metadata.json.

os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
