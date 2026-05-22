.PHONY: help up down ps logs psql redis-cli reset \
        install dev seed ingest verify-rls test \
        platform langgraph ingester metadata mcp \
        web web-build clean \
        validate-p0 validate-p1 validate-p2 validate-p3 validate-all \
        validate-loop validate-lint-e2e e2e-install \
        permissions ralph ralph-resume ralph-reset \
        monitor monitor-tail monitor-dashboard \
        bounce-services lint-sql p3-cron-install p3-cron-status p3-cron-uninstall

SHELL := /bin/bash
SVC_DIR := services/sediment
INFRA_DIR := infra
PYTHON := python3
# In a git worktree, services/sediment/.venv doesn't exist; fall back to the
# main repo's venv via the common git dir.
VENV_DIR := $(if $(wildcard $(SVC_DIR)/.venv),$(SVC_DIR)/.venv,$(shell git rev-parse --git-common-dir 2>/dev/null | xargs dirname)/services/sediment/.venv)
PIP := $(VENV_DIR)/bin/pip
PY := $(VENV_DIR)/bin/python
PYTEST := $(VENV_DIR)/bin/pytest

help:
	@echo "AI Curator — local dev"
	@echo ""
	@echo " infra:"
	@echo "  make up            — start Postgres+Redis (docker)"
	@echo "  make down / reset  — stop / destroy DB"
	@echo "  make psql          — open psql shell"
	@echo ""
	@echo " setup:"
	@echo "  make install       — create venv + pip install (incl. playwright)"
	@echo "  make e2e-install   — playwright install chromium"
	@echo "  make seed          — seed default tenant + members"
	@echo "  make ingest        — run initial vault ingest"
	@echo ""
	@echo " services (run each in its own terminal):"
	@echo "  make platform / langgraph / ingester / metadata / mcp"
	@echo "  make web           — run Next.js dev server"
	@echo ""
	@echo " tests:"
	@echo "  make test          — pytest"
	@echo "  make verify-rls    — quick RLS leak check"
	@echo ""
	@echo " validation (auto, rubric-driven):"
	@echo "  make validate-p0          — Phase 0 single-shot"
	@echo "  make validate-p1/p2/p3    — Phase 1/2/3 single-shot"
	@echo "  make validate-all         — P0 → P3 sequential"
	@echo "  make validate-loop PHASE=p1  — 50-iter self-improving loop"
	@echo "  make validate-lint-e2e    — lint e2e_spec.yaml against meta-spec"

# ================ infra ================
up:
	docker compose -f $(INFRA_DIR)/docker-compose.yml up -d
	@echo "waiting for postgres..."
	@until docker exec curator-pg pg_isready -U curator -d curator >/dev/null 2>&1; do sleep 1; done
	@echo "postgres ready on :5433"

down:
	docker compose -f $(INFRA_DIR)/docker-compose.yml down

reset:
	docker compose -f $(INFRA_DIR)/docker-compose.yml down -v
	docker compose -f $(INFRA_DIR)/docker-compose.yml up -d
	@echo "waiting for postgres..."
	@until docker exec curator-pg pg_isready -U curator -d curator >/dev/null 2>&1; do sleep 1; done

ps:
	docker compose -f $(INFRA_DIR)/docker-compose.yml ps

logs:
	docker compose -f $(INFRA_DIR)/docker-compose.yml logs -f --tail=100

psql:
	docker exec -it curator-pg psql -U curator -d curator

redis-cli:
	docker exec -it curator-redis redis-cli

# ================ python ================
install:
	cd $(SVC_DIR) && $(PYTHON) -m venv .venv && \
	  .venv/bin/pip install --upgrade pip && \
	  .venv/bin/pip install -e ".[dev]"

# ================ scripts ================
seed:
	cd $(SVC_DIR) && .venv/bin/python -m scripts.seed_lab

ingest:
	cd $(SVC_DIR) && .venv/bin/python -m scripts.ingest_repo

verify-rls:
	cd $(SVC_DIR) && .venv/bin/python -m scripts.verify_rls

test:
	cd $(SVC_DIR) && .venv/bin/pytest -v

# ================ migrations ================
migrate:
	cd $(SVC_DIR) && PYTHONPATH=. .venv/bin/python -m scripts.apply_migrations

migrate-dry:
	cd $(SVC_DIR) && PYTHONPATH=. .venv/bin/python -m scripts.apply_migrations --dry-run

# ================ CLI multi-user access test matrix ================
# Runs every test in docs/design/cli-test-requirements.md that doesn't need
# external services beyond Docker Postgres + a running platform on :10101.

test-cli-py:
	cd $(SVC_DIR) && PYTHONPATH=. $(abspath $(PYTEST)) \
	  tests/test_auth.py \
	  tests/test_oauth_device.py \
	  tests/test_oauth_device_edges.py \
	  tests/test_rate_limit.py tests/test_rate_limit_edges.py \
	  tests/test_audit.py \
	  tests/test_security.py \
	  tests/test_rls.py \
	  tests/test_rls_cross_tenant_via_api.py \
	  tests/test_cross_tenant_full.py \
	  -v

test-cli-shim:
	cd services/sediment-mcp && PYTHONPATH=src $(abspath $(PYTEST)) tests/test_shim.py tests/test_shim_edges.py -v

test-cli-rust:
	cd services/sediment-cli && cargo test --test unit --test edges

test-cli-all: test-cli-py test-cli-shim test-cli-rust
	@echo "✅ All non-E2E CLI tests passed"

# E2E — requires a running platform on :10101 with SEDIMENT_DEV_MODE=1
test-cli-e2e:
	cd services/sediment-cli && SEDIMENT_E2E_BASE_URL=http://localhost:10101 cargo test --test e2e_login --test e2e_full -- --nocapture
	@TOK=$$(curl -s -X POST http://localhost:10101/api/v1/auth/dev-token -H 'Content-Type: application/json' -d '{"email":"jay.lee@sonatus.com"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])"); \
	  cd services/sediment-mcp && SEDIMENT_E2E_BASE_URL=http://localhost:10101 SEDIMENT_E2E_TOKEN="$$TOK" PYTHONPATH=src $(abspath $(PYTEST)) tests/test_shim_e2e.py tests/test_shim_e2e_full.py -v

# ================ services ================
platform:
	cd $(SVC_DIR) && .venv/bin/uvicorn applications.sediment_platform.main:app --reload --port $${SEDIMENT_PLATFORM_PORT:-10100}

langgraph:
	cd $(SVC_DIR) && .venv/bin/uvicorn applications.sediment_langgraph.main:app --reload --port $${SEDIMENT_LANGGRAPH_PORT:-10020}

ingester:
	cd $(SVC_DIR) && .venv/bin/uvicorn applications.vault_ingester.main:app --reload --port $${VAULT_INGESTER_PORT:-11000}

metadata:
	cd $(SVC_DIR) && .venv/bin/uvicorn applications.metadata_svc.main:app --reload --port $${METADATA_SVC_PORT:-12000}

mcp:
	cd $(SVC_DIR) && .venv/bin/python -m lab_platform.mcp_servers.workspace_mcp

dev:
	@echo "open 4 terminal tabs and run: make platform / make langgraph / make ingester / make metadata"

# ================ web ================
web:
	cd ../../web && npm run dev

web-build:
	cd ../../web && npm run build

clean:
	rm -rf $(SVC_DIR)/.venv $(SVC_DIR)/**/__pycache__ $(SVC_DIR)/.pytest_cache

# ================ validator ================
e2e-install:
	cd $(SVC_DIR) && .venv/bin/playwright install chromium

validate-p0:
	cd $(SVC_DIR) && .venv/bin/python -m validator --phase P0

validate-p1:
	cd $(SVC_DIR) && .venv/bin/python -m validator --phase P1

validate-p2:
	cd $(SVC_DIR) && .venv/bin/python -m validator --phase P2

validate-p3:
	cd $(SVC_DIR) && .venv/bin/python -m validator --phase P3

validate-p4:
	cd $(SVC_DIR) && .venv/bin/python -m validator --phase P4

validate-all:
	@$(MAKE) validate-p0
	@$(MAKE) validate-p1
	@$(MAKE) validate-p2
	@$(MAKE) validate-p3
	@$(MAKE) validate-p4

# Phase 4: memory consolidation worker — extracts decisions+actions from chat
# into the decisions/actions tables. Idempotent.
consolidate:
	cd $(SVC_DIR) && .venv/bin/python -m scripts.consolidate_memory \
	  --tenant $${SEDIMENT_TENANT:-hypeproof-lab} \
	  --since-hours $${CONSOLIDATE_HOURS:-24}

consolidate-dry:
	cd $(SVC_DIR) && .venv/bin/python -m scripts.consolidate_memory \
	  --tenant $${SEDIMENT_TENANT:-hypeproof-lab} \
	  --since-hours $${CONSOLIDATE_HOURS:-168} --dry-run

# Self-improving loop. Default phase = p1 if not given.
PHASE ?= p1
validate-loop:
	cd $(SVC_DIR) && .venv/bin/python -m validator loop --phase $(PHASE)

validate-lint-e2e:
	cd $(SVC_DIR) && .venv/bin/python -m validator lint-e2e

# ================ Ralph loop (use bash, no chmod needed) ================
permissions:
	bash harness/permissions/apply.sh

bootstrap:
	bash harness/bootstrap-all.sh

bootstrap-with-ralph:
	bash harness/bootstrap-all.sh --with-ralph

ralph:
	bash harness/ralph/ralph.sh

ralph-supervisor:
	bash harness/ralph/supervisor.sh

ralph-resume:
	bash harness/ralph/ralph.sh --resume

ralph-reset:
	rm -f harness/ralph/TODO.md harness/ralph/JOURNAL.md harness/ralph/STATE.json

monitor:
	bash harness/monitor/watch.sh

monitor-tail:
	bash harness/monitor/tail.sh

monitor-dashboard:
	bash harness/monitor/dashboard-loop.sh &
	@echo "dashboard refreshing every 30s. open: output/ralph-dashboard.html"

# ================ Hardening helpers (added 2026-05-09 autonomous run) ================
# Bounce uvicorn services whose source files changed since BASELINE_REF (default HEAD~1).
# Usage: make bounce-services [BASELINE_REF=<sha-or-ref>]
bounce-services:
	bash harness/scripts/restart-services-if-changed.sh $(BASELINE_REF)

# Run the SQL :NAME::TYPE lint guard. Exits 1 with explanation on violation.
# Wired into ai-commit.sh gate, but you can run standalone for a sanity check.
lint-sql:
	bash harness/scripts/lint-sql-cast.sh

# Install / inspect / remove the daily P3 validator launchd job.
# After install, P3 validator runs at 09:15 daily and posts to Discord on regression.
p3-cron-install:
	bash harness/scripts/install-p3-cron.sh install

p3-cron-status:
	bash harness/scripts/install-p3-cron.sh status

p3-cron-uninstall:
	bash harness/scripts/install-p3-cron.sh uninstall
