"""Statistical drift detection: PSI + Kolmogorov-Smirnov (own implementation) and Evidently AI.

* PSI (Population Stability Index), reference-quantile bins for continuous features and
  category bins for discrete ones. Industry thresholds: <0.10 stable, 0.10-0.20 moderate,
  >=0.20 significant shift.
* KS two-sample test (continuous features): statistic D and p-value.
* Evidently AI report (HTML) with per-column drift tests, target/prediction drift and, when
  labels are available, classification quality vs. reference (concept drift evidence).
"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd
from scipy import stats

from . import config

EPS = 1e-6


def psi(reference: pd.Series, current: pd.Series, categorical: bool = False, bins: int = 10) -> float:
    ref = reference.dropna().to_numpy()
    cur = current.dropna().to_numpy()
    if categorical or np.unique(ref).size <= 15:
        cats = np.union1d(np.unique(ref), np.unique(cur))
        ref_pct = np.array([(ref == c).mean() for c in cats])
        cur_pct = np.array([(cur == c).mean() for c in cats])
    else:
        edges = np.unique(np.quantile(ref, np.linspace(0, 1, bins + 1)))
        edges[0], edges[-1] = -np.inf, np.inf
        ref_pct = np.histogram(ref, edges)[0] / len(ref)
        cur_pct = np.histogram(cur, edges)[0] / len(cur)
    ref_pct = np.clip(ref_pct, EPS, None)
    cur_pct = np.clip(cur_pct, EPS, None)
    return float(np.sum((cur_pct - ref_pct) * np.log(cur_pct / ref_pct)))


def ks(reference: pd.Series, current: pd.Series) -> tuple[float, float]:
    res = stats.ks_2samp(reference.dropna(), current.dropna())
    return float(res.statistic), float(res.pvalue)


def severity(psi_value: float) -> str:
    if psi_value >= config.PSI_CRITICAL:
        return "critical"
    if psi_value >= config.PSI_WARNING:
        return "warning"
    return "stable"


def feature_drift_table(reference: pd.DataFrame, current: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for col in config.FEATURES:
        is_cat = col in config.CATEGORICAL_FEATURES
        p = psi(reference[col], current[col], categorical=is_cat)
        d, pval = ks(reference[col], current[col]) if not is_cat else (np.nan, np.nan)
        rows.append(
            {
                "feature": col,
                "type": "categorical" if is_cat else "numerical",
                "psi": round(p, 4),
                "ks_statistic": None if is_cat else round(d, 4),
                "ks_pvalue": None if is_cat else float(f"{pval:.3g}"),
                "ks_drift": (not is_cat) and pval < config.KS_PVALUE,
                "severity": severity(p),
                "ref_mean": round(float(reference[col].mean()), 2),
                "cur_mean": round(float(current[col].mean()), 2),
            }
        )
    return pd.DataFrame(rows).sort_values("psi", ascending=False).reset_index(drop=True)


# --- Evidently ----------------------------------------------------------------------------------


def evidently_report(
    reference: pd.DataFrame, current: pd.DataFrame, out_html: Path, with_labels: bool = True
) -> dict[str, float]:
    """Generate the Evidently HTML report and return the key values it computed."""
    from evidently import BinaryClassification, DataDefinition, Dataset, Report
    from evidently.presets import ClassificationPreset, DataDriftPreset

    cols = [*config.FEATURES, "prediction", "prediction_label"]
    if with_labels:
        cols.append(config.TARGET)
    definition = DataDefinition(
        numerical_columns=[*config.NUMERICAL_FEATURES, "prediction"],
        categorical_columns=[
            *config.CATEGORICAL_FEATURES,
            "prediction_label",
            *([config.TARGET] if with_labels else []),
        ],
        classification=[
            BinaryClassification(
                target=config.TARGET, prediction_labels="prediction_label", prediction_probas="prediction"
            )
        ]
        if with_labels
        else None,
    )
    drift_cols = [*config.FEATURES, "prediction", *([config.TARGET] if with_labels else [])]
    metrics = [
        DataDriftPreset(
            columns=drift_cols, num_method="ks", cat_method="psi", drift_share=config.DRIFT_SHARE_CRITICAL
        )
    ]
    if with_labels:
        metrics.append(ClassificationPreset())
    report = Report(metrics, include_tests=True)
    snapshot = report.run(
        current_data=Dataset.from_pandas(current[cols], data_definition=definition),
        reference_data=Dataset.from_pandas(reference[cols], data_definition=definition),
    )
    out_html.parent.mkdir(parents=True, exist_ok=True)
    snapshot.save_html(str(out_html))

    values: dict[str, float] = {}
    for m in snapshot.dict()["metrics"]:
        cfg = m.get("config", {})
        mtype = cfg.get("type", "").split(":")[-1]
        val = m.get("value")
        if mtype == "ValueDrift":
            values[f"evidently_drift_{cfg['column']}"] = float(val)
        elif mtype == "DriftedColumnsCount" and isinstance(val, dict):
            values["evidently_drifted_columns"] = float(val.get("count", 0))
            values["evidently_drifted_share"] = float(val.get("share", 0))
        elif mtype in {"RocAuc", "Accuracy", "F1Score", "Precision", "Recall"} and isinstance(
            val, (int, float)
        ):
            values[f"evidently_{mtype.lower()}"] = float(val)
    return values
