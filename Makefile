# SENTRIX — task runner (Linux/macOS/WSL/CI). On Windows without make: .\make.ps1 <target>
# `make help` lists every target.

PYTHON      ?= python
PIP         ?= $(PYTHON) -m pip
PUSHGATEWAY ?= http://localhost:9091
PROM_IMAGE  ?= prom/prometheus:v2.55.1
RULES_DIR   := $(CURDIR)/monitoring/prometheus

.DEFAULT_GOAL := help
.PHONY: help install lint format test pipeline monitor findings push dashboard site serve \
        promtool up down logs check ci clean

help: ## List available targets
	@grep -E '^[a-zA-Z_-]+:.*?## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-10s\033[0m %s\n", $$1, $$2}'

install: ## Install pinned dependencies (runtime + dev)
	$(PIP) install -r requirements.txt -r requirements-dev.txt

lint: ## Ruff lint + format check
	$(PYTHON) -m ruff check .
	$(PYTHON) -m ruff format --check .

format: ## Auto-fix lint issues and format the code
	$(PYTHON) -m ruff check . --fix
	$(PYTHON) -m ruff format .

test: ## Unit and integration tests
	$(PYTHON) -m pytest -q

pipeline: ## Full pipeline: ingest -> train -> simulate drift -> monitor -> reports (pushes if the stack is up)
	SENTRIX_PUSHGATEWAY=$(PUSHGATEWAY) $(PYTHON) -m src.pipeline

monitor: ## Monitoring stage only, over existing batches
	SENTRIX_PUSHGATEWAY=$(PUSHGATEWAY) $(PYTHON) -m src.pipeline --monitor

findings: ## Reproduce the evidence of README > Technical findings
	$(PYTHON) -m src.technical_findings

push: ## Re-send the last run's metrics to the Pushgateway
	SENTRIX_PUSHGATEWAY=$(PUSHGATEWAY) $(PYTHON) -m src.push_metrics

dashboard: ## Regenerate the Grafana dashboard JSON from code
	$(PYTHON) monitoring/grafana/generate_dashboard.py

site: ## Build the GitHub Pages portal into site/ (needs a pipeline run)
	$(PYTHON) -m src.portal

serve: site ## Build and serve the portal at http://127.0.0.1:8765
	$(PYTHON) -m http.server 8765 --bind 127.0.0.1 -d site

promtool: ## Validate and unit-test the Prometheus alert rules
	docker run --rm -v "$(RULES_DIR):/rules" -w /rules --entrypoint promtool $(PROM_IMAGE) check rules alerts.yml
	docker run --rm -v "$(RULES_DIR):/rules" -w /rules --entrypoint promtool $(PROM_IMAGE) test rules alerts_test.yml

up: ## Start Pushgateway + Prometheus + Grafana (Grafana :3100, Prometheus :9190)
	docker compose up -d

down: ## Stop the observability stack (keeps volumes)
	docker compose down

logs: ## Tail the observability stack logs
	docker compose logs -f --tail=50

check: lint test promtool ## Everything CI checks, locally

ci: check pipeline ## check + full pipeline

clean: ## Remove caches (keeps data, models, reports)
	rm -rf .pytest_cache .ruff_cache site
	find . -type d -name __pycache__ -not -path "./.venv/*" -exec rm -rf {} +
