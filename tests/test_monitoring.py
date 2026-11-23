from prometheus_client import generate_latest

from src.monitoring import build_registry


def test_registry_exposes_metrics_used_by_alert_rules():
    records = [
        {"batch": "2025-12", "validation_passed": True, "rows": 2490, "inference_latency_ms": 6.0,
         "drift_share": 0.33, "prediction_psi": 0.36, "approval_rate": 0.53, "auc": 0.68,
         "observed_default_rate": 0.38, "disparate_impact": 0.94, "psi": {"age": 0.33}},
        {"batch": "bug", "validation_passed": False, "rows": 2510, "failed_rules": {"R1": 24}},
    ]
    alerts = [{"severity": "CRITICAL"}, {"severity": "WARNING"}]
    text = generate_latest(build_registry(records, alerts, {"auc": 0.78})).decode()

    # every metric referenced in monitoring/prometheus/alerts.yml must be exported
    for metric in ("credit_pipeline_batch_validation_passed", "credit_feature_psi",
                   "credit_data_drift_share", "credit_prediction_psi", "credit_model_auc",
                   "credit_model_baseline", "credit_observed_default_rate",
                   "credit_disparate_impact", "credit_pipeline_last_success_timestamp_seconds"):
        assert metric in text, metric
    assert 'credit_pipeline_batch_validation_passed{batch="bug"} 0.0' in text
    assert 'credit_contract_violations{batch="bug",rule="R1"} 24.0' in text
    assert 'credit_feature_psi{batch="2025-12",feature="age"} 0.33' in text
    assert 'credit_model_baseline{metric="auc"} 0.78' in text
