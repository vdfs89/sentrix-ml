"""Causal / root-cause analysis of the degradation via counterfactual interventions.

Because the simulation keeps the original (pre-drift) version of every production batch, we
can apply do-interventions: restore a group of features (or the labels) to their original
values while keeping everything else drifted, and measure how much of the observed change
disappears. That isolates *which mechanism* caused *which symptom*:

    Economic shock (inflation, new customer profile)
        |-- inflation  --> bill_amt UP, pay_amt DOWN --------------> score shift (data drift)
        |-- new profile --> age DOWN, limit_bal DOWN --------------> score shift (data drift)
        '-- higher utilisation --> good payers default ------------> P(y|X) change (concept drift)
                                                                      => AUC drop

Effects reported per month:
* score_shift_total: mean predicted PD (drifted) - mean predicted PD (original)
* score_shift_due_to_<group>: part of that shift removed when the group is restored
* auc_drop_data_drift: AUC(original X, original y) - AUC(drifted X, original y)
* auc_drop_concept_drift: AUC(drifted X, original y) - AUC(drifted X, drifted y)
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from sklearn.metrics import roc_auc_score

from . import config
from .drift_simulation import batch_path, original_path
from .logging_utils import get_logger, stage

logger = get_logger()

GROUPS = {
    "inflation (bill_amt, pay_amt)": config.BILL_AMT + config.PAY_AMT,
    "novo perfil (age, limit_bal)": ["age", "limit_bal"],
}


def _proba(model, df: pd.DataFrame) -> np.ndarray:
    return model.predict_proba(df[config.FEATURES])[:, 1]


def analyse_month(model, threshold: float, month: str) -> dict:
    orig = pd.read_csv(original_path(month))
    drift = pd.read_csv(batch_path(month))
    p_orig, p_drift = _proba(model, orig), _proba(model, drift)
    res: dict = {
        "month": month,
        "mean_pd_original": float(p_orig.mean()),
        "mean_pd_drifted": float(p_drift.mean()),
        "approval_rate_original": float((p_orig < threshold).mean()),
        "approval_rate_drifted": float((p_drift < threshold).mean()),
        "score_shift_total": float(p_drift.mean() - p_orig.mean()),
    }
    for name, cols in GROUPS.items():
        restored = drift.copy()
        restored[cols] = orig[cols].to_numpy()
        p_rest = _proba(model, restored)
        res[f"score_shift_due_to::{name}"] = float(p_drift.mean() - p_rest.mean())

    y_orig, y_drift = orig[config.TARGET], drift[config.TARGET]
    auc_oo = roc_auc_score(y_orig, p_orig)
    auc_do = roc_auc_score(y_orig, p_drift)
    auc_dd = roc_auc_score(y_drift, p_drift)
    res.update({
        "auc_original": float(auc_oo),
        "auc_drifted_x_original_y": float(auc_do),
        "auc_observed": float(auc_dd),
        "auc_drop_data_drift": float(auc_oo - auc_do),
        "auc_drop_concept_drift": float(auc_do - auc_dd),
        "default_rate_original": float(y_orig.mean()),
        "default_rate_observed": float(y_drift.mean()),
        # Defaults the model approved (missed risk) — the business cost of silent degradation
        "missed_defaults_original": int(((p_orig < threshold) & (y_orig == 1)).sum()),
        "missed_defaults_observed": int(((p_drift < threshold) & (y_drift == 1)).sum()),
    })
    return res


def run_causal_analysis(model, threshold: float) -> list[dict]:
    with stage(logger, "causal_analysis") as info:
        out = [analyse_month(model, threshold, s["month"]) for s in config.DRIFT_SCHEDULE]
        last = out[-1]
        info.update(month=last["month"],
                    auc_drop_data=round(last["auc_drop_data_drift"], 4),
                    auc_drop_concept=round(last["auc_drop_concept_drift"], 4))
    return out
