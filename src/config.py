"""Central configuration: paths, feature lists, thresholds and drift schedule."""

from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = ROOT / "data"
RAW_DIR = DATA_DIR / "raw"
REFERENCE_DIR = DATA_DIR / "reference"
PRODUCTION_DIR = DATA_DIR / "production"
QUARANTINE_DIR = DATA_DIR / "quarantine"
AUDIT_DIR = DATA_DIR / "audit"  # restricted area: protected attributes for fairness audit only

MODELS_DIR = ROOT / "models"
REPORTS_DIR = ROOT / "reports"
EVIDENTLY_DIR = REPORTS_DIR / "evidently"
LOGS_DIR = ROOT / "logs"

RAW_FILE = RAW_DIR / "credit_default_raw.csv"
REFERENCE_FILE = REFERENCE_DIR / "reference.csv"
REFERENCE_SCORED_FILE = REFERENCE_DIR / "reference_scored.csv"
PRODUCTION_POOL_FILE = PRODUCTION_DIR / "production_pool.csv"
MODEL_FILE = MODELS_DIR / "credit_model.json"
MODEL_META_FILE = MODELS_DIR / "model_metadata.json"
PIPELINE_LOG_FILE = LOGS_DIR / "pipeline.jsonl"

# UCI "Default of Credit Card Clients" (Taiwan, 2005) — OpenML id 42477, 30.000 linhas.
OPENML_DATA_ID = 42477

RANDOM_SEED = 42
REFERENCE_SIZE = 15_000  # first half: training era (reference)
N_PRODUCTION_BATCHES = 6
REFERENCE_START = "2025-01-01"
PRODUCTION_START = "2025-07-01"

# MLflow (local, file-free sqlite backend)
MLFLOW_TRACKING_URI = os.getenv("MLFLOW_TRACKING_URI", f"sqlite:///{(ROOT / 'mlflow.db').as_posix()}")
MLFLOW_ARTIFACT_ROOT = (ROOT / "mlartifacts").as_uri()
MLFLOW_TRAINING_EXPERIMENT = "sentrix-training"
MLFLOW_MONITORING_EXPERIMENT = "sentrix-monitoring"

# Pseudonymisation salt (LGPD art. 13 §4). In production comes from a secret manager.
PII_SALT_ENV = "CREDIT_PII_SALT"

# Prometheus Pushgateway (e.g. http://localhost:9091). Unset = only write reports/metrics.prom
PUSHGATEWAY_ENV = "SENTRIX_PUSHGATEWAY"
DEFAULT_DEV_SALT = "dev-only-salt-change-me"

TARGET = "default"
ID_COL = "customer_key"
TIME_COL = "reference_month"

# Protected / sensitive attributes: NEVER used as model features (minimisation + non-discrimination).
PROTECTED_ATTRIBUTES = ["sex", "marriage"]
# Direct identifiers (synthetic): removed after pseudonymisation.
DIRECT_IDENTIFIERS = ["cpf", "customer_id"]

PAY_STATUS = ["pay_0", "pay_2", "pay_3", "pay_4", "pay_5", "pay_6"]
BILL_AMT = [f"bill_amt{i}" for i in range(1, 7)]
PAY_AMT = [f"pay_amt{i}" for i in range(1, 7)]
FEATURES = ["limit_bal", "education", "age", *PAY_STATUS, *BILL_AMT, *PAY_AMT]
CATEGORICAL_FEATURES = ["education", *PAY_STATUS]
NUMERICAL_FEATURES = [c for c in FEATURES if c not in CATEGORICAL_FEATURES]

# --- Drift / alert thresholds ---------------------------------------------------------------
PSI_WARNING = 0.10
PSI_CRITICAL = 0.20
KS_PVALUE = 0.05
DRIFT_SHARE_CRITICAL = 0.30  # share of features with PSI >= PSI_CRITICAL
AUC_DROP_CRITICAL = 0.05  # absolute AUC drop vs. reference holdout
DEFAULT_RATE_SHIFT_WARNING = 0.05  # absolute shift in observed default rate (label drift)
DISPARATE_IMPACT_MIN = 0.80  # four-fifths rule for fairness audit

# --- Simulated economic scenario (one entry per production month) ---------------------------
# inflation: bills grow, real payments shrink | new_profile_share: influx of young, low-limit clients
# concept_flip: P(good payer with high utilisation becomes defaulter) -> changes P(y|X)
DRIFT_SCHEDULE = [
    {"month": "2025-07", "inflation": 0.00, "new_profile_share": 0.00, "concept_flip": 0.00},
    {"month": "2025-08", "inflation": 0.00, "new_profile_share": 0.00, "concept_flip": 0.00},
    {"month": "2025-09", "inflation": 0.15, "new_profile_share": 0.10, "concept_flip": 0.00},
    {"month": "2025-10", "inflation": 0.40, "new_profile_share": 0.20, "concept_flip": 0.10},
    {"month": "2025-11", "inflation": 0.70, "new_profile_share": 0.30, "concept_flip": 0.25},
    {"month": "2025-12", "inflation": 1.00, "new_profile_share": 0.40, "concept_flip": 0.40},
]

# Month whose clean batch is copied and corrupted to prove the contract blocks bad ingestion.
CORRUPTED_BATCH_SOURCE_MONTH = "2025-09"


def ensure_dirs() -> None:
    for d in (
        RAW_DIR,
        REFERENCE_DIR,
        PRODUCTION_DIR,
        QUARANTINE_DIR,
        AUDIT_DIR,
        MODELS_DIR,
        REPORTS_DIR,
        EVIDENTLY_DIR,
        LOGS_DIR,
    ):
        d.mkdir(parents=True, exist_ok=True)
