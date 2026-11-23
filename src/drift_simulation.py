"""Simulate the passage of time over the Production pool (Data Drift + Concept Drift).

Economic narrative (see config.DRIFT_SCHEDULE):
* Inflation  -> monthly bills (bill_amt*) grow and real repayments (pay_amt*) shrink
  (bills x (1+inflation), payments x (1-0.5*inflation)).
* New client profile -> influx of young customers (21-29) with lower credit limits
  (limit_bal, age shift).
* Concept drift -> customers that *look* safe (paying on time, pay_0 <= 0) but carry high
  credit utilisation start defaulting: P(default | X) changes, so a model trained on the
  old relation becomes silently wrong even where X looks familiar.

For every month we persist both the drifted batch (what production sees) and the original
batch (counterfactual, used by the causal analysis).
"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from . import config
from .logging_utils import get_logger, stage

logger = get_logger()

ORIGINAL_DIR = config.PRODUCTION_DIR / "original"


def batch_path(month: str) -> "config.Path":
    return config.PRODUCTION_DIR / f"batch_{month}.csv"


def original_path(month: str) -> "config.Path":
    return ORIGINAL_DIR / f"batch_{month}.csv"


def apply_data_drift(df: pd.DataFrame, inflation: float, new_profile_share: float,
                     rng: np.random.Generator) -> tuple[pd.DataFrame, int]:
    out = df.copy()
    n = len(out)
    if inflation > 0:
        noise = rng.lognormal(mean=0.0, sigma=0.05, size=(n, len(config.BILL_AMT)))
        out[config.BILL_AMT] = (out[config.BILL_AMT].to_numpy() * (1 + inflation) * noise).round(0)
        out[config.PAY_AMT] = (out[config.PAY_AMT].to_numpy() * (1 - 0.5 * inflation)).round(0)
    n_new = int(round(new_profile_share * n))
    if n_new:
        idx = rng.choice(n, size=n_new, replace=False)
        out.loc[out.index[idx], "age"] = rng.integers(21, 30, size=n_new)
        new_limit = (out.loc[out.index[idx], "limit_bal"] * 0.4).round(-4).clip(lower=10_000)
        out.loc[out.index[idx], "limit_bal"] = new_limit
    return out, n_new


def apply_concept_drift(df: pd.DataFrame, flip_prob: float,
                        rng: np.random.Generator) -> tuple[pd.DataFrame, int]:
    out = df.copy()
    if flip_prob <= 0:
        return out, 0
    utilisation = out["bill_amt1"] / out["limit_bal"]
    at_risk = (out["pay_0"] <= 0) & (utilisation > 0.5) & (out[config.TARGET] == 0)
    flips = at_risk & (rng.random(len(out)) < flip_prob)
    out.loc[flips, config.TARGET] = 1
    return out, int(flips.sum())


def run_simulation() -> list[dict]:
    config.ensure_dirs()
    ORIGINAL_DIR.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(config.RANDOM_SEED + 1)
    pool = pd.read_csv(config.PRODUCTION_POOL_FILE, dtype={config.TIME_COL: str})
    manifest = []
    with stage(logger, "drift_simulation", batches=len(config.DRIFT_SCHEDULE)) as info:
        for spec in config.DRIFT_SCHEDULE:
            month = spec["month"]
            original = pool[pool[config.TIME_COL] == month].copy()
            drifted, n_new = apply_data_drift(original, spec["inflation"],
                                              spec["new_profile_share"], rng)
            drifted, n_flips = apply_concept_drift(drifted, spec["concept_flip"], rng)
            for c in ["age", "education", *config.PAY_STATUS, config.TARGET]:
                drifted[c] = drifted[c].astype(int)
            original.to_csv(original_path(month), index=False)
            drifted.to_csv(batch_path(month), index=False)
            manifest.append({
                **spec, "rows": len(drifted), "new_profile_rows": n_new,
                "concept_flips": n_flips,
                "default_rate_original": round(float(original[config.TARGET].mean()), 4),
                "default_rate_drifted": round(float(drifted[config.TARGET].mean()), 4),
                "features_intentionally_drifted": (
                    (config.BILL_AMT + config.PAY_AMT if spec["inflation"] else [])
                    + (["age", "limit_bal"] if spec["new_profile_share"] else [])),
            })
        (config.PRODUCTION_DIR / "drift_manifest.json").write_text(
            json.dumps(manifest, indent=2), encoding="utf-8")
        info.update(rows=int(sum(m["rows"] for m in manifest)),
                    concept_flips=int(sum(m["concept_flips"] for m in manifest)))
    return manifest


if __name__ == "__main__":
    run_simulation()
