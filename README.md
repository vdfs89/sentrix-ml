<p align="center">
  <img src="docs/assets/capa.png" alt="SENTRIX — Continuous ML Observability &amp; Governance for Credit Risk" width="100%">
</p>

# SENTRIX

### Continuous ML Observability & Governance for Credit Risk

Projeto desenvolvido para o **Tech Challenge — Fase 4** da Pós-Tech em Machine Learning Engineering da FIAP.

Plataforma de observabilidade contínua para modelos de Credit Scoring, contemplando validação de dados, detecção de Data Drift e Concept Drift, monitoramento operacional e governança de dados.

Uma fintech de crédito tem um modelo de *credit scoring* em produção. Com inflação e novos perfis de clientes, a diretoria suspeita que ele está degradando em silêncio. Este repositório constrói a **camada de sustentação e confiabilidade** em volta desse modelo:

| Etapa | O que foi feito | Evidência gerada |
|---|---|---|
| 1. Validação de dados | Contrato **Pandera** com 10 regras rígidas, gate que bloqueia e coloca em quarentena lotes ruins | `src/contracts.py`, `data/quarantine/*`, alerta `ingestion_blocked` |
| 2. Drift | Simulação de 6 meses de produção (data drift + concept drift), PSI + KS próprios e relatórios **Evidently AI** | `reports/evidently/drift_report_<mês>.html`, `reports/drift_tables/*.csv` |
| 3. Observabilidade | Logs estruturados JSON, **MLflow** (treino + monitoramento por lote), stack **Pushgateway → Prometheus → Grafana** como código, 11 regras de alerta testadas com `promtool` | `logs/pipeline.jsonl`, `mlflow.db`, `docker-compose.yml`, `monitoring/`, `reports/monitoring_dashboard.html` |
| 4. Governança | Privacy by Design (pseudonimização, minimização, schema estrito), plano LGPD, auditoria de viés, análise causal | este README, `reports/causal_analysis.json` |

> 🎥 **Vídeo STAR (≤ 5 min):** _adicionar link aqui_

---

## Sumário
1. [Como executar](#como-executar)
2. [Arquitetura](#arquitetura)
3. [Dataset e separação Referência × Produção](#dataset-e-separação-referência--produção)
4. [Etapa 1 — Contratos de dados](#etapa-1--validação-de-dados-e-contratos)
5. [Etapa 2 — Simulação e detecção de drift](#etapa-2--simulação-e-detecção-de-drift)
6. [Etapa 3 — Observabilidade](#etapa-3--observabilidade-de-pipelines-e-modelos)
7. [Análise causal da degradação](#análise-causal-da-degradação)
8. [Etapa 4 — Governança e LGPD](#etapa-4--governança-e-lgpd)
9. [Lições aprendidas](#lições-aprendidas)
10. [Estrutura do repositório](#estrutura-do-repositório)
11. [Versionamento](#versionamento)

---

## Como executar

Requer Python 3.11.

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate   |   Linux/macOS: source .venv/bin/activate
pip install -r requirements.txt

# (opcional) segredo da pseudonimização — em produção vem de um secret manager
export CREDIT_PII_SALT="um-segredo-forte"

python -m src.pipeline          # ingestão → treino → simulação → monitoramento → relatórios (~30 s)
pytest -q                       # 17 testes: contratos, estatística de drift, regras de alerta, métricas

mlflow ui --backend-store-uri sqlite:///mlflow.db   # http://127.0.0.1:5000
```

Depois de rodar, abra **`reports/monitoring_dashboard.html`** no navegador. Etapas isoladas: `python -m src.ingestion`, `python -m src.train`, `python -m src.drift_simulation`, `python -m src.pipeline --monitor`.

### Stack de observabilidade (Docker)

```bash
docker compose up -d                                   # Pushgateway + Prometheus + Grafana
export SENTRIX_PUSHGATEWAY=http://localhost:9091       # PowerShell: $env:SENTRIX_PUSHGATEWAY="http://localhost:9091"
python -m src.pipeline                                 # ou só reenviar o último run: python -m src.push_metrics
```

| Serviço | URL | Observação |
|---|---|---|
| Grafana | http://localhost:3100 | dashboard **SENTRIX** abre como home (acesso anônimo de leitura; admin/`GRAFANA_ADMIN_PASSWORD`) |
| Prometheus | http://localhost:9190 | regras em *Alerts*, 36 alertas após o pipeline |
| Pushgateway | http://localhost:9091 | estado final de cada execução do job batch |

As portas do host fogem de 3000/9090 para a stack rodar ao lado de outros projetos; troque com `SENTRIX_GRAFANA_PORT`, `SENTRIX_PROMETHEUS_PORT` e `SENTRIX_PUSHGATEWAY_PORT`.

```bash
# validar e testar as regras de alerta
docker run --rm -v "$PWD/monitoring/prometheus:/rules" -w /rules --entrypoint promtool prom/prometheus:v2.55.1 test rules alerts_test.yml
```

O CI (`.github/workflows/ci.yml`) tem dois jobs: **alert-rules** (`promtool check` + `promtool test` e verificação de que o JSON do Grafana está em sincronia com o gerador) e **test-and-monitor** (pytest + pipeline completo, relatórios publicados como artefato).

---

## Arquitetura

```mermaid
flowchart LR
    A[UCI Credit Default<br/>OpenML 42477] --> B[Ingestão<br/>pseudonimização HMAC<br/>minimização]
    B -->|atributos protegidos| AU[(Audit store<br/>restrito)]
    B --> R[(Referência<br/>15.000 linhas)]
    B --> P[(Pool de produção<br/>14.965 linhas)]
    R --> V0{Contrato<br/>Pandera}
    V0 --> T[Treino XGBoost<br/>MLflow]
    P --> S[Simulação 6 meses<br/>data + concept drift]
    S --> V{Contrato<br/>Pandera}
    V -- falhou --> Q[(Quarentena<br/>+ alerta CRITICAL)]
    V -- ok --> I[Inferência em lote]
    I --> D[PSI · KS · Evidently]
    I --> PF[Performance c/ labels<br/>atrasados]
    I --> F[Fairness<br/>disparate impact]
    AU -.-> F
    D & PF & F --> AL[Regras de alerta]
    AL --> L[Logs JSON] & M[MLflow] & PR[metrics.prom] & DB[Dashboard HTML]
    PR --> PG[Pushgateway] --> PM[Prometheus<br/>11 regras de alerta] --> GF[Grafana<br/>dashboard as code]
    S --> C[Análise causal<br/>intervenções do]
    C --> DB
```

Cada etapa é instrumentada pelo context manager `stage()` (`src/logging_utils.py`), que emite eventos `started / success / blocked / failed` com `run_id`, `duration_ms` e contexto (lote, linhas, métricas).

---

## Dataset e separação Referência × Produção

* **Dataset:** *Default of Credit Card Clients* (UCI, Taiwan 2005) — 30.000 clientes, classificação binária (`default` no mês seguinte), baixado do OpenML (id 42477). Atende ao requisito de ≥ 5.000 amostras.
* **Limpeza:** colunas `x1..x23` renomeadas para nomes de negócio; códigos não documentados de escolaridade (0, 5, 6) e estado civil (0) mapeados para "outros"; 35 duplicatas removidas.
* **Separação clara** (embaralhamento com seed 42):
  * **Referência** — 15.000 linhas, meses `2025-01..2025-06` (era de treino). Treino 80% / holdout 20%, estratificado.
  * **Produção** — 14.965 linhas divididas em 6 lotes mensais `2025-07..2025-12`, onde o drift é injetado. **O modelo nunca vê esses dados no treino.**
* **Modelo baseline:** XGBoost (300 árvores, profundidade 4), 21 features, sem atributos protegidos. Threshold de decisão 0,23 escolhido por F1 no holdout.

| Métrica no holdout de referência | Valor |
|---|---|
| AUC | **0,782** |
| F1 / Recall / Precision | 0,553 / 0,642 / 0,485 |
| Taxa de aprovação | 70,5% |
| Taxa de default observada | 22,3% |

Features mais importantes: `pay_0` (status do último pagamento, 33%), `pay_2` (19%), `pay_3`, `pay_5`.

---

## Etapa 1 — Validação de dados e contratos

**Foco: lixo não entra no modelo.** O contrato (`src/contracts.py`) é um `DataFrameSchema` Pandera com validação *lazy* (coleta todas as violações de uma vez):

| Regra | Descrição |
|---|---|
| R1 | Sem nulos em features, id e target |
| R2 | Idade entre 18 e 100 (maioridade para crédito) |
| R3 | Limite de crédito > 0 e ≤ 1.000.000 |
| R4 | Escolaridade no domínio fechado {1, 2, 3, 4} |
| R5 | Status de pagamento `pay_*` entre −2 e 8 |
| R6 | `pay_amt*` ≥ 0 e `bill_amt*` entre −1M e 5M |
| R7 | `customer_key` único (sem clientes duplicados) |
| R8 | Target binário {0, 1} |
| R9 | **Schema estrito**: coluna não prevista é rejeitada — um CPF vazado pelo upstream nunca chega ao modelo (*Privacy by Default*) |
| R10 | Lote com ≥ 100 linhas e taxa de default < 80% (sanidade do upstream) |

**Gate de ingestão (`src/validation.py`):** se qualquer regra falhar, **o lote inteiro é bloqueado**, salvo em `data/quarantine/` (dados + `failure_cases.csv` + `validation_report.json`), um log `CRITICAL` com `alert=ingestion_blocked` é emitido e o lote não é pontuado. O próprio dataset de referência passa pelo contrato antes do treino.

**Demonstração:** o pipeline injeta um lote corrompido (`2025-09_upstream_bug`) simulando um bug do fornecedor de dados:

```
[CRITICAL] data_validation  DATA CONTRACT VIOLATED - batch blocked and quarantined
           batch=2025-09_upstream_bug
           failed_rules={'R1': 24, 'R2': 18, 'R3': 8, 'R4': 10, 'R5': 5, 'R6': 7, 'R7': 30, 'R9': 1}
```

Nulos no limite, menores de idade, idade 150, limite negativo, escolaridade inválida, status de pagamento 12, pagamento negativo, 15 clientes duplicados e uma coluna `cpf` vazada — **103 violações em 8 famílias de regras, todas interceptadas.**

---

## Etapa 2 — Simulação e detecção de drift

### Cenário simulado (`src/drift_simulation.py`, `config.DRIFT_SCHEDULE`)

| Mês | Inflação | Novo perfil | Concept flip | Default real |
|---|---|---|---|---|
| 2025-07 | 0% | 0% | 0% | 21,7% |
| 2025-08 | 0% | 0% | 0% | 20,9% |
| 2025-09 | 15% | 10% | 0% | 21,4% |
| 2025-10 | 40% | 20% | 10% | 25,4% |
| 2025-11 | 70% | 30% | 25% | 32,3% |
| 2025-12 | 100% | 40% | 40% | 38,0% |

* **Data drift (≥ 2 variáveis importantes):**
  * *Inflação* → faturas `bill_amt1..6` × (1 + inflação) e pagamentos reais `pay_amt1..6` × (1 − 0,5·inflação).
  * *Novo perfil de cliente* → parte da base vira jovens (21–29 anos, `age`) com limite 60% menor (`limit_bal`).
* **Concept drift:** clientes que *parecem* bons (`pay_0 ≤ 0`, em dia) mas com utilização do limite > 50% passam a inadimplir com probabilidade crescente. `P(default | X)` muda — o modelo continua confiante onde está errado.
* Cada lote original (pré-drift) é guardado em `data/production/original/` para a análise causal.

### Detecção (`src/drift_detection.py`)

Duas camadas complementares, comparando cada lote de produção com a referência:

1. **Testes próprios**, usados pelas regras de alerta:
   * **PSI** com bins por quantis da referência (contínuas) ou por categoria (discretas). < 0,10 estável · 0,10–0,20 moderado (WARNING) · ≥ 0,20 crítico (CRITICAL).
   * **Kolmogorov-Smirnov** (estatística D e p-valor) para as contínuas.
2. **Evidently AI** (`DataDriftPreset` com KS para numéricas e PSI para categóricas + `ClassificationPreset`), gerando um HTML por mês com drift por coluna, drift do score (`prediction`), drift do target e qualidade de classificação vs. referência.

### Resultado

| Lote | AUC | PSI do score | Features PSI ≥ 0,20 | Features PSI 0,10–0,20 | Colunas c/ drift (Evidently, KS) |
|---|---|---|---|---|---|
| 2025-07 | 0,796 | 0,004 | — | — | 1* |
| 2025-08 | 0,776 | 0,006 | — | — | 0 |
| 2025-09 | 0,774 | 0,030 | — | pay_amt4 | 9 |
| 2025-10 | 0,752 | 0,063 | — | — | 15 |
| 2025-11 | **0,700** | 0,194 | — | age, pay_amt1–6, bill_amt1–6 | 15 |
| 2025-12 | **0,683** | **0,364** | **pay_amt1–6, age** | limit_bal, bill_amt1–6 | 16 |

\* falso positivo do KS: com milhares de linhas o teste detecta diferenças minúsculas. Por isso os alertas usam PSI (magnitude) e o KS/Evidently funciona como sinal antecipado.

**Variáveis degradadas apontadas:** `pay_amt1..6` e `age` (críticas), `limit_bal` e `bill_amt1..6` (moderadas) — exatamente as que foram alteradas. As features de status de pagamento (`pay_0..6`), que não foram mexidas, ficaram estáveis: **zero falsos positivos de PSI.**

---

## Etapa 3 — Observabilidade de pipelines e modelos

### O que é coletado

| Camada | Onde | Conteúdo |
|---|---|---|
| **Logs estruturados** | `logs/pipeline.jsonl` | 1 evento JSON por início/fim de etapa: `ts, level, run_id, stage, status, duration_ms, batch, rows, …` + todos os alertas. Pronto para Datadog / Loki / CloudWatch. |
| **MLflow** (`mlflow.db`) | experimento `sentrix-training` | parâmetros, métricas do holdout, hash SHA-256 dos dados de referência, modelo e importâncias (linhagem) |
| | experimento `sentrix-monitoring` | 1 run pai por execução + 1 run filho por lote com todas as métricas, PSI por feature, alertas e o HTML do Evidently; métricas `timeline_*` com `step` = mês para gráficos no UI |
| **Prometheus** | `reports/metrics.prom` → Pushgateway → Prometheus | gauges por lote: `credit_pipeline_batch_validation_passed`, `credit_contract_violations{rule}`, `credit_feature_psi{feature}`, `credit_data_drift_share`, `credit_prediction_psi`, `credit_model_auc`, `credit_model_baseline{metric}`, `credit_observed_default_rate`, `credit_approval_rate`, `credit_disparate_impact`, `credit_model_inference_latency_ms`, `credit_pipeline_last_success_timestamp_seconds` |
| **Grafana** | `monitoring/grafana/` | dashboard provisionado (gerado por `generate_dashboard.py`): KPIs, AUC/drift/score por lote, PSI por feature com seletor de lote, default e aprovação, fairness, latência, violações do contrato por regra e alertas disparados |
| **Dashboard** | `reports/monitoring_dashboard.html` | KPIs, AUC × inadimplência, drift por mês, heatmap PSI feature × mês, decomposição causal, latência/volume, fairness, execuções por etapa (lidas dos logs), tabela de lotes e de alertas com links para cada relatório Evidently |
| **Alertas** | `reports/alerts.json` + logs | 36 alertas na execução de referência (12 críticos, 24 warnings) |

### Regras de alerta no Prometheus

`monitoring/prometheus/alerts.yml` replica as regras do relatório Python — as duas camadas produzem os mesmos **36 alertas**:

| Alerta | Expressão (resumo) | Severidade |
|---|---|---|
| `SentrixDataContractViolation` | `credit_pipeline_batch_validation_passed == 0` | critical |
| `SentrixFeatureDriftWarning` / `Critical` | `credit_feature_psi` ≥ 0,10 / ≥ 0,20 | warning / critical |
| `SentrixDatasetDrift` | `credit_data_drift_share >= 0.30` | critical |
| `SentrixPredictionDriftWarning` / `SentrixPredictionDrift` | `credit_prediction_psi` ≥ 0,10 / ≥ 0,20 | warning / critical |
| `SentrixConceptDrift` | AUC do lote < AUC baseline − 0,05 | critical |
| `SentrixLabelDrift` | \|default observado − referência\| ≥ 0,05 | warning |
| `SentrixFairnessDisparateImpact` | `credit_disparate_impact < 0.80` | warning |
| `SentrixPipelineStale` | última execução há mais de 35 dias | warning |
| `SentrixPushgatewayDown` | `up{job="pushgateway"} == 0` por 1 min | critical |

`alerts_test.yml` tem 7 grupos de teste para o `promtool` com valores reais do pipeline. Ele cobre casos que **devem** disparar, casos que **não** devem (AUC 0,752 fica acima do limite, PSI 0,02 é estável, pipeline com 30 dias) e a janela `for: 1m`. Também foi feito um teste de mutação: subir o limite crítico de PSI para 0,40 faz o `promtool` falhar, o que prova que os testes pegam regressões.

### Métricas de saúde definidas

| Métrica | Tipo | Regra de alerta |
|---|---|---|
| Status do contrato por lote | pipeline | lote bloqueado → **CRITICAL** `data_contract` |
| Volume (linhas por lote) e falhas de validação | pipeline | registrado; lote < 100 linhas viola R10 |
| Duração por etapa (`duration_ms`) e status | pipeline / infra | etapa `failed` vira evento `ERROR` com `error_type` |
| Latência de inferência (ms por lote e por 1k linhas) | infra | registrada (≈ 5 ms / 2.500 linhas) |
| PSI por feature | modelo — data drift | ≥ 0,10 WARNING · ≥ 0,20 CRITICAL |
| % de features com PSI crítico | modelo — dataset drift | ≥ 30% CRITICAL |
| PSI do score (prediction drift) | modelo | ≥ 0,10 WARNING · ≥ 0,20 CRITICAL |
| AUC com labels atrasados | modelo — concept drift | queda ≥ 0,05 vs. baseline → CRITICAL |
| Taxa de default observada | label drift | variação ≥ 5 p.p. → WARNING |
| Disparate impact por sexo | governança | < 0,80 → WARNING |

### Linha do tempo dos alertas

* **2025-09** — bloqueio do lote corrompido (CRITICAL) + primeiro WARNING de PSI.
* **2025-11** — **AUC cai 0,082 (0,782 → 0,700): alerta CRITICAL de concept drift**, mais 13 WARNINGs de PSI, prediction drift moderado (PSI do score 0,19) e label drift de +9,3 p.p.
* **2025-12** — 7 features com drift crítico, 33% do dataset (CRITICAL `dataset_drift`), score com PSI 0,36 (CRITICAL `prediction_drift`) e AUC 0,683.

> O monitoramento de performance (com labels) disparou **um mês antes** do drift crítico de entrada: monitorar só a distribuição de X não basta.

---

## Análise causal da degradação

Monitorar mostra **que** o modelo piorou; a análise causal (`src/causal_analysis.py`) mostra **por quê**. Como a simulação guarda a versão original de cada lote, aplicamos **intervenções do(·)**: restauramos um grupo de variáveis (ou os rótulos) ao valor original, mantendo o resto com drift, e medimos quanto do efeito some.

```mermaid
flowchart LR
    E[Choque econômico] --> INF[Inflação]
    E --> NP[Novo perfil de clientes]
    INF --> B[bill_amt ↑]
    INF --> PA[pay_amt ↓]
    NP --> AG[age ↓]
    NP --> LB[limit_bal ↓]
    B & LB --> U[Utilização do limite ↑]
    U --> Y[Inadimplência real ↑<br/>mesmo entre bons pagadores]
    B & PA & AG & LB --> S[Score do modelo muda<br/>data drift]
    Y --> CD[Relação X→y muda<br/>concept drift]
    CD --> AUC[AUC ↓ e defaults aprovados ↑]
```

Resultados (`reports/causal_analysis.json`):

| Mês | Δ PD médio total | devido à inflação | devido ao novo perfil | AUC perdida por data drift | AUC perdida por concept drift | Defaults aprovados pelo modelo (orig. → obs.) |
|---|---|---|---|---|---|---|
| 2025-10 | +0,022 | +0,020 | +0,004 | 0,000 | **0,028** | 196 → 238 |
| 2025-11 | +0,041 | +0,036 | +0,008 | 0,026 | **0,066** | 203 → 336 |
| 2025-12 | +0,056 | +0,050 | +0,010 | 0,023 | **0,080** | 194 → 355 |

**Diagnóstico:**
1. **A mudança no score é causada principalmente pela inflação** (~90% do deslocamento do PD médio); o novo perfil contribui com ~10%.
2. **A perda de capacidade de discriminação é causada principalmente pelo concept drift** (≈ 78% da queda de AUC em dezembro). O modelo não está "vendo dados estranhos": está aplicando uma relação que deixou de valer.
3. **Impacto de negócio:** em dezembro o modelo aprova **355 clientes que inadimplem** contra 194 no cenário sem choque (+83%), enquanto *também* reprova mais (aprovação de 68,9% → 52,6%). Degrada em risco **e** em receita.

**Prescrição:**
* Concept drift não se resolve recalibrando: **retreinar com dados rotulados recentes** (janela deslizante ou ponderada) e incluir variáveis de capacidade de pagamento sensíveis a ciclo econômico (ex.: utilização do limite `bill_amt/limit_bal`, comprometimento de renda).
* Gatilho de retreino: alerta CRITICAL de `concept_drift_performance` **ou** `dataset_drift` por 2 meses seguidos, com aprovação humana (champion/challenger no MLflow antes da promoção).
* Enquanto não retreina: política conservadora temporária (subir o threshold para os segmentos com utilização > 50%) e revisão manual das propostas na fronteira de decisão.

---

## Etapa 4 — Governança e LGPD

### Inventário de dados pessoais e tratamento (Privacy by Design)

| Dado | Classificação | Tratamento no projeto |
|---|---|---|
| `customer_id`, `cpf` | identificadores diretos (sintéticos, gerados para simular a operação real) | **Pseudonimizados** com HMAC-SHA256 + segredo (`CREDIT_PII_SALT`) em `customer_key` e **descartados** na ingestão. Sem o segredo o hash não é revertido nem recalculado por força bruta sobre CPFs. |
| `sex`, `marriage` | dados pessoais com **alto risco de discriminação** | **Nunca usados como feature.** Separados em `data/audit/` (acesso restrito, fora do git), ligados só pelo pseudônimo e usados exclusivamente para auditoria de viés. |
| `age`, `education` | dados pessoais, potenciais *proxies* | Usados como feature (relevância de risco documentada) e monitorados (drift de `age` é alerta). Avaliar remoção na próxima versão — ver vieses abaixo. |
| `limit_bal`, `bill_amt*`, `pay_amt*`, `pay_*` | dados financeiros/comportamentais | Necessários à finalidade (avaliar risco de crédito). Contrato garante domínio e ausência de nulos. |

**Privacy by Default no código:**
* **Minimização (LGPD art. 6º, III):** o modelo recebe só as 21 features listadas em `config.FEATURES`.
* **Schema estrito (R9):** qualquer coluna extra — inclusive um CPF vazado — bloqueia o lote.
* **Segregação:** atributos protegidos vivem em outro armazenamento; dados, banco MLflow e auditoria estão no `.gitignore`.
* **Logs sem PII:** os eventos registram lote, contagens e métricas; nenhum identificador de cliente.
* **Rastreabilidade (art. 37):** cada execução tem `run_id`; o modelo registra hash SHA-256 dos dados de treino, parâmetros e versão no MLflow (linhagem dado → modelo → decisão).

### Base legal para a decisão de crédito

* **Art. 7º, X — proteção do crédito:** base principal para tratar dados financeiros e comportamentais na avaliação de risco.
* **Art. 7º, V — execução de contrato / procedimentos preliminares** a pedido do titular (a proposta de crédito).
* **Não se usa consentimento como base**, porque recusar o tratamento inviabilizaria a análise — consentimento não seria livre.
* **Art. 20 — revisão de decisões automatizadas:** o titular pode pedir revisão da decisão e informações sobre os critérios. Atendemos com (i) importância de features versionada no MLflow, (ii) threshold e versão do modelo registrados por decisão, (iii) fila de revisão humana para contestações.
* **Art. 6º, IX — não discriminação:** atributos protegidos excluídos + monitoramento contínuo de disparate impact.

### Plano de retenção

| Artefato | Retenção | Justificativa / descarte |
|---|---|---|
| Dados brutos com identificadores diretos | não armazenados após a ingestão | pseudonimização imediata (minimização) |
| Base pseudonimizada de referência (treino) | enquanto o modelo treinado com ela estiver em uso + 5 anos | auditoria e reprodutibilidade de decisões; descarte seguro ao fim (art. 16) |
| Lotes de produção e predições | 5 anos após o fim da relação contratual | obrigações legais/regulatórias de guarda de registros de crédito (art. 16, I); após o prazo, eliminação ou anonimização |
| Lotes em quarentena | 90 dias | só para diagnóstico do fornecedor; depois, eliminação |
| Atributos protegidos (audit store) | mesmo prazo da base a que se referem, acesso restrito | só para auditoria de viés |
| Logs operacionais | 1 ano | sem PII; troubleshooting e auditoria |
| Métricas agregadas, relatórios de drift, runs MLflow | indefinido | dados agregados/anonimizados (art. 12) |

Informação negativa de crédito respeita o limite de 5 anos do CDC (art. 43, §1º). O titular pode pedir eliminação; havendo obrigação legal de guarda, os dados ficam bloqueados para outros usos até o fim do prazo.

### Mitigação de vieses

* **Exclusão de atributos protegidos** (sexo, estado civil) das features.
* **Auditoria contínua:** disparate impact (aprovação do grupo menos favorecido ÷ mais favorecido, regra dos 4/5) por lote. Resultado: **0,91–0,98 em todos os meses** (acima de 0,80), sem alerta.
* **Riscos remanescentes documentados:**
  * *Proxies:* `age` e `education` podem carregar viés indireto. `age` sofreu drift (novo público jovem) — monitorar aprovação por faixa etária e testar o modelo sem `age` no próximo ciclo.
  * *Viés histórico do rótulo:* inadimplência passada reflete políticas de crédito passadas (só observamos default de quem foi aprovado — *reject inference*).
  * *Viés de população:* dataset de Taiwan/2005; antes de uso real seria necessário revalidar com a população brasileira.
* **Segurança (art. 46) e incidentes (art. 48):** segredo de pseudonimização em secret manager, acesso mínimo ao audit store, comunicação à ANPD e aos titulares em caso de incidente relevante.
* **RIPD (art. 38):** este README funciona como base do Relatório de Impacto à Proteção de Dados do modelo.

---

## Lições aprendidas

1. **Drift de entrada não basta.** O alerta mais valioso (AUC −0,08 em 2025-11) veio da performance com labels, um mês antes de o PSI ficar crítico.
2. **KS em amostras grandes é sensível demais** (falso positivo no mês sem drift). PSI dá a magnitude; KS/Evidently servem de sinal precoce.
3. **Contrato de dados é a primeira linha de defesa**, inclusive de privacidade: o schema estrito impede que PII chegue ao modelo.
4. **Causalidade muda a resposta.** Data drift pede recalibração; concept drift pede retreino com dados novos. Saber qual domina evita a correção errada.
5. **Observabilidade precisa de um formato comum.** Logs JSON + MLflow + Prometheus a partir dos mesmos registros deixam qualquer ferramenta (Grafana, Datadog) consumir sem retrabalho.

---

## Estrutura do repositório

```
├── src/
│   ├── config.py            # caminhos, features, thresholds, cenário de drift
│   ├── ingestion.py         # download, limpeza, pseudonimização, split referência/produção
│   ├── contracts.py         # contrato Pandera (R1–R10)
│   ├── validation.py        # gate de ingestão, quarentena, lote corrompido de demonstração
│   ├── train.py             # XGBoost baseline + MLflow
│   ├── drift_simulation.py  # inflação, novo perfil, concept drift
│   ├── drift_detection.py   # PSI, KS, relatórios Evidently
│   ├── fairness.py          # disparate impact
│   ├── causal_analysis.py   # intervenções do(·) e decomposição da degradação
│   ├── monitoring.py        # MLflow, regras de alerta, Prometheus
│   ├── dashboard.py         # dashboard HTML (Plotly)
│   ├── logging_utils.py     # logs JSON estruturados
│   ├── push_metrics.py      # reenvia o último run ao Pushgateway
│   └── pipeline.py          # orquestração ponta a ponta
├── tests/                   # pytest
├── reports/                 # dashboard, Evidently, drift tables, alertas, métricas
├── models/                  # modelo + metadados (versão, hash dos dados, métricas)
├── logs/pipeline.jsonl      # logs estruturados
├── monitoring/
│   ├── prometheus/          # prometheus.yml, alerts.yml (11 regras), alerts_test.yml (promtool)
│   └── grafana/             # provisioning, dashboards/sentrix.json, generate_dashboard.py
├── docker-compose.yml       # Pushgateway + Prometheus + Grafana
├── docs/                    # capa
├── CHANGELOG.md
└── .github/workflows/ci.yml
```

## Versionamento

| O quê | Versão | Onde |
|---|---|---|
| Projeto SENTRIX | **1.1.0** ([SemVer](https://semver.org/lang/pt-BR/)) | tags git `v1.0.0`, `v1.1.0` · `src/__init__.py` · [`CHANGELOG.md`](CHANGELOG.md) |
| Modelo `credit-scoring-xgb` | 1.0.0 | `models/model_metadata.json` + MLflow (com hash SHA-256 dos dados de treino) |
| Contrato de dados | `credit_scoring_input_contract_v1` | `src/contracts.py` |
| Python | 3.11 | CI e ambiente local |
| Dependências Python | versões exatas | `requirements.txt` (pandas 2.3.3, scikit-learn 1.9.1, XGBoost 3.2.0, Pandera 0.36.0, Evidently 0.7.23, MLflow 3.18.0, prometheus-client 0.26.0…) |
| Imagens Docker | tags fixas | Prometheus v2.55.1 · Pushgateway v1.9.0 · Grafana 11.3.0 |

Projeto, modelo e contrato têm versões independentes: mudar o dashboard não muda o modelo, e retreinar o modelo não muda o contrato.
