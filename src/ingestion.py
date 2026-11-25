"""Data ingestion with Privacy by Design.

Steps
-----
1. Download the public UCI "Default of Credit Card Clients" dataset (30k rows) from OpenML.
2. Rename the anonymous x1..x23 columns to business names and normalise undocumented codes.
3. Attach *synthetic* direct identifiers (customer_id, cpf) to emulate what a real fintech
   receives, then immediately pseudonymise them with a salted HMAC-SHA256 and drop them
   (LGPD art. 6 III - necessity / art. 13 - pseudonymisation).
4. Move protected attributes (sex, marriage) to a restricted audit table: they are never model
   features, only used to audit bias (LGPD art. 6 IX - non-discrimination).
5. Split into Reference (training era) and Production pool (future months, where drift happens).
"""

from __future__ import annotations

import hashlib
import hmac
import os

import numpy as np
import pandas as pd

from . import config
from .logging_utils import get_logger, stage

logger = get_logger()

COLUMN_MAP = {
    "x1": "limit_bal",
    "x2": "sex",
    "x3": "education",
    "x4": "marriage",
    "x5": "age",
    "x6": "pay_0",
    "x7": "pay_2",
    "x8": "pay_3",
    "x9": "pay_4",
    "x10": "pay_5",
    "x11": "pay_6",
    **{f"x{11 + i}": f"bill_amt{i}" for i in range(1, 7)},
    **{f"x{17 + i}": f"pay_amt{i}" for i in range(1, 7)},
    "y": config.TARGET,
}


def download_raw(force: bool = False) -> pd.DataFrame:
    if config.RAW_FILE.exists() and not force:
        return pd.read_csv(config.RAW_FILE)
    from sklearn.datasets import fetch_openml

    frame = fetch_openml(data_id=config.OPENML_DATA_ID, as_frame=True, parser="auto").frame
    config.RAW_FILE.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(config.RAW_FILE, index=False)
    return pd.read_csv(config.RAW_FILE)


def _salt() -> bytes:
    salt = os.getenv(config.PII_SALT_ENV)
    if not salt:
        salt = config.DEFAULT_DEV_SALT
    return salt.encode()


def pseudonymize(values: pd.Series) -> pd.Series:
    """Keyed hash (HMAC-SHA256). Irreversible without the secret salt; stable for joins."""
    key = _salt()
    return values.astype(str).map(lambda v: hmac.new(key, v.encode(), hashlib.sha256).hexdigest()[:20])


def _synthetic_cpf(rng: np.random.Generator, n: int) -> list[str]:
    # Random 11-digit strings — synthetic, not valid CPFs, never real people.
    digits = rng.integers(0, 10, size=(n, 11))
    return ["".join(map(str, row)) for row in digits]


def clean_raw(raw: pd.DataFrame) -> pd.DataFrame:
    df = raw.rename(columns=COLUMN_MAP).copy()
    df[config.TARGET] = df[config.TARGET].astype(int)
    # Undocumented codes in the original dataset -> "others"
    df["education"] = df["education"].replace({0: 4, 5: 4, 6: 4})
    df["marriage"] = df["marriage"].replace({0: 3})
    return df


def attach_and_protect_identifiers(df: pd.DataFrame, rng: np.random.Generator) -> pd.DataFrame:
    df = df.copy()
    df["customer_id"] = [f"C{idx:07d}" for idx in range(len(df))]
    df["cpf"] = _synthetic_cpf(rng, len(df))
    df[config.ID_COL] = pseudonymize(df["cpf"])
    return df.drop(columns=config.DIRECT_IDENTIFIERS)


def assign_months(n: int, start: str, n_months: int) -> list[str]:
    months = pd.period_range(start=start, periods=n_months, freq="M").astype(str)
    return list(np.repeat(months, int(np.ceil(n / n_months)))[:n])


def run_ingestion() -> dict[str, int]:
    config.ensure_dirs()
    rng = np.random.default_rng(config.RANDOM_SEED)
    with stage(logger, "ingestion", source=f"openml:{config.OPENML_DATA_ID}") as info:
        raw = download_raw()
        df = clean_raw(raw)
        n_dupes = int(df.duplicated().sum())
        df = df.drop_duplicates().reset_index(drop=True)
        df = attach_and_protect_identifiers(df, rng)
        df = df.sample(frac=1.0, random_state=config.RANDOM_SEED).reset_index(drop=True)

        reference = df.iloc[: config.REFERENCE_SIZE].copy()
        production = df.iloc[config.REFERENCE_SIZE :].copy()
        reference[config.TIME_COL] = assign_months(len(reference), config.REFERENCE_START, 6)
        production[config.TIME_COL] = assign_months(
            len(production), config.PRODUCTION_START, config.N_PRODUCTION_BATCHES
        )

        # Protected attributes go to a restricted audit store, keyed only by the pseudonym.
        audit = df[[config.ID_COL, *config.PROTECTED_ATTRIBUTES]]
        audit.to_csv(config.AUDIT_DIR / "protected_attributes.csv", index=False)

        cols = [config.ID_COL, config.TIME_COL, *config.FEATURES, config.TARGET]
        reference[cols].to_csv(config.REFERENCE_FILE, index=False)
        production[cols].to_csv(config.PRODUCTION_POOL_FILE, index=False)

        info.update(
            rows_raw=len(raw),
            duplicates_removed=n_dupes,
            rows_reference=len(reference),
            rows_production=len(production),
        )
    return info


if __name__ == "__main__":
    run_ingestion()
