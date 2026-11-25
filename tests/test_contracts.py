import numpy as np
import pandas as pd
import pytest

from src import config
from src.validation import make_corrupted_batch, validate_batch


def _clean_batch(n: int = 300) -> pd.DataFrame:
    rng = np.random.default_rng(0)
    df = pd.DataFrame(
        {
            config.ID_COL: [f"key{i:08d}" for i in range(n)],
            config.TIME_COL: "2025-07",
            "limit_bal": rng.integers(1, 50, n) * 10_000.0,
            "education": rng.integers(1, 5, n),
            "age": rng.integers(21, 70, n),
            config.TARGET: rng.integers(0, 2, n),
        }
    )
    for c in config.PAY_STATUS:
        df[c] = rng.integers(-2, 3, n)
    for c in config.BILL_AMT:
        df[c] = rng.normal(50_000, 20_000, n).round()
    for c in config.PAY_AMT:
        df[c] = rng.exponential(5_000, n).round()
    return df[[config.ID_COL, config.TIME_COL, *config.FEATURES, config.TARGET]]


def test_clean_batch_passes():
    result, validated = validate_batch(_clean_batch(), "test_clean", quarantine=False)
    assert result.passed and validated is not None


def test_corrupted_batch_is_blocked_with_every_rule_family():
    result, validated = validate_batch(make_corrupted_batch(_clean_batch()), "test_bad", quarantine=False)
    assert not result.passed
    assert validated is None
    for rule in ("R1", "R2", "R3", "R4", "R5", "R6", "R7", "R9"):
        assert rule in result.failed_rules, rule


@pytest.mark.parametrize(
    "column,value,rule",
    [
        ("age", 17, "R2"),
        ("limit_bal", 0, "R3"),
        ("education", 7, "R4"),
        ("pay_0", 9, "R5"),
        ("pay_amt3", -1, "R6"),
    ],
)
def test_single_rule_violation(column, value, rule):
    df = _clean_batch()
    df.loc[0, column] = value
    result, _ = validate_batch(df, "test_single", quarantine=False)
    assert not result.passed and rule in result.failed_rules


def test_raw_pii_column_is_rejected():
    df = _clean_batch()
    df["cpf"] = "12345678900"
    result, _ = validate_batch(df, "test_pii", quarantine=False)
    assert "R9" in result.failed_rules


def test_null_income_is_rejected():
    df = _clean_batch()
    df.loc[3, "limit_bal"] = np.nan
    result, _ = validate_batch(df, "test_null", quarantine=False)
    assert "R1" in result.failed_rules
