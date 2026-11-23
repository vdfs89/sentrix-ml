# Changelog

Formato baseado em [Keep a Changelog](https://keepachangelog.com/pt-BR/1.1.0/), versionamento [SemVer](https://semver.org/lang/pt-BR/).

Versões independentes:
- **Projeto SENTRIX** — tags git `vX.Y.Z` e `src/__init__.py::__version__`.
- **Modelo** `credit-scoring-xgb` — `models/model_metadata.json::version` (muda só quando o modelo é retreinado).

## [1.1.0] — 2026-11-23

### Adicionado
- Stack de observabilidade como código (`docker-compose.yml`): Pushgateway v1.9.0 → Prometheus v2.55.1 → Grafana 11.3.0.
- Envio de métricas do pipeline ao Pushgateway (`SENTRIX_PUSHGATEWAY`) e `python -m src.push_metrics`.
- Novas métricas Prometheus: `credit_prediction_psi`, `credit_disparate_impact`, `credit_approval_rate`, `credit_observed_default_rate`, `credit_contract_violations{rule}`, `credit_model_baseline{metric}`, `credit_pipeline_last_success_timestamp_seconds`.
- 11 regras de alerta no Prometheus (`monitoring/prometheus/alerts.yml`), equivalentes às regras do relatório Python (36 alertas nas duas camadas).
- Testes unitários das regras com `promtool test rules` (`alerts_test.yml`), incluindo casos negativos e o `for: 1m`.
- Dashboard Grafana provisionado e gerado por código (`monitoring/grafana/generate_dashboard.py`).
- Job de CI que valida e testa as regras de alerta com `promtool`.
- Teste garantindo que toda métrica usada nas regras é exportada.

### Alterado
- `requirements.txt` com versões exatas testadas.
- Portas da stack no host: Grafana 3100 e Prometheus 9190 (configuráveis via `SENTRIX_*_PORT`), para rodar ao lado de outras stacks.

## [1.0.0] — 2026-11-23

### Adicionado
- Ingestão do UCI Credit Default (OpenML 42477) com pseudonimização HMAC-SHA256 e segregação de atributos protegidos.
- Contrato de dados Pandera (R1–R10) com gate de ingestão e quarentena.
- Modelo baseline XGBoost (AUC 0,782 no holdout), rastreado no MLflow.
- Simulação de 6 meses de produção com data drift e concept drift.
- Detecção com PSI, KS e relatórios Evidently AI.
- Logs JSON estruturados, MLflow por lote, `metrics.prom` e dashboard HTML.
- Auditoria de fairness (disparate impact) e análise causal com intervenções do(·).
- README com governança e LGPD e CI no GitHub Actions.
- Identidade SENTRIX e capa.

[1.1.0]: https://github.com/vdfs89/sentrix-ml/compare/v1.0.0...v1.1.0
[1.0.0]: https://github.com/vdfs89/sentrix-ml/releases/tag/v1.0.0
