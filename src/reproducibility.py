"""Cross-platform reproducibility check of a pipeline run against the versioned evidence.

    python -m src.reproducibility <baseline records.json> [alerts.json] [model_metadata.json]

Data-level outputs (ingestion, contract, simulated batches, feature PSI) are deterministic and
must match exactly. XGBoost training is NOT bit-identical across operating systems / CPUs
(README > Achados técnicos A8), so model-dependent metrics are compared with a tolerance and
alert differences on those metrics are reported, not failed.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

from . import config

EXACT_TOL = 1e-9
MODEL_TOLERANCES = {"auc": 0.01, "prediction_psi": 0.02, "approval_rate": 0.03, "mean_pd": 0.01}
MODEL_DEPENDENT_RULES = {"prediction_drift", "concept_drift_performance", "fairness"}


def compare(baseline: list[dict], current: list[dict]) -> tuple[list[str], list[str]]:
    """Return (errors, notes). Errors break reproducibility; notes are expected model noise."""
    errors: list[str] = []
    notes: list[str] = []
    if [r["batch"] for r in baseline] != [r["batch"] for r in current]:
        return [f"batches differ: {[r['batch'] for r in current]}"], notes
    for b, c in zip(baseline, current, strict=True):
        name = b["batch"]
        for key in ("rows", "validation_passed", "failed_rules", "n_drifted_features"):
            if b.get(key) != c.get(key):
                errors.append(f"{name}: {key} {b.get(key)} != {c.get(key)}")
        if not b["validation_passed"]:
            continue
        for feat, v in b["psi"].items():
            if abs(v - c["psi"][feat]) > EXACT_TOL:
                errors.append(f"{name}: PSI[{feat}] {v} != {c['psi'][feat]}")
        for key, tol in MODEL_TOLERANCES.items():
            diff = abs(b[key] - c[key])
            if diff > tol:
                errors.append(f"{name}: {key} diff {diff:.4f} > {tol}")
            elif diff > EXACT_TOL:
                notes.append(f"{name}: {key} {b[key]:.4f} -> {c[key]:.4f} (within ±{tol})")
    return errors, notes


def compare_alerts(baseline: list[dict], current: list[dict]) -> tuple[list[str], list[str]]:
    def key(a: dict) -> tuple[str, str, str]:
        return a["batch"], a["rule"], a["severity"]

    b, c = {key(a) for a in baseline}, {key(a) for a in current}
    diff = sorted(b ^ c)
    errors = [f"alert set differs on data-level rule: {d}" for d in diff if d[1] not in MODEL_DEPENDENT_RULES]
    notes = [
        f"model-dependent alert differs (threshold boundary): {d}"
        for d in diff
        if d[1] in MODEL_DEPENDENT_RULES
    ]
    return errors, notes


def main(argv: list[str]) -> int:
    baseline = json.loads(Path(argv[0]).read_text(encoding="utf-8"))
    current = json.loads((config.REPORTS_DIR / "monitoring_records.json").read_text(encoding="utf-8"))
    errors, notes = compare(baseline, current)
    if len(argv) > 1:
        base_alerts = json.loads(Path(argv[1]).read_text(encoding="utf-8"))
        cur_alerts = json.loads((config.REPORTS_DIR / "alerts.json").read_text(encoding="utf-8"))
        e, n = compare_alerts(base_alerts, cur_alerts)
        errors += e
        notes += n
    if len(argv) > 2:
        base_meta = json.loads(Path(argv[2]).read_text(encoding="utf-8"))
        cur_meta = json.loads(config.MODEL_META_FILE.read_text(encoding="utf-8"))
        if base_meta["threshold"] != cur_meta["threshold"]:
            errors.append(f"decision threshold changed: {base_meta['threshold']} -> {cur_meta['threshold']}")
        if base_meta.get("reference_data_sha256") != cur_meta.get("reference_data_sha256"):
            errors.append("reference data hash differs (ingestion is not reproducible)")
        b_opt, c_opt = base_meta.get("f1_optimal_threshold"), cur_meta.get("f1_optimal_threshold")
        if b_opt is not None and c_opt is not None and b_opt != c_opt:
            notes.append(
                f"F1-optimal threshold moved {b_opt} -> {c_opt} (informative; policy threshold fixed)"
            )
    for n in notes:
        print(f"NOTE  {n}")
    for e in errors:
        print(f"ERROR {e}")
    print("reproducibility:", "FAILED" if errors else "OK", f"({len(notes)} expected model-noise notes)")
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
