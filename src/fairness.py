"""Bias audit. Protected attributes live in a restricted store and are joined only here, by
pseudonymous key, to measure outcome disparity. They never reach the model."""

from __future__ import annotations

import pandas as pd

from . import config

SEX_LABELS = {1: "masculino", 2: "feminino"}


def load_protected() -> pd.DataFrame:
    return pd.read_csv(config.AUDIT_DIR / "protected_attributes.csv")


def disparate_impact(
    scored: pd.DataFrame, protected: pd.DataFrame | None = None, attribute: str = "sex"
) -> dict:
    protected = load_protected() if protected is None else protected
    df = scored[[config.ID_COL, "prediction_label"]].merge(protected, on=config.ID_COL, how="left")
    approval = (1 - df.groupby(attribute)["prediction_label"].mean()).rename(
        index=SEX_LABELS if attribute == "sex" else None
    )
    di = float(approval.min() / approval.max()) if approval.max() > 0 else 1.0
    return {
        "attribute": attribute,
        "approval_rate_by_group": approval.round(4).to_dict(),
        "disparate_impact": round(di, 4),
    }
