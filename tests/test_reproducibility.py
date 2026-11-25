from src.reproducibility import compare, compare_alerts


def _rec(batch="2025-12", auc=0.683, psi_age=0.33, approval=0.526, passed=True):
    r = {
        "batch": batch,
        "rows": 2490,
        "validation_passed": passed,
        "failed_rules": {},
        "n_drifted_features": 7,
    }
    if passed:
        r.update(psi={"age": psi_age}, auc=auc, prediction_psi=0.36, approval_rate=approval, mean_pd=0.287)
    return r


def test_model_noise_within_tolerance_is_a_note_not_an_error():
    errors, notes = compare([_rec()], [_rec(auc=0.688)])
    assert errors == [] and any("auc" in n for n in notes)


def test_data_level_change_is_an_error():
    errors, _ = compare([_rec()], [_rec(psi_age=0.34)])
    assert any("PSI[age]" in e for e in errors)


def test_approval_swing_beyond_tolerance_is_an_error():
    errors, _ = compare([_rec()], [_rec(approval=0.59)])
    assert any("approval_rate" in e for e in errors)


def test_alert_flip_on_model_rule_is_note_but_on_data_rule_is_error():
    base = [{"batch": "2025-11", "rule": "prediction_drift", "severity": "WARNING"}]
    cur = [{"batch": "2025-11", "rule": "prediction_drift", "severity": "CRITICAL"}]
    errors, notes = compare_alerts(base, cur)
    assert errors == [] and len(notes) == 2
    errors, _ = compare_alerts([{"batch": "x", "rule": "data_contract", "severity": "CRITICAL"}], [])
    assert errors
