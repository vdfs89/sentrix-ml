"""Data contracts (Pandera).

The contract is the formal agreement between the upstream data producer and the model. Every
batch must satisfy it *before* reaching the model; any violation blocks the whole batch.

Hard rules (all enforced, lazy validation collects every failure):
  R1  No nulls in any feature, id or target                      (nullable=False everywhere)
  R2  age between 18 and 100                                     (legal age for credit)
  R3  limit_bal > 0 and <= 1.000.000                             (credit limit bounds)
  R4  education in {1, 2, 3, 4}                                  (closed domain)
  R5  repayment status pay_* in [-2, 8]                          (documented codes)
  R6  pay_amt* >= 0 and bill_amt* within [-1M, 5M]               (financial sanity)
  R7  customer_key unique (no duplicated customers in a batch)
  R8  target in {0, 1} (when labels are present)
  R9  strict schema: unexpected columns are rejected -> a raw identifier (e.g. cpf) can never
      leak into the model by accident (Privacy by Default)
  R10 batch-level: at least 100 rows and default rate below 80% (upstream sanity)
"""

from __future__ import annotations

import pandera.pandas as pa
from pandera.pandas import Check, Column, DataFrameSchema

from . import config

AGE_MIN, AGE_MAX = 18, 100
LIMIT_MAX = 1_000_000


def _feature_columns() -> dict[str, Column]:
    cols: dict[str, Column] = {
        "limit_bal": Column(float, [Check.gt(0), Check.le(LIMIT_MAX)], coerce=True, nullable=False),
        "education": Column(int, Check.isin([1, 2, 3, 4]), coerce=True, nullable=False),
        "age": Column(int, Check.in_range(AGE_MIN, AGE_MAX), coerce=True, nullable=False),
    }
    for c in config.PAY_STATUS:
        cols[c] = Column(int, Check.in_range(-2, 8), coerce=True, nullable=False)
    for c in config.BILL_AMT:
        cols[c] = Column(float, Check.in_range(-1_000_000, 5_000_000), coerce=True, nullable=False)
    for c in config.PAY_AMT:
        cols[c] = Column(float, Check.ge(0), coerce=True, nullable=False)
    return cols


def build_contract(with_target: bool = True) -> DataFrameSchema:
    columns: dict[str, Column] = {
        config.ID_COL: Column(str, Check.str_length(min_value=8), unique=True, nullable=False),
        config.TIME_COL: Column(str, Check.str_matches(r"^\d{4}-\d{2}$"), nullable=False),
        **_feature_columns(),
    }
    checks = [Check(lambda df: len(df) >= 100, error="batch must have at least 100 rows")]
    if with_target:
        columns[config.TARGET] = Column(int, Check.isin([0, 1]), coerce=True, nullable=False)
        checks.append(
            Check(
                lambda df: df[config.TARGET].mean() < 0.8,
                error="default rate >= 80%: upstream labelling bug suspected",
            )
        )
    return DataFrameSchema(
        columns=columns,
        checks=checks,
        strict=True,  # R9: extra columns (e.g. raw PII) are rejected
        name="credit_scoring_input_contract_v1",
    )


CONTRACT = build_contract(with_target=True)
CONTRACT_INFERENCE = build_contract(with_target=False)

RULES = {
    "R1": "Sem nulos em features, id e target",
    "R2": f"Idade entre {AGE_MIN} e {AGE_MAX}",
    "R3": f"Limite de crédito > 0 e <= {LIMIT_MAX:,}".replace(",", "."),
    "R4": "Escolaridade no domínio {1,2,3,4}",
    "R5": "Status de pagamento pay_* entre -2 e 8",
    "R6": "pay_amt >= 0 e bill_amt entre -1M e 5M",
    "R7": "customer_key único (sem duplicatas)",
    "R8": "Target binário {0,1}",
    "R9": "Schema estrito: colunas não previstas (ex.: CPF) são rejeitadas",
    "R10": "Lote com >= 100 linhas e taxa de default < 80%",
}

__all__ = ["CONTRACT", "CONTRACT_INFERENCE", "RULES", "build_contract", "pa"]
