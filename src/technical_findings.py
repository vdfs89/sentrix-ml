"""Reproducible evidence for the technical findings documented in the README.

    python -m src.technical_findings      # writes reports/technical_findings.json

F1  Evidently's PSI bins numerical features with equal-width (Sturges) edges over the combined
    range, although its docstring says "reference quantiles". On heavy-tailed financial
    variables almost all mass falls into the first bins, so real shifts are under-reported.
F2  KS false-positive rate under the null hypothesis (no drift): resample the reference
    against itself many times and count how often each test flags drift.
F3  Detection quality per month vs. the ground truth of the simulation (which features were
    intentionally changed): precision / recall of PSI and KS.
F4  Lead time: first month each alert type fired.
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import config
from .drift_detection import ks, psi
from .drift_simulation import batch_path

N_NULL_TRIALS = 200


def _evidently_psi(ref: pd.Series, cur: pd.Series) -> float:
    from evidently.legacy.calculations.stattests.psi import _psi
    from evidently.legacy.core import ColumnType

    value, _ = _psi(ref, cur, ColumnType.Numerical, threshold=0.1)
    return float(value)


def finding_psi_binning(reference: pd.DataFrame) -> dict:
    rows = []
    for spec in config.DRIFT_SCHEDULE[2:]:
        cur = pd.read_csv(batch_path(spec["month"]))
        for feat in ("bill_amt1", "pay_amt1", "limit_bal"):
            rows.append({"month": spec["month"], "feature": feat,
                         "psi_quantile_bins": round(psi(reference[feat], cur[feat]), 4),
                         "psi_evidently": round(_evidently_psi(reference[feat], cur[feat]), 4)})
    combined = pd.concat([reference["pay_amt1"], reference["pay_amt1"]])
    edges = np.histogram_bin_edges(combined, bins="sturges")
    first_bin_share = float(((reference["pay_amt1"] >= edges[0]) & (reference["pay_amt1"] < edges[1])).mean())
    return {"comparison": rows,
            "pay_amt1_reference_share_in_first_equal_width_bin": round(first_bin_share, 4),
            "n_equal_width_bins_pay_amt1": int(len(edges) - 1)}


def finding_null_false_positives(reference: pd.DataFrame, n_cur: int = 2495) -> dict:
    rng = np.random.default_rng(config.RANDOM_SEED)
    feats = config.NUMERICAL_FEATURES
    ks_hits = {f: 0 for f in feats}
    psi_hits = {f: 0 for f in feats}
    any_ks = any_psi = 0
    for _ in range(N_NULL_TRIALS):
        idx = rng.permutation(len(reference))
        ref, cur = reference.iloc[idx[n_cur:]], reference.iloc[idx[:n_cur]]
        trial_ks = trial_psi = False
        for f in feats:
            _, p = ks(ref[f], cur[f])
            if p < config.KS_PVALUE:
                ks_hits[f] += 1
                trial_ks = True
            if psi(ref[f], cur[f]) >= config.PSI_WARNING:
                psi_hits[f] += 1
                trial_psi = True
        any_ks += trial_ks
        any_psi += trial_psi
    return {
        "trials": N_NULL_TRIALS, "numerical_features": len(feats),
        "ks_false_positive_rate_per_feature": round(sum(ks_hits.values()) / (N_NULL_TRIALS * len(feats)), 4),
        "psi_false_positive_rate_per_feature": round(sum(psi_hits.values()) / (N_NULL_TRIALS * len(feats)), 4),
        "share_of_batches_with_any_ks_alarm": round(any_ks / N_NULL_TRIALS, 4),
        "share_of_batches_with_any_psi_alarm": round(any_psi / N_NULL_TRIALS, 4),
    }


def detection_quality(records: list[dict], manifest: list[dict]) -> list[dict]:
    truth = {m["month"]: set(m["features_intentionally_drifted"]) for m in manifest}
    out = []
    for r in records:
        if not r.get("validation_passed") or r["batch"] not in truth:
            continue
        t = truth[r["batch"]]
        flagged_psi = {f for f, v in r["psi"].items() if v >= config.PSI_WARNING}
        flagged_ks = {f for f, p in r["ks_pvalue"].items() if p < config.KS_PVALUE}
        row = {"month": r["batch"], "truly_drifted": len(t)}
        for name, flagged in (("psi", flagged_psi), ("ks", flagged_ks)):
            tp = len(flagged & t)
            row[f"{name}_flagged"] = len(flagged)
            row[f"{name}_false_positives"] = sorted(flagged - t)
            row[f"{name}_precision"] = round(tp / len(flagged), 3) if flagged else None
            row[f"{name}_recall"] = round(tp / len(t), 3) if t else None
        out.append(row)
    return out


def lead_time(alerts: list[dict]) -> dict:
    first: dict[str, str] = {}
    for a in sorted(alerts, key=lambda a: a["batch"]):
        key = f"{a['rule']}:{a['severity']}"
        first.setdefault(key, a["batch"])
    return dict(sorted(first.items(), key=lambda kv: kv[1]))


def run() -> dict:
    reference = pd.read_csv(config.REFERENCE_FILE)
    records = json.loads((config.REPORTS_DIR / "monitoring_records.json").read_text(encoding="utf-8"))
    alerts = json.loads((config.REPORTS_DIR / "alerts.json").read_text(encoding="utf-8"))
    manifest = json.loads((config.PRODUCTION_DIR / "drift_manifest.json").read_text(encoding="utf-8"))
    result = {
        "F1_psi_binning": finding_psi_binning(reference),
        "F2_null_false_positives": finding_null_false_positives(reference),
        "F3_detection_quality": detection_quality(records, manifest),
        "F4_first_month_per_alert": lead_time(alerts),
    }
    (config.REPORTS_DIR / "technical_findings.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    return result


if __name__ == "__main__":
    print(json.dumps(run(), indent=2, ensure_ascii=False))
