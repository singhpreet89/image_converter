VENV := .venv
PYTHON := $(VENV)/bin/python
PIP := $(VENV)/bin/pip
STAMP := $(VENV)/.installed

APP_NAME := $(shell grep -m1 '^APP_NAME=' .env 2>/dev/null | cut -d '=' -f2)
APP_NAME := $(if $(APP_NAME),$(APP_NAME),image_converter)
LOG_FILE := storage/logs/$(APP_NAME).log

.DEFAULT_GOAL := help
.PHONY: help setup run dry-run logs clean-logs clean

help:
	@echo "Available targets:"
	@echo "  make setup       Create venv and install dependencies"
	@echo "  make run         Run the converter using .env settings (override with ARGS=\"...\")"
	@echo "  make dry-run     Preview what would be converted without writing any files"
	@echo "  make logs        Tail the run log ($(LOG_FILE))"
	@echo "  make clean-logs  Delete generated log files (keeps storage/logs/.gitignore)"
	@echo "  make clean       Remove the virtual environment"

setup: $(STAMP)

$(PYTHON):
	@echo "Virtual environment not found - creating $(VENV)..."
	python3 -m venv $(VENV)

$(STAMP): $(PYTHON) requirements.txt
	@echo "Installing/updating dependencies from requirements.txt..."
	$(PIP) install -q -r requirements.txt
	@touch $(STAMP)
	@echo "Dependencies up to date."

run: $(STAMP)
	$(PYTHON) convert.py $(ARGS)

dry-run: $(STAMP)
	$(PYTHON) convert.py --dry-run $(ARGS)

logs:
	tail -f $(LOG_FILE)

clean-logs:
	find storage/logs -type f -name '*.log' -delete

clean:
	rm -rf $(VENV)
