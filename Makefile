VERSION := $(shell sed -n 's/^version = "\(.*\)"/\1/p' pyproject.toml | head -1)
LOCAL_INFOOS_DATA_HOME ?= $(CURDIR)/data
DIAGNOSTICS_BASE_DIR ?= $(CURDIR)/.infoos-diagnostics
DIAGNOSTICS_INFOOS_DATA_HOME ?= $(DIAGNOSTICS_BASE_DIR)/data

.PHONY: venv install install-web frontend-install frontend-test frontend-build extension-install extension-test extension-build extension-verify test run-web diagnostics-guard diagnostics-run diagnostics-fetch

venv:
	python3 -m venv .venv

install:
	bash scripts/bootstrap_env.sh

install-web:
	bash scripts/bootstrap_env.sh

frontend-install:
	npm --prefix frontend install

frontend-test:
	npm --prefix frontend test -- --run

frontend-build:
	npm --prefix frontend run build

extension-install:
	npm --prefix clients/save-to-infoos install

extension-test:
	npm --prefix clients/save-to-infoos test

extension-build:
	npm --prefix clients/save-to-infoos run build

extension-verify:
	npm --prefix clients/save-to-infoos run verify

test:
	. .venv/bin/activate && pytest tests/infoos -q

run-web:
	mkdir -p "$(LOCAL_INFOOS_DATA_HOME)"
	INFOOS_DATA_HOME="$(LOCAL_INFOOS_DATA_HOME)" bash scripts/infoos-web-server.sh

diagnostics-guard:
	@case "$(abspath $(DIAGNOSTICS_INFOOS_DATA_HOME))" in "$(abspath $(DIAGNOSTICS_BASE_DIR))"/*) ;; *) echo "diagnostics data must stay under DIAGNOSTICS_BASE_DIR" >&2; exit 2 ;; esac
	@case "$(abspath $(DIAGNOSTICS_INFOOS_DATA_HOME))" in "$(abspath $(LOCAL_INFOOS_DATA_HOME))"|"$(HOME)/.infoos"|"$(HOME)/.infoos/"*|"$(HOME)/Library/Application Support/InfoOS"|"$(HOME)/Library/Application Support/InfoOS/"*) echo "refusing to use a production data directory for diagnostics" >&2; exit 2 ;; esac

diagnostics-run: diagnostics-guard
	mkdir -p "$(DIAGNOSTICS_INFOOS_DATA_HOME)"
	. .venv/bin/activate && INFOOS_DATA_HOME="$(DIAGNOSTICS_INFOOS_DATA_HOME)" python -m backend.application.workflows.run_once --base-dir "$(DIAGNOSTICS_BASE_DIR)"

diagnostics-fetch: diagnostics-guard
	mkdir -p "$(DIAGNOSTICS_INFOOS_DATA_HOME)"
	. .venv/bin/activate && INFOOS_DATA_HOME="$(DIAGNOSTICS_INFOOS_DATA_HOME)" python -m backend.application.workflows.fetch --base-dir "$(DIAGNOSTICS_BASE_DIR)"
