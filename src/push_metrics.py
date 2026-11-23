"""Push the metrics of the last pipeline run to the Pushgateway without re-running it.

    SENTRIX_PUSHGATEWAY=http://localhost:9091 python -m src.push_metrics
"""
from __future__ import annotations

import json
import os

from . import config
from .monitoring import export_prometheus


def main() -> None:
    os.environ.setdefault(config.PUSHGATEWAY_ENV, "http://localhost:9091")
    records = json.loads((config.REPORTS_DIR / "monitoring_records.json").read_text(encoding="utf-8"))
    alerts = json.loads((config.REPORTS_DIR / "alerts.json").read_text(encoding="utf-8"))
    meta = json.loads(config.MODEL_META_FILE.read_text(encoding="utf-8"))
    export_prometheus(records, alerts, meta["holdout_metrics"])


if __name__ == "__main__":
    main()
