"""Validation gate: applies the data contract to a batch and blocks ingestion on failure."""
from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass, field

import numpy as np
import pandas as pd
import pandera.errors

from . import config
from .contracts import CONTRACT, RULES
from .logging_utils import get_logger, log_event, stage

logger = get_logger()


@dataclass
class ValidationResult:
    batch: str
    passed: bool
    rows: int
    n_failures: int = 0
    failed_rules: dict[str, int] = field(default_factory=dict)
    failed_rows: int = 0
    sample_failures: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _rule_for(column: str | None, check: str) -> str:
    check = str(check)
    if "column_in_dataframe" in check or "column_in_schema" in check:
        return "R9"
    if "not_nullable" in check:
        return "R1"
    if "field_uniqueness" in check or "unique" in check:
        return "R7"
    if "dtype" in check or "coerce" in check:
        return "R1"  # type coercion failure is treated as missing/invalid value
    col = column or ""
    if col == "age":
        return "R2"
    if col == "limit_bal":
        return "R3"
    if col == "education":
        return "R4"
    if col in config.PAY_STATUS:
        return "R5"
    if col in config.PAY_AMT or col in config.BILL_AMT:
        return "R6"
    if col == config.TARGET:
        return "R8"
    return "R10"


def validate_batch(df: pd.DataFrame, batch: str, quarantine: bool = True) -> tuple[ValidationResult, pd.DataFrame | None]:
    """Return (result, validated_df). validated_df is None when the batch is blocked."""
    with stage(logger, "data_validation", batch=batch, rows=len(df)) as info:
        try:
            validated = CONTRACT.validate(df, lazy=True)
            result = ValidationResult(batch=batch, passed=True, rows=len(df))
            info.update(passed=True, n_failures=0)
            return result, validated
        except pandera.errors.SchemaErrors as err:
            fc = err.failure_cases.copy()
            fc["rule"] = [_rule_for(c, k) for c, k in zip(fc["column"], fc["check"])]
            failed_rules = fc["rule"].value_counts().sort_index().to_dict()
            failed_rows = int(fc["index"].dropna().nunique())
            sample = (
                fc.groupby("rule").head(3)[["rule", "column", "check", "failure_case", "index"]]
                .astype(str).to_dict(orient="records")
            )
            result = ValidationResult(
                batch=batch, passed=False, rows=len(df), n_failures=len(fc),
                failed_rules={k: int(v) for k, v in failed_rules.items()},
                failed_rows=failed_rows, sample_failures=sample,
            )
            if quarantine:
                config.QUARANTINE_DIR.mkdir(parents=True, exist_ok=True)
                df.to_csv(config.QUARANTINE_DIR / f"{batch}_blocked.csv", index=False)
                fc.to_csv(config.QUARANTINE_DIR / f"{batch}_failure_cases.csv", index=False)
                report = {**result.to_dict(), "rules": {k: RULES[k] for k in failed_rules}}
                (config.QUARANTINE_DIR / f"{batch}_validation_report.json").write_text(
                    json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
            info.update(passed=False, n_failures=len(fc), failed_rules=result.failed_rules,
                        status="blocked")
            log_event(logger, logging.CRITICAL,
                      "DATA CONTRACT VIOLATED - batch blocked and quarantined",
                      stage="data_validation", batch=batch, failed_rules=result.failed_rules,
                      alert="ingestion_blocked")
            return result, None


def make_corrupted_batch(clean: pd.DataFrame, seed: int = config.RANDOM_SEED) -> pd.DataFrame:
    """Simulate an upstream bug: nulls, out-of-range values, duplicates and a leaked CPF column."""
    rng = np.random.default_rng(seed)
    bad = clean.copy().reset_index(drop=True)
    n = len(bad)

    def pick(k: int) -> np.ndarray:
        return rng.choice(n, size=k, replace=False)

    bad = bad.astype({"limit_bal": float, "age": float, "pay_amt1": float})
    bad.loc[pick(25), "limit_bal"] = np.nan            # R1 null income/limit
    bad.loc[pick(12), "age"] = 15                       # R2 minor
    bad.loc[pick(6), "age"] = 150                       # R2 impossible age
    bad.loc[pick(8), "limit_bal"] = -5_000              # R3 negative limit
    bad.loc[pick(10), "education"] = 9                  # R4 unknown code
    bad.loc[pick(5), "pay_0"] = 12                      # R5 invalid status
    bad.loc[pick(7), "pay_amt1"] = -300                 # R6 negative payment
    dupes = bad.sample(15, random_state=seed)           # R7 duplicated customers
    bad = pd.concat([bad, dupes], ignore_index=True)
    bad["cpf"] = "00000000000"                          # R9 raw PII leaked by upstream
    return bad
