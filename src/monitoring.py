"""Observability helpers: MLflow tracking, alert rules and Prometheus metric export."""
from __future__ import annotations

import json
import logging
from typing import Any

import mlflow
from prometheus_client import CollectorRegistry, Gauge, write_to_textfile

from . import config
from .logging_utils import get_logger, log_event

logger = get_logger()


def setup_mlflow(experiment: str) -> None:
    mlflow.set_tracking_uri(config.MLFLOW_TRACKING_URI)
    if mlflow.get_experiment_by_name(experiment) is None:
        mlflow.create_experiment(experiment, artifact_location=config.MLFLOW_ARTIFACT_ROOT)
    mlflow.set_experiment(experiment)


# --- Alert rules ---------------------------------------------------------------------------------

def evaluate_alerts(batch: dict[str, Any], baseline: dict[str, float]) -> list[dict[str, Any]]:
    """Turn a batch monitoring record into a list of alerts (severity, rule, message)."""
    alerts: list[dict[str, Any]] = []
    month = batch["batch"]

    def add(severity: str, rule: str, message: str, value: Any = None) -> None:
        alerts.append({"batch": month, "severity": severity, "rule": rule,
                       "message": message, "value": value})

    if not batch.get("validation_passed", True):
        add("CRITICAL", "data_contract", "Lote bloqueado pelo contrato de dados",
            batch.get("failed_rules"))
        return alerts

    for feat, psi in batch.get("psi", {}).items():
        if psi >= config.PSI_CRITICAL:
            add("CRITICAL", "feature_drift_psi", f"Drift forte em '{feat}' (PSI={psi:.3f})", psi)
        elif psi >= config.PSI_WARNING:
            add("WARNING", "feature_drift_psi", f"Drift moderado em '{feat}' (PSI={psi:.3f})", psi)

    share = batch.get("drift_share", 0.0)
    if share >= config.DRIFT_SHARE_CRITICAL:
        add("CRITICAL", "dataset_drift", f"{share:.0%} das features com drift crítico", share)

    pred_psi = batch.get("prediction_psi", 0.0)
    if pred_psi >= config.PSI_CRITICAL:
        add("CRITICAL", "prediction_drift", f"Distribuição do score mudou (PSI={pred_psi:.3f})", pred_psi)
    elif pred_psi >= config.PSI_WARNING:
        add("WARNING", "prediction_drift", f"Score em mudança (PSI={pred_psi:.3f})", pred_psi)

    auc = batch.get("auc")
    if auc is not None and baseline.get("auc") is not None:
        drop = baseline["auc"] - auc
        if drop >= config.AUC_DROP_CRITICAL:
            add("CRITICAL", "concept_drift_performance",
                f"AUC caiu {drop:.3f} vs baseline ({baseline['auc']:.3f} -> {auc:.3f})", drop)

    dr = batch.get("observed_default_rate")
    if dr is not None and baseline.get("observed_default_rate") is not None:
        shift = dr - baseline["observed_default_rate"]
        if abs(shift) >= config.DEFAULT_RATE_SHIFT_WARNING:
            add("WARNING", "label_drift", f"Taxa de default mudou {shift:+.1%}", shift)

    di = batch.get("disparate_impact")
    if di is not None and di < config.DISPARATE_IMPACT_MIN:
        add("WARNING", "fairness", f"Disparate impact {di:.2f} < {config.DISPARATE_IMPACT_MIN}", di)

    return alerts


def emit_alerts(alerts: list[dict[str, Any]]) -> None:
    for a in alerts:
        level = logging.CRITICAL if a["severity"] == "CRITICAL" else logging.WARNING
        log_event(logger, level, a["message"], stage="alerting", alert=a["rule"],
                  severity=a["severity"], batch=a["batch"])


# --- Prometheus ----------------------------------------------------------------------------------

def export_prometheus(records: list[dict[str, Any]], alerts: list[dict[str, Any]]) -> None:
    """Write metrics in Prometheus text format (node_exporter textfile collector compatible)."""
    reg = CollectorRegistry()
    g_status = Gauge("credit_pipeline_batch_validation_passed", "1 if batch passed the contract",
                     ["batch"], registry=reg)
    g_rows = Gauge("credit_pipeline_rows_processed", "Rows in batch", ["batch"], registry=reg)
    g_lat = Gauge("credit_model_inference_latency_ms", "Batch inference latency (ms)",
                  ["batch"], registry=reg)
    g_share = Gauge("credit_data_drift_share", "Share of features with critical drift",
                    ["batch"], registry=reg)
    g_psi = Gauge("credit_feature_psi", "PSI per feature", ["batch", "feature"], registry=reg)
    g_auc = Gauge("credit_model_auc", "AUC when labels are available", ["batch"], registry=reg)
    g_alerts = Gauge("credit_alerts_total", "Alerts by severity", ["severity"], registry=reg)

    for r in records:
        b = r["batch"]
        g_status.labels(b).set(1 if r.get("validation_passed") else 0)
        g_rows.labels(b).set(r.get("rows", 0))
        if r.get("validation_passed"):
            g_lat.labels(b).set(r.get("inference_latency_ms", 0))
            g_share.labels(b).set(r.get("drift_share", 0))
            for f, v in r.get("psi", {}).items():
                g_psi.labels(b, f).set(v)
            if r.get("auc") is not None:
                g_auc.labels(b).set(r["auc"])
    for sev in ("WARNING", "CRITICAL"):
        g_alerts.labels(sev).set(sum(a["severity"] == sev for a in alerts))
    write_to_textfile(str(config.REPORTS_DIR / "metrics.prom"), reg)


def save_json(obj: Any, name: str) -> None:
    (config.REPORTS_DIR / name).write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
