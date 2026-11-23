"""End-to-end sustainment pipeline.

    python -m src.pipeline            # full run: ingest -> train -> simulate -> monitor -> report
    python -m src.pipeline --monitor  # only monitoring over existing batches

For every production month:
  1. Data contract gate (Pandera)     -> blocks & quarantines bad batches
  2. Batch inference                   -> latency / volume metrics
  3. Drift detection (PSI, KS, Evidently HTML)
  4. Performance with delayed labels   -> concept drift evidence
  5. Fairness audit (disparate impact)
  6. Alert rules -> structured logs, MLflow, Prometheus textfile
Then: causal analysis + consolidated monitoring dashboard.
"""
from __future__ import annotations

import argparse
import logging
import time

import mlflow
import pandas as pd

from . import config
from .causal_analysis import run_causal_analysis
from .dashboard import build_dashboard
from .drift_detection import evidently_report, feature_drift_table, psi
from .drift_simulation import batch_path, run_simulation
from .fairness import disparate_impact, load_protected
from .ingestion import run_ingestion
from .logging_utils import RUN_ID, get_logger, log_event, stage
from .monitoring import emit_alerts, evaluate_alerts, export_prometheus, save_json, setup_mlflow
from .train import classification_metrics, load_model, run_training, score
from .validation import make_corrupted_batch, validate_batch

logger = get_logger()


def monitor_batch(name: str, df: pd.DataFrame, model, meta: dict, reference: pd.DataFrame,
                  holdout: pd.DataFrame, protected: pd.DataFrame, step: int) -> dict:
    record: dict = {"batch": name, "step": step, "rows": len(df),
                    "kind": "corrupted" if "upstream" in name else "production"}

    result, valid = validate_batch(df, batch=name)
    record.update(validation_passed=result.passed, failed_rules=result.failed_rules,
                  validation_failures=result.n_failures)
    if valid is None:
        return record

    with stage(logger, "inference", batch=name, rows=len(valid)) as info:
        t0 = time.perf_counter()
        scored = score(model, valid, meta["threshold"])
        latency = (time.perf_counter() - t0) * 1000
        info.update(latency_ms=round(latency, 2))
    record.update(inference_latency_ms=round(latency, 2),
                  latency_ms_per_1k_rows=round(latency / len(valid) * 1000, 3),
                  mean_pd=float(scored["prediction"].mean()),
                  approval_rate=float((scored["prediction_label"] == 0).mean()))

    with stage(logger, "drift_detection", batch=name) as info:
        table = feature_drift_table(reference, scored)
        table.to_csv(config.REPORTS_DIR / "drift_tables" / f"drift_{name}.csv", index=False)
        record["psi"] = dict(zip(table["feature"], table["psi"]))
        record["ks_pvalue"] = {r.feature: r.ks_pvalue for r in table.itertuples() if r.ks_pvalue is not None}
        record["ks_statistic"] = {r.feature: r.ks_statistic for r in table.itertuples() if r.ks_statistic is not None}
        critical = table.loc[table["severity"] == "critical", "feature"].tolist()
        warning = table.loc[table["severity"] == "warning", "feature"].tolist()
        record.update(drifted_features=critical, warning_features=warning,
                      n_drifted_features=len(critical),
                      drift_share=len(critical) / len(config.FEATURES),
                      prediction_psi=psi(reference["prediction"], scored["prediction"]),
                      target_psi=psi(reference[config.TARGET], scored[config.TARGET], categorical=True))
        html = config.EVIDENTLY_DIR / f"drift_report_{name}.html"
        record.update(evidently_report(holdout, scored, html, with_labels=True))
        record["evidently_report"] = html.relative_to(config.ROOT).as_posix()
        info.update(n_drifted=len(critical), drift_share=round(record["drift_share"], 3),
                    prediction_psi=round(record["prediction_psi"], 4))

    perf = classification_metrics(scored[config.TARGET].to_numpy(), scored["prediction"].to_numpy(),
                                  meta["threshold"])
    record.update({k: perf[k] for k in ("auc", "f1", "recall", "precision", "observed_default_rate")})
    fair = disparate_impact(scored, protected)
    record.update(disparate_impact=fair["disparate_impact"],
                  approval_by_sex=fair["approval_rate_by_group"])
    return record


def log_to_mlflow(record: dict, alerts: list[dict]) -> None:
    with mlflow.start_run(run_name=f"batch-{record['batch']}", nested=True):
        mlflow.set_tags({"batch": record["batch"], "kind": record["kind"],
                         "validation_passed": str(record["validation_passed"])})
        scalars = {k: float(v) for k, v in record.items()
                   if isinstance(v, (int, float)) and not isinstance(v, bool) and k != "step"}
        scalars["validation_passed"] = float(record["validation_passed"])
        scalars["alerts_critical"] = float(sum(a["severity"] == "CRITICAL" for a in alerts))
        scalars["alerts_warning"] = float(sum(a["severity"] == "WARNING" for a in alerts))
        mlflow.log_metrics(scalars)
        for feat, v in record.get("psi", {}).items():
            mlflow.log_metric(f"psi_{feat}", v)
        mlflow.log_dict({"record": record, "alerts": alerts}, "monitoring_record.json")
        if record.get("evidently_report"):
            mlflow.log_artifact(str(config.ROOT / record["evidently_report"]), "evidently")
    # Timeline on the parent run (step = month index) -> charts in the MLflow UI
    for key in ("drift_share", "auc", "prediction_psi", "approval_rate", "rows"):
        if key in record:
            mlflow.log_metric(f"timeline_{key}", float(record[key]), step=record["step"])
    mlflow.log_metric("timeline_validation_passed", float(record["validation_passed"]),
                      step=record["step"])


def run_monitoring() -> dict:
    config.ensure_dirs()
    (config.REPORTS_DIR / "drift_tables").mkdir(parents=True, exist_ok=True)
    model, meta = load_model()
    reference = pd.read_csv(config.REFERENCE_SCORED_FILE, dtype={config.TIME_COL: str})
    holdout = reference[reference["split"] == "holdout"]
    protected = load_protected()
    baseline = meta["holdout_metrics"]

    batches: list[tuple[str, pd.DataFrame]] = []
    for spec in config.DRIFT_SCHEDULE:
        m = spec["month"]
        df = pd.read_csv(batch_path(m), dtype={config.TIME_COL: str})
        batches.append((m, df))
        if m == config.CORRUPTED_BATCH_SOURCE_MONTH:
            batches.append((f"{m}_upstream_bug", make_corrupted_batch(df)))

    records, all_alerts = [], []
    setup_mlflow(config.MLFLOW_MONITORING_EXPERIMENT)
    with mlflow.start_run(run_name=f"monitoring-{RUN_ID}"):
        mlflow.set_tags({"pipeline_run_id": RUN_ID, "model_version": meta["version"]})
        mlflow.log_dict(meta["holdout_metrics"], "baseline_metrics.json")
        for step, (name, df) in enumerate(batches):
            with stage(logger, "batch_monitoring", batch=name) as info:
                record = monitor_batch(name, df, model, meta, reference, holdout, protected, step)
                alerts = evaluate_alerts(record, baseline)
                emit_alerts(alerts)
                record["n_alerts_critical"] = sum(a["severity"] == "CRITICAL" for a in alerts)
                record["n_alerts_warning"] = sum(a["severity"] == "WARNING" for a in alerts)
                log_to_mlflow(record, alerts)
                records.append(record)
                all_alerts.extend(alerts)
                info.update(validation_passed=record["validation_passed"],
                            alerts_critical=record["n_alerts_critical"],
                            status="success" if record["validation_passed"] else "blocked")

        causal = run_causal_analysis(model, meta["threshold"])
        save_json(records, "monitoring_records.json")
        save_json(all_alerts, "alerts.json")
        save_json(causal, "causal_analysis.json")
        export_prometheus(records, all_alerts)
        with stage(logger, "dashboard"):
            path = build_dashboard(records, all_alerts, causal, meta)
        for f in ("monitoring_records.json", "alerts.json", "causal_analysis.json", "metrics.prom"):
            mlflow.log_artifact(str(config.REPORTS_DIR / f), "reports")
        mlflow.log_artifact(str(path), "reports")

    log_event(logger, logging.INFO, "pipeline finished", stage="pipeline", status="success",
              batches=len(records), alerts=len(all_alerts), dashboard=str(path))
    return {"records": records, "alerts": all_alerts, "causal": causal}


def main() -> None:
    parser = argparse.ArgumentParser(description="Credit scoring sustainment pipeline")
    parser.add_argument("--monitor", action="store_true", help="only run monitoring stage")
    args = parser.parse_args()
    with stage(logger, "pipeline", run_id=RUN_ID):
        if not args.monitor:
            run_ingestion()
            run_training()
            run_simulation()
        run_monitoring()


if __name__ == "__main__":
    main()
