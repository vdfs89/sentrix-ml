"""Generate the SENTRIX Grafana dashboard (dashboard as code).

    python monitoring/grafana/generate_dashboard.py

Writes monitoring/grafana/dashboards/sentrix.json, which Grafana loads via provisioning.
Thresholds mirror src/config.py.
"""

from __future__ import annotations

import json
from pathlib import Path

DS = {"type": "prometheus", "uid": "sentrix-prom"}
OUT = Path(__file__).parent / "dashboards" / "sentrix.json"

GREEN, ORANGE, RED, BLUE = "green", "orange", "red", "blue"
_next_id = 0


def _id() -> int:
    global _next_id
    _next_id += 1
    return _next_id


def target(expr: str, legend: str = "", fmt: str = "time_series") -> dict:
    return {
        "datasource": DS,
        "expr": expr,
        "legendFormat": legend,
        "instant": True,
        "range": False,
        "format": fmt,
        "refId": "A",
    }


def thresholds(*steps: tuple[float | None, str]) -> dict:
    return {"mode": "absolute", "steps": [{"color": c, "value": v} for v, c in steps]}


def stat(
    title: str,
    expr: str,
    x: int,
    y: int,
    w: int = 4,
    unit: str = "none",
    th: dict | None = None,
    decimals: int | None = None,
    desc: str = "",
) -> dict:
    defaults = {"unit": unit, "thresholds": th or thresholds((None, BLUE)), "color": {"mode": "thresholds"}}
    if decimals is not None:
        defaults["decimals"] = decimals
    return {
        "id": _id(),
        "type": "stat",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": 4},
        "targets": [target(expr)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
            "colorMode": "background",
            "graphMode": "none",
            "textMode": "value",
            "justifyMode": "center",
        },
    }


def bars(
    title: str,
    expr: str,
    legend: str,
    x: int,
    y: int,
    w: int,
    h: int,
    unit: str,
    th: dict,
    vmin: float | None = 0,
    vmax: float | None = None,
    desc: str = "",
    orientation: str = "horizontal",
    name_top: bool = False,
    decimals: int = 3,
) -> dict:
    defaults = {"unit": unit, "thresholds": th, "color": {"mode": "thresholds"}, "decimals": decimals}
    if vmin is not None:
        defaults["min"] = vmin
    if vmax is not None:
        defaults["max"] = vmax
    return {
        "id": _id(),
        "type": "bargauge",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(expr, legend)],
        "fieldConfig": {"defaults": defaults, "overrides": []},
        "options": {
            "orientation": orientation,
            "displayMode": "basic",
            "showUnfilled": True,
            "valueMode": "color",
            "namePlacement": "top" if name_top else "left",
            "sizing": "auto",
            "reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False},
        },
    }


def row(title: str, y: int) -> dict:
    return {
        "id": _id(),
        "type": "row",
        "title": title,
        "collapsed": False,
        "gridPos": {"x": 0, "y": y, "w": 24, "h": 1},
        "panels": [],
    }


def table(
    title: str,
    expr: str,
    x: int,
    y: int,
    w: int,
    h: int,
    keep: list[str],
    desc: str = "",
    rename: dict[str, str] | None = None,
) -> dict:
    pattern = "^(" + "|".join(keep) + ")$"
    return {
        "id": _id(),
        "type": "table",
        "title": title,
        "description": desc,
        "datasource": DS,
        "gridPos": {"x": x, "y": y, "w": w, "h": h},
        "targets": [target(expr, fmt="table")],
        "transformations": [
            {"id": "filterFieldsByName", "options": {"include": {"pattern": pattern}}},
            {
                "id": "organize",
                "options": {"indexByName": {k: i for i, k in enumerate(keep)}, "renameByName": rename or {}},
            },
        ],
        "fieldConfig": {
            "defaults": {},
            "overrides": [
                {
                    "matcher": {"id": "byName", "options": "severity"},
                    "properties": [
                        {"id": "custom.cellOptions", "value": {"type": "color-background"}},
                        {
                            "id": "mappings",
                            "value": [
                                {
                                    "type": "value",
                                    "options": {
                                        "critical": {"color": RED, "index": 0},
                                        "warning": {"color": ORANGE, "index": 1},
                                    },
                                }
                            ],
                        },
                    ],
                }
            ],
        },
        "options": {
            "showHeader": True,
            "cellHeight": "sm",
            "sortBy": [{"displayName": "severity", "desc": False}],
        },
    }


def build() -> dict:
    p: list[dict] = []
    y = 0
    p.append(row("Visão geral", y))
    y += 1
    p += [
        stat("Lotes processados", "count(credit_pipeline_batch_validation_passed)", 0, y),
        stat(
            "Lotes bloqueados (contrato)",
            "count(credit_pipeline_batch_validation_passed == 0) or vector(0)",
            4,
            y,
            th=thresholds((None, GREEN), (1, RED)),
        ),
        stat(
            "Alertas disparados",
            'count(ALERTS{alertstate="firing"}) or vector(0)',
            8,
            y,
            th=thresholds((None, GREEN), (1, ORANGE), (5, RED)),
        ),
        stat("AUC baseline (holdout)", 'credit_model_baseline{metric="auc"}', 12, y, decimals=3),
        stat(
            "Pior AUC em produção",
            "min(credit_model_auc)",
            16,
            y,
            decimals=3,
            th=thresholds((None, RED), (0.732, GREEN)),
            desc="Vermelho quando cai mais de 0.05 abaixo do baseline (concept drift).",
        ),
        stat(
            "Última execução do pipeline",
            "credit_pipeline_last_success_timestamp_seconds * 1000",
            20,
            y,
            unit="dateTimeFromNow",
        ),
    ]
    y += 4
    p.append(row("Saúde do modelo e drift por lote", y))
    y += 1
    p += [
        bars(
            "AUC por lote (labels atrasados)",
            "credit_model_auc",
            "{{batch}}",
            0,
            y,
            8,
            8,
            "none",
            thresholds((None, RED), (0.732, GREEN)),
            vmin=0.5,
            vmax=0.85,
        ),
        bars(
            "% de features com drift crítico",
            "credit_data_drift_share",
            "{{batch}}",
            8,
            y,
            8,
            8,
            "percentunit",
            thresholds((None, GREEN), (0.01, ORANGE), (0.30, RED)),
            vmax=1,
        ),
        bars(
            "PSI do score (prediction drift)",
            "credit_prediction_psi",
            "{{batch}}",
            16,
            y,
            8,
            8,
            "none",
            thresholds((None, GREEN), (0.10, ORANGE), (0.20, RED)),
            vmax=0.5,
        ),
    ]
    y += 8
    p += [
        bars(
            "PSI por feature — lote $batch",
            'sort_desc(credit_feature_psi{batch="$batch"})',
            "{{feature}}",
            0,
            y,
            12,
            22,
            "none",
            thresholds((None, GREEN), (0.10, ORANGE), (0.20, RED)),
            vmax=0.6,
            name_top=True,
            desc="< 0.10 estável · 0.10–0.20 moderado · ≥ 0.20 crítico. Troque o lote no topo.",
        ),
        bars(
            "Taxa de default observada por lote",
            "credit_observed_default_rate",
            "{{batch}}",
            12,
            y,
            6,
            11,
            "percentunit",
            thresholds((None, GREEN), (0.273, ORANGE)),
            vmax=0.5,
            desc="Laranja: mais de 5 p.p. acima da referência (label drift).",
        ),
        bars(
            "Taxa de aprovação por lote",
            "credit_approval_rate",
            "{{batch}}",
            18,
            y,
            6,
            11,
            "percentunit",
            thresholds((None, ORANGE), (0.6, GREEN)),
            vmax=1,
        ),
        bars(
            "Disparate impact (sexo)",
            "credit_disparate_impact",
            "{{batch}}",
            12,
            y + 11,
            6,
            11,
            "none",
            thresholds((None, RED), (0.8, GREEN)),
            vmin=0,
            vmax=1,
            desc="Regra dos 4/5: abaixo de 0.80 indica possível discriminação.",
        ),
        bars(
            "Latência de inferência (ms)",
            "credit_model_inference_latency_ms",
            "{{batch}}",
            18,
            y + 11,
            6,
            11,
            "ms",
            thresholds((None, GREEN), (100, ORANGE)),
            vmax=None,
        ),
    ]
    y += 22
    p.append(row("Pipeline e alertas", y))
    y += 1
    p += [
        bars(
            "Linhas por lote",
            "credit_pipeline_rows_processed",
            "{{batch}}",
            0,
            y,
            6,
            10,
            "none",
            thresholds((None, BLUE)),
            vmax=None,
            decimals=0,
        ),
        table(
            "Violações do contrato por regra",
            "credit_contract_violations",
            6,
            y,
            8,
            10,
            # Grafana names the value column "Value" (or the metric name, depending on version)
            ["batch", "rule", "Value", "credit_contract_violations"],
            rename={
                "Value": "violações",
                "credit_contract_violations": "violações",
                "batch": "lote",
                "rule": "regra",
            },
        ),
        table(
            "Alertas disparados (Prometheus)",
            'ALERTS{alertstate="firing"}',
            14,
            y,
            10,
            10,
            ["severity", "alertname", "batch", "feature"],
            rename={"batch": "lote"},
        ),
    ]
    return {
        "uid": "sentrix-main",
        "title": "SENTRIX — Credit Risk Model Monitoring",
        "tags": ["sentrix", "mlops", "drift"],
        "timezone": "browser",
        "schemaVersion": 39,
        "version": 1,
        "editable": False,
        "refresh": "30s",
        "time": {"from": "now-1h", "to": "now"},
        "templating": {
            "list": [
                {
                    "name": "batch",
                    "label": "Lote",
                    "type": "query",
                    "datasource": DS,
                    "query": {"query": "label_values(credit_feature_psi, batch)", "refId": "batch"},
                    "definition": "label_values(credit_feature_psi, batch)",
                    "refresh": 1,
                    "sort": 2,
                    "current": {"text": "2025-12", "value": "2025-12"},
                    "includeAll": False,
                    "multi": False,
                }
            ]
        },
        "panels": p,
    }


if __name__ == "__main__":
    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(json.dumps(build(), indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"written {OUT}")
