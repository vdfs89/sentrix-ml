# Model Card — `credit-scoring-xgb` v1.0.0

Formato inspirado em *Model Cards for Model Reporting* (Mitchell et al., 2019). Projeto SENTRIX v1.x.

## 1. Identificação

| Campo | Valor |
|---|---|
| Nome / versão | `credit-scoring-xgb` 1.0.0 |
| Tipo | Classificação binária: probabilidade de inadimplência (PD) no mês seguinte |
| Algoritmo | XGBoost 3.2.0 — 300 árvores, profundidade 4, learning rate 0,05, subsample/colsample 0,8 |
| Threshold de decisão | **0,23, fixo como política de crédito versionada** (`config.DECISION_THRESHOLD`). PD ≥ 0,23 → recusa. O ótimo por F1 no holdout é recalculado a cada treino só como informação (hoje também 0,23), ver README › A8 |
| Treinado em | 2026-11-23, sobre 12.000 linhas (80% da referência), seed 42 |
| Linhagem | hash SHA-256 dos dados de referência `5e6943cc2ddabb82…` em `models/model_metadata.json` e no MLflow (`sentrix-training`) |
| Responsável | Vitor Diogo Fonseca da Silva (RM375157) — Tech Challenge Fase 4, PosTech MLE FIAP |

## 2. Uso pretendido

* **Uso:** apoio à decisão de concessão/manutenção de crédito rotativo, sempre com revisão humana disponível (LGPD art. 20).
* **Usuários:** time de risco de crédito e operação de MLOps (monitoramento).
* **Fora do escopo:** decisão 100% automática sem possibilidade de revisão; precificação de juros; cobrança; qualquer uso com populações fora de cartão de crédito rotativo; uso real sem revalidação com dados brasileiros.

## 3. Dados

| | |
|---|---|
| Fonte | *Default of Credit Card Clients* (UCI, Taiwan, 2005), OpenML 42477, 30.000 clientes |
| Referência | 15.000 linhas (treino 12.000 / holdout 3.000, estratificado). Taxa de default 22,3% |
| Produção | 14.965 linhas em 6 lotes mensais, com drift simulado (ver README › Etapa 2) |
| Features (21) | `limit_bal`, `education`, `age`, `pay_0..6` (status de pagamento), `bill_amt1..6` (faturas), `pay_amt1..6` (pagamentos) |
| **Excluídos de propósito** | `sex`, `marriage` (risco de discriminação) e identificadores diretos (pseudonimizados e descartados) |
| Pré-processamento | códigos não documentados de escolaridade/estado civil → "outros"; 35 duplicatas removidas; contrato Pandera R1–R10 aplicado antes do treino |

## 4. Desempenho (holdout da referência, n = 3.000)

| AUC | Accuracy | Precision | Recall | F1 | Aprovação | Default observado |
|---|---|---|---|---|---|---|
| **0,782** | 0,768 | 0,485 | 0,642 | 0,553 | 70,5% | 22,3% |

Importância das features (gain): `pay_0` 33%, `pay_2` 19%, `pay_3` 8%, `pay_5` 7% — **o histórico recente de atraso domina o modelo**.

### Desempenho por subgrupo (holdout)

| Subgrupo | n | AUC | Aprovação | Default real |
|---|---|---|---|---|
| Sexo masculino | 1.183 | 0,770 | 66,2% | 24,3% |
| Sexo feminino | 1.817 | 0,790 | 73,3% | 21,1% |
| Idade 21–29 | 980 | 0,796 | 71,2% | 21,5% |
| Idade 30–39 | 1.076 | 0,794 | 75,2% | 21,0% |
| Idade 40–49 | 669 | 0,741 | 67,1% | 24,7% |
| Idade 50+ | 275 | 0,796 | **57,5%** | 24,7% |

* **Sexo:** disparate impact 0,90 no holdout e 0,91–0,98 em produção, acima da regra dos 4/5. Monitorado por lote (alerta `SentrixFairnessDisparateImpact`).
* **Idade — risco identificado:** a aprovação de clientes com 50 anos ou mais é 57,5%, contra 75,2% na faixa 30–39, uma razão de **0,76, abaixo de 0,80**. Parte da diferença acompanha um default real maior (24,7% contra 21,0%), mas `age` é feature do modelo e pode atuar como proxy. Ver seção 6.

## 5. Monitoramento em produção

| Sinal | Limite | Onde |
|---|---|---|
| Contrato de dados (R1–R10) | qualquer violação bloqueia o lote | `src/contracts.py`, alerta `SentrixDataContractViolation` |
| PSI por feature | ≥ 0,10 warning · ≥ 0,20 critical | `SentrixFeatureDrift*` |
| % de features com PSI crítico | ≥ 30% | `SentrixDatasetDrift` |
| PSI do score | ≥ 0,10 / ≥ 0,20 | `SentrixPredictionDrift*` |
| AUC com labels atrasados | queda ≥ 0,05 | `SentrixConceptDrift` |
| Taxa de default observada | desvio ≥ 5 p.p. | `SentrixLabelDrift` |
| Disparate impact (sexo) | < 0,80 | `SentrixFairnessDisparateImpact` |

**Comportamento observado no cenário simulado:** a AUC caiu para 0,700 em 2025-11 e 0,683 em 2025-12. A análise causal atribui cerca de 78% dessa perda ao **concept drift** (README › Análise causal).

## 6. Limitações e riscos conhecidos

1. **População e época:** Taiwan, 2005. Não representa o mercado brasileiro atual. Exige revalidação antes de qualquer uso real.
2. **Viés de seleção do rótulo:** só se observa default de quem foi aprovado no passado (*reject inference* não tratado).
3. **Idade como proxy:** razão de aprovação 0,76 para 50+ no holdout. **Ação recomendada para a v1.1 do modelo:** (a) incluir o disparate impact por faixa etária no monitoramento, (b) treinar uma variante sem `age` e comparar AUC e fairness no MLflow, (c) documentar a justificativa de negócio se `age` for mantida.
4. **Sensibilidade a ciclo econômico:** o modelo depende de atraso recente (`pay_*`) e não tem variável de comprometimento de renda. Por isso não antecipa um choque inflacionário que atinja bons pagadores (concept drift).
5. **Threshold único** para todos os segmentos. Não há calibração por segmento.
6. **Drift simulado:** os cenários de produção são sintéticos e servem para demonstrar a camada de sustentação, não para medir o desempenho real do modelo no mercado.

## 7. Considerações éticas e LGPD

* Base legal: art. 7º, X (proteção do crédito) e V (procedimentos preliminares a contrato). Não se usa consentimento.
* Direito à revisão (art. 20): versão do modelo, threshold e importâncias de features registradas. Contestação vai para revisão humana.
* Minimização (art. 6º, III): 21 features, sem atributos protegidos. Um schema estrito impede a entrada de PII.
* Detalhes de retenção, segurança e RIPD: README › Etapa 4.

## 8. Política de retreino

* **Gatilho:** `SentrixConceptDrift` **ou** `SentrixDatasetDrift` por 2 lotes consecutivos.
* **Processo:** retreinar com janela recente rotulada. Registrar o modelo challenger no MLflow e comparar AUC, calibração e fairness (sexo **e** idade) com o champion no mesmo holdout recente. A promoção exige aprovação humana e incremento de versão (patch para retreino com os mesmos dados e features, minor para novas features, major para mudança de algoritmo ou de contrato).
