"""Observability helpers: MLflow tracking, alert rules and Prometheus metric export."""
from __future__ import annotations

import json
import logging
import os
from typing import Any

import mlflow
from prometheus_client import CollectorRegistry, Gauge, push_to_gateway, write_to_textfile

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

def build_registry(records: list[dict[str, Any]], alerts: list[dict[str, Any]],
                   baseline: dict[str, float]) -> CollectorRegistry:
    """Translate monitoring records into Prometheus gauges (one label set per batch)."""
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
    g_rule = Gauge("credit_contract_violations", "Contract violations by rule",
                   ["batch", "rule"], registry=reg)
    g_pred = Gauge("credit_prediction_psi", "PSI of the model score vs reference",
                   ["batch"], registry=reg)
    g_di = Gauge("credit_disparate_impact", "Approval-rate ratio between sex groups",
                 ["batch"], registry=reg)
    g_appr = Gauge("credit_approval_rate", "Share of approved applications", ["batch"], registry=reg)
    g_dr = Gauge("credit_observed_default_rate", "Observed default rate (delayed labels)",
                 ["batch"], registry=reg)
    g_base = Gauge("credit_model_baseline", "Reference holdout metrics", ["metric"], registry=reg)
    g_last = Gauge("credit_pipeline_last_success_timestamp_seconds",
                   "Unix time of the last successful pipeline run", registry=reg)

    for r in records:
        b = r["batch"]
        g_status.labels(b).set(1 if r.get("validation_passed") else 0)
        g_rows.labels(b).set(r.get("rows", 0))
        for rule, n in (r.get("failed_rules") or {}).items():
            g_rule.labels(b, rule).set(n)
        if r.get("validation_passed"):
            g_lat.labels(b).set(r.get("inference_latency_ms", 0))
            g_share.labels(b).set(r.get("drift_share", 0))
            g_pred.labels(b).set(r.get("prediction_psi", 0))
            g_appr.labels(b).set(r.get("approval_rate", 0))
            for f, v in r.get("psi", {}).items():
                g_psi.labels(b, f).set(v)
            if r.get("auc") is not None:
                g_auc.labels(b).set(r["auc"])
            if r.get("observed_default_rate") is not None:
                g_dr.labels(b).set(r["observed_default_rate"])
            if r.get("disparate_impact") is not None:
                g_di.labels(b).set(r["disparate_impact"])
    for sev in ("WARNING", "CRITICAL"):
        g_alerts.labels(sev).set(sum(a["severity"] == sev for a in alerts))
    for k in ("auc", "observed_default_rate", "approval_rate"):
        if baseline.get(k) is not None:
            g_base.labels(k).set(baseline[k])
    g_last.set_to_current_time()
    return reg


def export_prometheus(records: list[dict[str, Any]], alerts: list[dict[str, Any]],
                      baseline: dict[str, float]) -> None:
    """Write metrics in Prometheus text format and, if configured, push them to a Pushgateway.

    Batch jobs do not live long enough to be scraped, so the standard pattern is to push the
    final state to a Pushgateway that Prometheus scrapes (see docker-compose.yml).
    """
    reg = build_registry(records, alerts, baseline)
    write_to_textfile(str(config.REPORTS_DIR / "metrics.prom"), reg)
    gateway = os.getenv(config.PUSHGATEWAY_ENV)
    if gateway:
        try:
            push_to_gateway(gateway, job="sentrix_pipeline", registry=reg, timeout=5)
            log_event(logger, logging.INFO, "metrics pushed", stage="prometheus_push",
                      status="success", gateway=gateway)
        except OSError as exc:  # monitoring must never break the pipeline
            log_event(logger, logging.WARNING, f"pushgateway unreachable: {exc}",
                      stage="prometheus_push", status="failed", gateway=gateway)


def save_json(obj: Any, name: str) -> None:
    (config.REPORTS_DIR / name).write_text(
        json.dumps(obj, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
