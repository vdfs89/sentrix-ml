import numpy as np
import pandas as pd

from src import config
from src.drift_detection import ks, psi, severity
from src.drift_simulation import apply_concept_drift, apply_data_drift
from src.ingestion import pseudonymize
from src.monitoring import evaluate_alerts


def test_psi_zero_for_identical_distributions():
    s = pd.Series(np.random.default_rng(0).normal(size=5_000))
    assert psi(s, s) < 1e-6


def test_psi_detects_shift_and_ks_agrees():
    rng = np.random.default_rng(1)
    ref = pd.Series(rng.lognormal(10, 1, 10_000))
    cur = pd.Series(rng.lognormal(10, 1, 3_000) * 2.5)
    assert severity(psi(ref, cur)) == "critical"
    stat, p = ks(ref, cur)
    assert p < 0.05 and stat > 0.2


def test_psi_categorical():
    ref = pd.Series([0] * 800 + [1] * 200)
    cur = pd.Series([0] * 500 + [1] * 500)
    assert psi(ref, cur, categorical=True) > config.PSI_CRITICAL


def _frame(n=1000):
    rng = np.random.default_rng(2)
    df = pd.DataFrame({c: rng.integers(1_000, 50_000, n).astype(float) for c in config.BILL_AMT + config.PAY_AMT})
    df["limit_bal"] = 100_000.0
    df["age"] = 40
    df["pay_0"] = 0
    df[config.TARGET] = 0
    return df


def test_data_drift_moves_intended_features():
    df = _frame()
    out, n_new = apply_data_drift(df, inflation=0.5, new_profile_share=0.3, rng=np.random.default_rng(3))
    assert out[config.BILL_AMT].mean().mean() > df[config.BILL_AMT].mean().mean() * 1.4
    assert out[config.PAY_AMT].mean().mean() < df[config.PAY_AMT].mean().mean()
    assert n_new == 300 and (out["age"] < 30).sum() == 300


def test_concept_drift_only_flips_at_risk_good_payers():
    df = _frame()
    df["bill_amt1"] = 80_000.0  # utilisation 0.8 -> at risk
    out, flips = apply_concept_drift(df, flip_prob=1.0, rng=np.random.default_rng(4))
    assert flips == len(df) and out[config.TARGET].eq(1).all()


def test_pseudonymization_is_deterministic_and_irreversible_looking():
    a = pseudonymize(pd.Series(["12345678900"]))[0]
    b = pseudonymize(pd.Series(["12345678900"]))[0]
    assert a == b and "12345678900" not in a and len(a) == 20


def test_alert_rules():
    batch = {"batch": "x", "validation_passed": True, "psi": {"age": 0.3, "pay_amt1": 0.12},
             "drift_share": 0.4, "prediction_psi": 0.05, "auc": 0.70,
             "observed_default_rate": 0.35, "disparate_impact": 0.95}
    alerts = evaluate_alerts(batch, {"auc": 0.78, "observed_default_rate": 0.22})
    rules = {(a["rule"], a["severity"]) for a in alerts}
    assert ("feature_drift_psi", "CRITICAL") in rules
    assert ("feature_drift_psi", "WARNING") in rules
    assert ("dataset_drift", "CRITICAL") in rules
    assert ("concept_drift_performance", "CRITICAL") in rules
    assert ("label_drift", "WARNING") in rules
