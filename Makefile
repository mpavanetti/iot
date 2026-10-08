# IoT Center: common tasks. Run `make help` for the list.

PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
PLATFORM := docker compose -f platform/compose.yaml
LITE := docker compose -f lite/compose.yaml
SIMULATOR := $(PYTHON) simulator/simulate_picow.py

.DEFAULT_GOAL := help
.PHONY: help install lite lite-docker simulate backfill platform-up platform-down \
        platform-logs platform-reset tools rebuild-hourly test test-spark e2e lint format

help: ## List the available tasks
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-15s\033[0m %s\n", $$1, $$2}'

install: ## Create .venv with everything needed for development
	$(PYTHON) -m venv $(VENV)
	$(BIN)/pip install --upgrade pip
	$(BIN)/pip install -e ".[dev]"

# --- Lite ---------------------------------------------------------------------------------

lite: ## Run IoT Center Lite locally (dashboard :8000, devices :1500)
	$(BIN)/iotcenter lite

lite-docker: ## Run IoT Center Lite in Docker
	$(LITE) up -d --build

# --- Simulated boards -----------------------------------------------------------------------

simulate: ## Stream three simulated Pico W boards to localhost:1500
	$(SIMULATOR) --devices 3

backfill: ## Send a week of simulated history to localhost:1500
	$(SIMULATOR) --devices 3 --backfill 7d --backfill-only

# --- Platform -------------------------------------------------------------------------------

platform-up: ## Build and start the platform
	$(PLATFORM) up -d --build

platform-down: ## Stop the platform (data is kept)
	$(PLATFORM) down

platform-logs: ## Follow the platform's logs
	$(PLATFORM) logs -f --tail 50

platform-reset: ## Stop the platform and delete all of its data
	$(PLATFORM) down -v

tools: ## Start Kafka UI on :8090
	$(PLATFORM) --profile tools up -d kafka-ui

rebuild-hourly: ## Recompute the hourly aggregates with a Spark batch job
	$(PLATFORM) run --rm spark-rebuild

# --- Quality -----------------------------------------------------------------------------------

test: ## Unit and integration tests (no Docker needed)
	$(BIN)/pytest

test-spark: ## Spark transformation tests, inside the Spark image
	docker build -q -t iotcenter-spark-test platform/spark
	docker run --rm -u root -e PYTHONDONTWRITEBYTECODE=1 -v "$(CURDIR)/platform/spark:/work" iotcenter-spark-test bash -c '\
	  pip install -q pytest && cd /work && \
	  PYTHONPATH=/opt/spark/python:$$(ls /opt/spark/python/lib/py4j-*.zip):/work/jobs \
	  python3 -m pytest -q -p no:cacheprovider tests'

e2e: ## End-to-end tests against the running platform (make platform-up first)
	$(BIN)/pytest -m e2e tests/e2e

lint: ## Check style and lint
	$(BIN)/ruff check .
	$(BIN)/ruff format --check .

format: ## Format the code
	$(BIN)/ruff format .
	$(BIN)/ruff check --fix .
