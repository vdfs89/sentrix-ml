import json

from src import config
from src.portal import _month_label, build_index


def _load(name):
    return json.loads((config.REPORTS_DIR / name).read_text(encoding="utf-8"))


def test_portal_renders_versioned_results():
    meta = json.loads(config.MODEL_META_FILE.read_text(encoding="utf-8"))
    page = build_index(
        meta,
        _load("monitoring_records.json"),
        _load("alerts.json"),
        _load("causal_analysis.json"),
        _load("technical_findings.json"),
    )
    assert page.startswith("<!doctype html>")
    assert "monitoring_dashboard.html" in page
    for month in ("2025-07", "2025-12"):
        assert f"evidently/drift_report_{month}.html" in page
    assert "bloqueado" in page and "quarentena" in page  # corrupted batch shown as blocked
    assert "36" in page  # total alerts KPI


def test_month_label():
    assert _month_label("2025-12") == "dez/25"
    assert _month_label("2025-09_upstream_bug") == "set/25"
