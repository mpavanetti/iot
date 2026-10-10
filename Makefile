# IoT Center: common tasks. Run `make help` for the list.

PYTHON ?= python3
VENV ?= .venv
BIN := $(VENV)/bin
PLATFORM := docker compose -f platform/compose.yaml
# Prints the links, pipeline health and boards of the edition whose dashboard is published by
# $(1) (a compose command) for service $(2). Stdlib only, so any Python 3 can run it. $(3): extra flags.
status = port=$$($(1) port $(2) 8000 2>/dev/null | head -1 | cut -d: -f2); \
	if [ -z "$$port" ]; then echo "Not running."; exit 1; fi; \
	$(PYTHON) src/iotcenter/status.py http://localhost:$$port $(3)
# Lite runs from lite/, so lite/.env (and a COMPOSE_FILE set there) applies
LITE := cd lite && docker compose
SIMULATOR := $(PYTHON) simulator/simulate_picow.py

.DEFAULT_GOAL := help
.PHONY: help install lite lite-docker lite-docker-usb lite-docker-camera lite-down lite-status \
        lite-camera-demo firmware simulate backfill \
        platform-up platform-status platform-down platform-purge \
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
	@$(call status,$(LITE),lite,--wait 120)

lite-docker-usb: ## Run IoT Center Lite in Docker, also reading a Pico W on USB
	$(LITE) -f compose.yaml -f compose.usb.yaml up -d --build
	@$(call status,$(LITE),lite,--wait 120)

lite-docker-camera: ## Run IoT Center Lite in Docker with a Pico W on USB and a USB webcam (lite/.env)
	$(LITE) -f compose.yaml -f compose.usb.yaml -f compose.camera.yaml up -d --build
	@$(call status,$(LITE),lite,--wait 120)

lite-camera-demo: ## Run IoT Center Lite locally with the demo camera and sounds (no webcam needed)
	$(BIN)/iotcenter lite --camera demo --microphone demo

lite-down: ## Stop Lite in Docker (data is kept)
	$(LITE) down

lite-status: ## Links, pipeline health and boards of Lite in Docker
	@$(call status,$(LITE),lite)

# --- Firmware -------------------------------------------------------------------------------

# The board's USB serial port: its stable name on Linux, else mpremote's own search
PICO_PORT ?= $(or $(firstword $(wildcard /dev/serial/by-id/usb-MicroPython*)),auto)

firmware: ## Upload firmware/ to a Pico W on USB (pauses Lite in Docker, which shares the port)
	@if [ "$(PICO_PORT)" != auto ] && ! [ -r "$(PICO_PORT)" -a -w "$(PICO_PORT)" ]; then \
	  echo "Cannot open the Pico W ($(PICO_PORT)): this shell is not in the dialout group yet."; \
	  echo "  Now:     sg dialout -c 'make firmware'"; \
	  echo "  For good: log out and in (VS Code: run 'Remote-SSH: Kill VS Code Server on Host', then reconnect)"; \
	  exit 1; \
	fi
	-$(LITE) stop
	cd firmware && $(CURDIR)/$(BIN)/mpremote connect $(PICO_PORT) cp -r lib : + \
	  cp config.py main.py telemetry.py link.py hardware.py : + reset; \
	  status=$$?; cd $(CURDIR)/lite && docker compose start; exit $$status

# --- Simulated boards -----------------------------------------------------------------------

simulate: ## Stream three simulated Pico W boards to localhost:1500
	$(SIMULATOR) --devices 3

backfill: ## Send a week of simulated history to localhost:1500
	$(SIMULATOR) --devices 3 --backfill 7d --backfill-only

# --- Platform -------------------------------------------------------------------------------

platform-up: ## Build and start the platform
	$(PLATFORM) up -d --build
	@$(call status,$(PLATFORM),web,--wait 180)

platform-status: ## Links, pipeline health and boards of the platform
	@$(call status,$(PLATFORM),web)

platform-down: ## Stop the platform (data is kept)
	$(PLATFORM) down

platform-logs: ## Follow the platform's logs
	$(PLATFORM) logs -f --tail 50

platform-reset: ## Stop the platform and delete all of its data
	$(PLATFORM) down -v

platform-purge: ## Delete the platform completely: containers, data and images (Lite is kept)
	$(PLATFORM) --profile '*' down -v --rmi all --remove-orphans

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
