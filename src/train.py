"""Baseline credit-risk model (XGBoost) trained ONLY on the Reference dataset."""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime

import mlflow
import numpy as np
import pandas as pd
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import train_test_split
from xgboost import XGBClassifier

from . import config
from .logging_utils import get_logger, stage
from .monitoring import setup_mlflow
from .validation import validate_batch

logger = get_logger()

PARAMS = dict(
    n_estimators=300,
    max_depth=4,
    learning_rate=0.05,
    subsample=0.8,
    colsample_bytree=0.8,
    min_child_weight=5,
    reg_lambda=1.0,
    eval_metric="auc",
    random_state=config.RANDOM_SEED,
    n_jobs=-1,
)


def _best_threshold(y: np.ndarray, proba: np.ndarray) -> float:
    grid = np.linspace(0.1, 0.7, 61)
    scores = [f1_score(y, proba >= t) for t in grid]
    return float(grid[int(np.argmax(scores))])


def classification_metrics(y: np.ndarray, proba: np.ndarray, threshold: float) -> dict[str, float]:
    pred = (proba >= threshold).astype(int)
    return {
        "auc": float(roc_auc_score(y, proba)),
        "accuracy": float(accuracy_score(y, pred)),
        "precision": float(precision_score(y, pred, zero_division=0)),
        "recall": float(recall_score(y, pred, zero_division=0)),
        "f1": float(f1_score(y, pred, zero_division=0)),
        "approval_rate": float((pred == 0).mean()),
        "predicted_default_rate": float(pred.mean()),
        "observed_default_rate": float(np.mean(y)),
    }


def load_model() -> tuple[XGBClassifier, dict]:
    model = XGBClassifier()
    model.load_model(config.MODEL_FILE)
    meta = json.loads(config.MODEL_META_FILE.read_text(encoding="utf-8"))
    return model, meta


def score(model: XGBClassifier, df: pd.DataFrame, threshold: float) -> pd.DataFrame:
    out = df.copy()
    out["prediction"] = model.predict_proba(df[config.FEATURES])[:, 1]
    out["prediction_label"] = (out["prediction"] >= threshold).astype(int)
    return out


def run_training() -> dict:
    config.ensure_dirs()
    reference = pd.read_csv(config.REFERENCE_FILE, dtype={config.TIME_COL: str})

    # Even the training data must honour the contract.
    result, reference = validate_batch(reference, batch="reference")
    if not result.passed:
        raise RuntimeError("Reference dataset violates the data contract; training aborted.")

    with stage(logger, "training", model="xgboost") as info:
        train_df, test_df = train_test_split(
            reference, test_size=0.2, stratify=reference[config.TARGET], random_state=config.RANDOM_SEED
        )
        model = XGBClassifier(**PARAMS)
        t0 = time.perf_counter()
        model.fit(train_df[config.FEATURES], train_df[config.TARGET])
        train_seconds = time.perf_counter() - t0

        proba_test = model.predict_proba(test_df[config.FEATURES])[:, 1]
        # Policy threshold is fixed (config.DECISION_THRESHOLD); the F1-optimal one is informative only.
        f1_optimal_threshold = _best_threshold(test_df[config.TARGET].to_numpy(), proba_test)
        threshold = config.DECISION_THRESHOLD
        metrics = classification_metrics(test_df[config.TARGET].to_numpy(), proba_test, threshold)

        model.save_model(config.MODEL_FILE)
        data_hash = hashlib.sha256(
            pd.util.hash_pandas_object(reference, index=False).values.tobytes()
        ).hexdigest()
        importances = dict(
            sorted(
                zip(config.FEATURES, map(float, model.feature_importances_), strict=True),
                key=lambda kv: kv[1],
                reverse=True,
            )
        )
        meta = {
            "model_name": "credit-scoring-xgb",
            "version": "1.0.0",
            "trained_at": datetime.now(UTC).isoformat(),
            "features": config.FEATURES,
            "excluded_protected_attributes": config.PROTECTED_ATTRIBUTES,
            "threshold": threshold,
            "threshold_policy": "fixed business policy (config.DECISION_THRESHOLD)",
            "f1_optimal_threshold": f1_optimal_threshold,
            "params": PARAMS,
            "reference_rows": len(reference),
            "reference_data_sha256": data_hash,
            "holdout_metrics": metrics,
            "feature_importance": importances,
        }
        config.MODEL_META_FILE.write_text(json.dumps(meta, indent=2), encoding="utf-8")

        # Reference predictions (needed by Evidently for prediction / performance drift)
        scored = score(model, reference, threshold)
        scored["split"] = np.where(scored.index.isin(test_df.index), "holdout", "train")
        scored.to_csv(config.REFERENCE_SCORED_FILE, index=False)

        setup_mlflow(config.MLFLOW_TRAINING_EXPERIMENT)
        with mlflow.start_run(run_name="baseline-xgb"):
            mlflow.set_tags(
                {
                    "stage": "training",
                    "data_sha256": data_hash[:16],
                    "protected_attributes_excluded": ",".join(config.PROTECTED_ATTRIBUTES),
                }
            )
            mlflow.log_params({**PARAMS, "threshold": threshold, "n_features": len(config.FEATURES)})
            mlflow.log_metrics({f"holdout_{k}": v for k, v in metrics.items()})
            mlflow.log_metric("train_seconds", train_seconds)
            mlflow.log_metric("f1_optimal_threshold", f1_optimal_threshold)
            mlflow.log_artifact(str(config.MODEL_FILE), artifact_path="model")
            mlflow.log_artifact(str(config.MODEL_META_FILE), artifact_path="model")
            mlflow.log_dict(importances, "feature_importance.json")

        info.update(
            rows_train=len(train_df),
            rows_holdout=len(test_df),
            threshold=threshold,
            auc=round(metrics["auc"], 4),
            f1=round(metrics["f1"], 4),
        )
    return meta


if __name__ == "__main__":
    run_training()
