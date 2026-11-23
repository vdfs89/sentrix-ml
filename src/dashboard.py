"""Static, self-contained monitoring dashboard (Plotly) built from pipeline outputs and logs."""
from __future__ import annotations

import html
import json
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go
from plotly.subplots import make_subplots

from . import config
from .logging_utils import RUN_ID

BLUE, ORANGE, RED, GREEN, GREY = "#2563eb", "#f59e0b", "#dc2626", "#16a34a", "#6b7280"
LAYOUT = dict(template="plotly_white", height=360, margin=dict(l=50, r=20, t=50, b=40),
              font=dict(family="Inter, Segoe UI, sans-serif", size=12),
              legend=dict(orientation="h", y=-0.2))


def _fig_html(fig: go.Figure, first: bool = False) -> str:
    return fig.to_html(full_html=False, include_plotlyjs="cdn" if first else False,
                       config={"displayModeBar": False, "responsive": True})


def _load_run_logs() -> pd.DataFrame:
    rows = []
    if config.PIPELINE_LOG_FILE.exists():
        for line in config.PIPELINE_LOG_FILE.read_text(encoding="utf-8").splitlines():
            try:
                rec = json.loads(line)
            except json.JSONDecodeError:
                continue
            if rec.get("run_id") == RUN_ID:
                rows.append(rec)
    return pd.DataFrame(rows)


def build_dashboard(records: list[dict], alerts: list[dict], causal: list[dict], meta: dict) -> Path:
    base = meta["holdout_metrics"]
    ok = [r for r in records if r["validation_passed"]]
    months = [r["batch"] for r in ok]
    figs: list[str] = []

    # 1. Model health over time
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Scatter(x=months, y=[r["auc"] for r in ok], name="AUC (labels atrasados)",
                             mode="lines+markers", line=dict(color=BLUE, width=3)))
    fig.add_hline(y=base["auc"], line_dash="dot", line_color=BLUE,
                  annotation_text=f"AUC baseline {base['auc']:.3f}")
    fig.add_hline(y=base["auc"] - config.AUC_DROP_CRITICAL, line_dash="dash", line_color=RED,
                  annotation_text="limite crítico")
    fig.add_trace(go.Scatter(x=months, y=[r["observed_default_rate"] for r in ok],
                             name="Taxa de default observada", mode="lines+markers",
                             line=dict(color=RED)), secondary_y=True)
    fig.add_trace(go.Scatter(x=months, y=[1 - r["approval_rate"] for r in ok],
                             name="Taxa de reprovação do modelo", mode="lines+markers",
                             line=dict(color=ORANGE, dash="dot")), secondary_y=True)
    fig.update_layout(title="Saúde do modelo: AUC vs. inadimplência real", **LAYOUT)
    fig.update_yaxes(title_text="AUC", secondary_y=False)
    fig.update_yaxes(title_text="taxa", tickformat=".0%", secondary_y=True)
    figs.append(_fig_html(fig, first=True))

    # 2. Drift share + prediction drift
    fig = make_subplots(specs=[[{"secondary_y": True}]])
    fig.add_trace(go.Bar(x=months, y=[r["drift_share"] for r in ok], name="% features com drift crítico",
                         marker_color=[RED if r["drift_share"] >= config.DRIFT_SHARE_CRITICAL
                                       else ORANGE if r["drift_share"] > 0 else GREEN for r in ok]))
    fig.add_trace(go.Scatter(x=months, y=[r["prediction_psi"] for r in ok], name="PSI do score",
                             mode="lines+markers", line=dict(color=BLUE)), secondary_y=True)
    fig.add_hline(y=config.DRIFT_SHARE_CRITICAL, line_dash="dash", line_color=RED,
                  annotation_text="alerta dataset drift")
    fig.update_layout(title="Data drift e prediction drift por mês", **LAYOUT)
    fig.update_yaxes(tickformat=".0%", secondary_y=False)
    fig.update_yaxes(title_text="PSI", secondary_y=True)
    figs.append(_fig_html(fig))

    # 3. PSI heatmap
    feats = config.FEATURES
    z = [[r["psi"][f] for r in ok] for f in feats]
    fig = go.Figure(go.Heatmap(z=z, x=months, y=feats, colorscale=[[0, "#f0fdf4"], [0.1 / 0.6, "#fef08a"],
                                                                   [0.2 / 0.6, "#fb923c"], [1, "#b91c1c"]],
                               zmin=0, zmax=0.6, text=[[f"{v:.2f}" for v in row] for row in z],
                               texttemplate="%{text}", colorbar=dict(title="PSI")))
    fig.update_layout(title="PSI por feature × mês (≥0.10 moderado, ≥0.20 crítico)",
                      **{**LAYOUT, "height": 620})
    figs.append(_fig_html(fig))

    # 4. Causal decomposition
    cm = [c["month"] for c in causal]
    fig = make_subplots(rows=1, cols=2, subplot_titles=(
        "Mudança no PD médio atribuída (intervenção do)", "Queda de AUC: data drift vs concept drift"))
    keys = [k for k in causal[0] if k.startswith("score_shift_due_to::")]
    for k, color in zip(keys, [ORANGE, BLUE]):
        fig.add_trace(go.Bar(x=cm, y=[c[k] for c in causal], name=k.split("::")[1],
                             marker_color=color), row=1, col=1)
    fig.add_trace(go.Bar(x=cm, y=[c["auc_drop_data_drift"] for c in causal], name="AUC perdida por data drift",
                         marker_color=GREY), row=1, col=2)
    fig.add_trace(go.Bar(x=cm, y=[c["auc_drop_concept_drift"] for c in causal],
                         name="AUC perdida por concept drift", marker_color=RED), row=1, col=2)
    fig.update_layout(title="Análise causal da degradação", barmode="relative", **LAYOUT)
    figs.append(_fig_html(fig))

    # 5. Operations: latency, volume, fairness
    fig = make_subplots(rows=1, cols=2, subplot_titles=("Latência de inferência e volume", "Fairness (disparate impact por sexo)"),
                        specs=[[{"secondary_y": True}, {}]])
    fig.add_trace(go.Bar(x=[r["batch"] for r in records], y=[r["rows"] for r in records], name="linhas no lote",
                         marker_color=[GREY if r["validation_passed"] else RED for r in records]),
                  row=1, col=1)
    fig.add_trace(go.Scatter(x=months, y=[r["inference_latency_ms"] for r in ok], name="latência (ms)",
                             mode="lines+markers", line=dict(color=BLUE)), row=1, col=1, secondary_y=True)
    fig.add_trace(go.Scatter(x=months, y=[r["disparate_impact"] for r in ok], name="disparate impact",
                             mode="lines+markers", line=dict(color=GREEN)), row=1, col=2)
    fig.add_hline(y=config.DISPARATE_IMPACT_MIN, line_dash="dash", line_color=RED, row=1, col=2)
    fig.update_layout(title="Operação do pipeline e governança", **LAYOUT)
    figs.append(_fig_html(fig))

    # 6. Stage durations from structured logs
    logs = _load_run_logs()
    stage_html = "<p class='muted'>Sem logs para este run.</p>"
    if not logs.empty and "duration_ms" in logs:
        fin = logs[logs["status"].isin(["success", "blocked", "failed"]) & logs["duration_ms"].notna()]
        agg = fin.groupby(["stage", "status"]).agg(execucoes=("duration_ms", "size"),
                                                   duracao_media_ms=("duration_ms", "mean")).reset_index()
        fig = go.Figure()
        for status, color in (("success", GREEN), ("blocked", RED), ("failed", "#7f1d1d")):
            part = agg[agg["status"] == status]
            if len(part):
                fig.add_trace(go.Bar(x=part["stage"], y=part["execucoes"], name=status, marker_color=color,
                                     customdata=part["duracao_media_ms"].round(1),
                                     hovertemplate="%{x}: %{y} execuções<br>média %{customdata} ms"))
        fig.update_layout(title="Execuções por etapa do pipeline (logs estruturados)", barmode="stack", **LAYOUT)
        stage_html = _fig_html(fig)

    # Tables
    def row_cls(r):
        if not r["validation_passed"]:
            return "bad"
        return "bad" if r.get("n_alerts_critical") else ("warn" if r.get("n_alerts_warning") else "good")

    def rules_txt(r):
        return html.escape(", ".join(f"{k}x{v}" for k, v in r.get("failed_rules", {}).items()) or "-")

    def batch_row(r):
        ok_ = r["validation_passed"]
        auc = f"{r['auc']:.3f}" if r.get("auc") is not None else "-"
        link = (f"<a href='evidently/drift_report_{r['batch']}.html'>abrir</a>"
                if r.get("evidently_report") else "-")
        drifted = html.escape(", ".join(r.get("drifted_features", [])) or "-")
        return (f"<tr class='{row_cls(r)}'><td>{r['batch']}</td>"
                f"<td>{'APROVADO' if ok_ else 'BLOQUEADO'}</td><td>{r['rows']}</td>"
                f"<td>{rules_txt(r)}</td><td>{drifted}</td><td>{auc}</td>"
                f"<td>{r.get('n_alerts_critical', 0)} / {r.get('n_alerts_warning', 0)}</td>"
                f"<td>{link}</td></tr>")

    batch_rows = "".join(batch_row(r) for r in records)
    sev_order = {"CRITICAL": 0, "WARNING": 1}
    alert_rows = "".join(
        f"<tr class='{'bad' if a['severity'] == 'CRITICAL' else 'warn'}'><td>{a['batch']}</td>"
        f"<td>{a['severity']}</td><td>{a['rule']}</td><td>{html.escape(a['message'])}</td></tr>"
        for a in sorted(alerts, key=lambda a: (a["batch"], sev_order[a["severity"]])))

    last = ok[-1]
    blocked = sum(not r["validation_passed"] for r in records)
    n_crit = sum(a["severity"] == "CRITICAL" for a in alerts)
    kpis = [
        ("Lotes processados", f"{len(records)}", f"{blocked} bloqueado(s) pelo contrato"),
        ("AUC baseline → último mês", f"{base['auc']:.3f} → {last['auc']:.3f}", f"Δ {last['auc'] - base['auc']:+.3f}"),
        ("Features c/ drift crítico (último)", f"{last['n_drifted_features']}/{len(config.FEATURES)}",
         ", ".join(last["drifted_features"][:4]) + ("…" if len(last["drifted_features"]) > 4 else "")),
        ("Alertas", f"{len(alerts)}", f"{n_crit} críticos"),
        ("Inadimplência real (último)", f"{last['observed_default_rate']:.1%}", f"baseline {base['observed_default_rate']:.1%}"),
    ]
    kpi_html = "".join(f"<div class='kpi'><div class='kl'>{k}</div><div class='kv'>{v}</div><div class='ks'>{html.escape(s)}</div></div>"
                       for k, v, s in kpis)

    page = f"""<!doctype html><html lang="pt-BR"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>SENTRIX Monitoring</title>
<style>
:root {{ --bg:#f8fafc; --card:#fff; --fg:#0f172a; --muted:#64748b; --border:#e2e8f0; }}
body {{ margin:0; background:var(--bg); color:var(--fg); font-family:Inter,'Segoe UI',sans-serif; }}
header {{ padding:24px 32px; background:#0f172a; color:#f8fafc; }}
header h1 {{ margin:0 0 4px; font-size:22px; }} header p {{ margin:0; color:#cbd5e1; font-size:13px; }}
main {{ padding:24px 32px; max-width:1400px; margin:auto; }}
.kpis {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(210px,1fr)); gap:12px; margin-bottom:20px; }}
.kpi,.card {{ background:var(--card); border:1px solid var(--border); border-radius:10px; padding:14px 16px; }}
.kl {{ font-size:12px; color:var(--muted); }} .kv {{ font-size:22px; font-weight:700; margin:4px 0; }} .ks {{ font-size:12px; color:var(--muted); }}
.grid {{ display:grid; grid-template-columns:repeat(auto-fit,minmax(560px,1fr)); gap:16px; }}
.card {{ margin-bottom:16px; overflow-x:auto; }}
h2 {{ font-size:16px; margin:24px 0 10px; }}
table {{ border-collapse:collapse; width:100%; font-size:13px; }}
th,td {{ text-align:left; padding:6px 8px; border-bottom:1px solid var(--border); }}
tr.bad td:first-child {{ border-left:4px solid {RED}; }} tr.warn td:first-child {{ border-left:4px solid {ORANGE}; }}
tr.good td:first-child {{ border-left:4px solid {GREEN}; }} .muted {{ color:var(--muted); }}
@media (max-width:640px) {{ main,header {{ padding:16px; }} .grid {{ grid-template-columns:1fr; }} }}
</style></head><body>
<header><h1>SENTRIX — Continuous ML Observability &amp; Governance for Credit Risk</h1>
<p>Modelo {meta['model_name']} v{meta['version']} · pipeline run {RUN_ID} · threshold {meta['threshold']:.2f} ·
referência {meta['reference_rows']} linhas · atributos protegidos excluídos: {', '.join(meta['excluded_protected_attributes'])}</p></header>
<main>
<div class="kpis">{kpi_html}</div>
<div class="grid"><div class="card">{figs[0]}</div><div class="card">{figs[1]}</div></div>
<div class="card">{figs[2]}</div>
<div class="card">{figs[3]}</div>
<div class="grid"><div class="card">{figs[4]}</div><div class="card">{stage_html}</div></div>
<h2>Lotes: contrato de dados, drift e relatórios Evidently</h2>
<div class="card"><table><thead><tr><th>Lote</th><th>Contrato</th><th>Linhas</th><th>Regras violadas</th>
<th>Features c/ drift crítico (PSI≥{config.PSI_CRITICAL})</th><th>AUC</th><th>Alertas crit/warn</th><th>Evidently</th></tr></thead>
<tbody>{batch_rows}</tbody></table></div>
<h2>Alertas gerados ({len(alerts)})</h2>
<div class="card"><table><thead><tr><th>Lote</th><th>Severidade</th><th>Regra</th><th>Mensagem</th></tr></thead>
<tbody>{alert_rows}</tbody></table></div>
<p class="muted">Gerado por <code>python -m src.pipeline</code>. Métricas também exportadas em <code>reports/metrics.prom</code> (Prometheus) e no MLflow (<code>mlflow ui --backend-store-uri sqlite:///mlflow.db</code>).</p>
</main></body></html>"""
    out = config.REPORTS_DIR / "monitoring_dashboard.html"
    out.write_text(page, encoding="utf-8")
    return out
