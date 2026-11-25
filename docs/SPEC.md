# SPEC — Requisitos e rastreabilidade

Cada requisito do enunciado do Tech Challenge Fase 4 aparece abaixo com onde foi implementado, a evidência gerada e o teste que o protege.
Legenda: ✅ atendido · ➕ além do pedido.

## 1. Requisitos obrigatórios do repositório

| ID | Requisito (enunciado) | Implementação | Evidência | Teste / verificação |
|---|---|---|---|---|
| RQ-01 | Pipeline de validação de dados (data contracts) configurado | `src/contracts.py`, `src/validation.py` | `data/quarantine/*`, log `ingestion_blocked` | `tests/test_contracts.py` (9 testes) ✅ |
| RQ-02 | Scripts para simulação e detecção estatística de drift | `src/drift_simulation.py`, `src/drift_detection.py` | `data/production/drift_manifest.json`, `reports/drift_tables/*.csv` | `tests/test_drift.py` ✅ |
| RQ-03 | Dashboards ou relatórios HTML comprovando o monitoramento | Evidently + `src/dashboard.py` + Grafana | `reports/evidently/*.html`, `reports/monitoring_dashboard.html`, Grafana `:3100` | CI publica `reports/` como artefato ✅ |
| RQ-04 | Documentação consolidada de governança no README | `README.md` › Etapa 4 | — | revisão ✅ |
| RQ-05 | Histórico de commits semântico e organizado | Conventional Commits + tags SemVer | `git log`, `CHANGELOG.md` | — ✅ |

## 2. Bibliotecas requeridas

| ID | Requisito | Atendido com | Versão |
|---|---|---|---|
| RQ-06 | Scikit-Learn / XGBoost | XGBoost (modelo), scikit-learn (split e métricas) | 3.2.0 / 1.9.1 ✅ |
| RQ-07 | Great Expectations **ou** Pandera | Pandera | 0.36.0 ✅ |
| RQ-08 | Evidently AI (ou Alibi Detect) | Evidently AI | 0.7.23 ✅ |
| RQ-09 | Opcional: prometheus-client | prometheus-client + Pushgateway + Prometheus + Grafana | 0.26.0 ➕ |

## 3. Boas práticas obrigatórias

| ID | Requisito | Como foi atendido | Evidência |
|---|---|---|---|
| RQ-10 | Separação clara entre Referência (treino) e Produção (drift) | `src/ingestion.py`: 15.000 linhas de referência (meses 2025-01..06) e 14.965 de produção (2025-07..12). O modelo nunca vê produção | `data/reference/`, `data/production/` ✅ |
| RQ-11 | ≥ 3 regras rígidas de qualidade (ex.: nulos, idade, renda) | **10 regras** (R1–R10), entre elas sem nulos, idade 18–100, limite > 0 e ≤ 1M, schema estrito | `src/contracts.py` ✅ ➕ |
| RQ-12 | Relatório estatístico da mudança de distribuição (PSI ou KS) | **PSI e KS** próprios por feature + KS/PSI no Evidently | `reports/drift_tables/`, `reports/evidently/` ✅ ➕ |
| RQ-13 | Documentação de compliance com plano de privacidade (LGPD) | README › Etapa 4 (inventário, base legal, retenção, vieses) + Model Card | `README.md`, `docs/MODEL_CARD.md` ✅ |

## 4. Etapas

| ID | Etapa / tarefa | Implementação | Evidência | Teste |
|---|---|---|---|---|
| RQ-14 | E1 — Treinar classificador base em dataset limpo | `src/train.py`, contrato aplicado antes do treino | `models/`, MLflow `sentrix-training` (AUC 0,782) | pipeline no CI |
| RQ-15 | E1 — Contrato com regras estritas (idade > 18, renda não nula, sem duplicatas) | R1, R2, R3, R7 | `src/contracts.py` | `test_single_rule_violation`, `test_null_income_is_rejected` |
| RQ-16 | E1 — Forçar lote com erros e mostrar o contrato bloqueando | `make_corrupted_batch` → `2025-09_upstream_bug` (103 violações, 8 regras) | `data/quarantine/`, alerta CRITICAL | `test_corrupted_batch_is_blocked_with_every_rule_family` |
| RQ-17 | E2 — Dataset de produção alterando ≥ 2 variáveis importantes | inflação (`bill_amt*`, `pay_amt*`), novo perfil (`age`, `limit_bal`) + concept drift | `drift_manifest.json` | `test_data_drift_moves_intended_features` |
| RQ-18 | E2 — Gerar predições para o novo dataset | `src/pipeline.py::monitor_batch` | `reports/monitoring_records.json` | pipeline no CI |
| RQ-19 | E2 — Evidently comparando Referência × Produção | `evidently_report` (DataDrift + Classification) | `reports/evidently/drift_report_<mês>.html` | pipeline no CI |
| RQ-20 | E2 — Identificar via testes estatísticos quais features sofreram drift | PSI/KS por feature, severidade e precisão contra o gabarito | `reports/technical_findings.json` › F3 | `test_detection_flags_only_intentionally_drifted_features` |
| RQ-21 | E3 — Centralizar logs (sucesso/falha de ingestão e transformação) e resultados de drift | logs JSON por etapa (`stage()`), MLflow por lote | `logs/pipeline.jsonl`, MLflow `sentrix-monitoring` | — |
| RQ-22 | E3 — Rastreamento (MLflow) **ou** dashboard de métricas | **ambos**: MLflow + Prometheus/Grafana como código | `mlflow.db`, `docker-compose.yml`, `monitoring/` | `promtool test rules` (CI) ➕ |
| RQ-23 | E3 — Definir e documentar métricas de saúde | README › Etapa 3 (status, latência, volume, alertas, drift) | README | `test_registry_exposes_metrics_used_by_alert_rules` |
| RQ-24 | E4 — Plano de proteção de dados: PII, base legal, retenção | README › Etapa 4 | README | — |
| RQ-25 | E4 — Documentação de causalidade | `src/causal_analysis.py` (intervenções do(·)) + README › Análise causal | `reports/causal_analysis.json` | — |
| RQ-26 | E4 — Vídeo STAR ≤ 5 min | vídeo gravado | link no topo do README | **pendente: gravar** |

## 5. Requisitos não funcionais adotados

| ID | Requisito | Decisão |
|---|---|---|
| NF-01 | Reprodutibilidade | seed 42, versões exatas (`requirements.txt`), imagens Docker com tag fixa. Pipeline do zero gera os mesmos números |
| NF-02 | Privacy by Default | PII pseudonimizada na ingestão; schema estrito (R9); `data/` e audit store fora do git; logs sem PII |
| NF-03 | Monitoramento não pode derrubar o pipeline | falha no Pushgateway só gera WARNING (`export_prometheus`) |
| NF-04 | Alertas como código testado | `alerts.yml` + `alerts_test.yml` (promtool) no CI, paridade de 36 alertas com o relatório Python |
| NF-05 | Coexistência com outras stacks | portas 3100/9190 configuráveis por variável de ambiente |
| NF-06 | Versionamento independente | projeto (SemVer/tags), modelo (`model_metadata.json`), contrato (`_v1`) |

## 6. Critérios de avaliação × onde olhar

| Critério (peso) | Onde está |
|---|---|
| Validação de Dados (25%) | README › Etapa 1, `src/contracts.py`, `data/quarantine/`, `tests/test_contracts.py` |
| Detecção de Drift (25%) | README › Etapa 2 e Achados técnicos, `reports/evidently/`, `reports/technical_findings.json` |
| Observabilidade e Logs (20%) | README › Etapa 3, Grafana `:3100`, Prometheus `:9190`, `logs/pipeline.jsonl`, MLflow |
| Governança LGPD (15%) | README › Etapa 4, `docs/MODEL_CARD.md` |
| Vídeo STAR (15%) | link no README |
