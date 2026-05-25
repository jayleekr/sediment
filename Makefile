.PHONY: help up down ps logs psql redis-cli reset \
        install dev seed ingest verify-rls test \
        platform langgraph ingester metadata mcp \
        web web-build clean \
        validate-p0 validate-p1 validate-p2 validate-p3 validate-all \
        validate-loop validate-lint-e2e e2e-install \
        permissions ralph ralph-resume ralph-reset \
        monitor monitor-tail monitor-dashboard \
        bounce-services lint-sql p3-cron-install p3-cron-status p3-cron-uninstall \
        push prod-run \
        preflight smoke-tests deploy-check verify-deploy doctor \
        check-secrets check-secrets-history install-hooks uninstall-hooks

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
	@echo "Sediment — local dev"
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
	@echo ""
	@echo " session DRY pack (sediment#46):"
	@echo "  make push                                            — push w/ auto-rebase on conflict"
	@echo "  make prod-run SCRIPT=cleanup_test_conversations      — run scripts/X on the fly VM"
	@echo "  make prod-run SCRIPT=retention_sweep ARGS=--dry-run  — with args"
	@echo ""
	@echo " harness gates (after any code change):"
	@echo "  make doctor          — one-shot: preflight + smoke + check-secrets + deploy-check (run me FIRST)"
	@echo "  make preflight       — local stack ready? (docker, venv, ports, .env, lint)"
	@echo "  make smoke-tests     — fast Python canaries (JWT, fixer 4-tier, search_utils, chunker, ...)"
	@echo "  make deploy-check    — pre-push gate (fly secrets, migrations, nginx envsubst, P0)"
	@echo "  make verify-deploy   — post-deploy prod check (healthz, OAuth, proxy guard, headers)"
	@echo ""
	@echo " secret-scan (run before going public):"
	@echo "  make check-secrets         — scan working tree (current uncommitted state)"
	@echo "  make check-secrets-history — scan EVERY commit (~minute, before make repo public)"
	@echo "  make install-hooks         — wire pre-commit + pre-push hooks (block bad commits)"
	@echo "  make uninstall-hooks       — remove the hooks"

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
	@TOK=$$(curl -s -X POST http://localhost:10101/api/v1/auth/dev-token -H 'Content-Type: application/json' -d '{"email":"jayleekr0125@gmail.com"}' | python3 -c "import sys,json;print(json.load(sys.stdin)['token'])"); \
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
# 2026-05-23 fix: post-split layout — frontend lives at `frontend/` inside this
# repo, not at `../../web/` (pre-split monorepo path).
web:
	cd frontend && npm run dev

web-build:
	cd frontend && npm run build

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

# ---------------------------------------------------------------------------
# DRY pack (sediment#46) — one-shot helpers for repeating session patterns
# ---------------------------------------------------------------------------

# `make push` — push current branch with auto-rebase-on-conflict.
#
# Plain fast-forward push is the happy path. When the remote has new commits
# (concurrent worktree pushing in parallel — common in this repo), the
# default `git push` fails with `non-fast-forward`. This target handles that
# by stashing unstaged work, doing a non-rebase pull (preserves the local
# commit by creating a merge commit), pushing again, and restoring stashed
# changes. If the merge has conflicts, they stay in the worktree for human
# resolution — the target does NOT silently auto-resolve.
push:
	@branch=$$(git symbolic-ref --short HEAD); \
	if git push origin "$$branch" 2>&1 | tee /tmp/sediment-push.log; then \
	  if ! grep -q "non-fast-forward\|fetch first\|rejected" /tmp/sediment-push.log; then \
	    echo "✓ pushed $$branch ($$(git rev-parse --short HEAD))"; \
	    exit 0; \
	  fi; \
	fi; \
	echo "↻ remote ahead — stash + pull --no-rebase + push + pop"; \
	stash_ref=$$(git stash create -u 2>/dev/null); \
	if [ -n "$$stash_ref" ]; then git stash store -m "make-push autosave" "$$stash_ref" && git reset --hard HEAD >/dev/null 2>&1; fi; \
	if ! git pull --no-rebase origin "$$branch" --no-edit; then \
	  echo "✗ pull failed (likely merge conflict). Resolve manually then re-run \`make push\`."; \
	  [ -n "$$stash_ref" ] && echo "  (your stashed changes are in stash@{0})"; \
	  exit 1; \
	fi; \
	git push origin "$$branch" || { echo "✗ post-merge push failed"; exit 1; }; \
	if [ -n "$$stash_ref" ]; then git stash pop --quiet || echo "  (stash pop had conflicts — resolve in worktree)"; fi; \
	echo "✓ pushed $$branch with merge ($$(git rev-parse --short HEAD))"

# `make prod-run SCRIPT=X [ARGS=...]` — run a Python module on the fly VM.
#
# Uses harness/scripts/fly-exec.sh (which calls `fly machine exec`) instead of
# `fly ssh console -C`, because the latter hangs the client process indefinitely
# after the remote command exits when invoked non-interactively (sediment#54).
# The /run-with-db.sh wrapper massages DATABASE_URL into asyncpg form so the
# script's `service_session()` works.
#
# Examples:
#   make prod-run SCRIPT=cleanup_test_conversations ARGS=--dry-run
#   make prod-run SCRIPT=retention_sweep ARGS=--dry-run
#   make prod-run SCRIPT=reembed_all ARGS=--tenant=kids-edu
prod-run:
	@if [ -z "$(SCRIPT)" ]; then \
	  echo "usage: make prod-run SCRIPT=<module> [ARGS='--flag']"; \
	  echo "       (module is the dotted path after \`scripts.\` — e.g. cleanup_test_conversations)"; \
	  exit 1; \
	fi
	bash harness/scripts/fly-exec.sh "cd /app/services/sediment && /run-with-db.sh python -m scripts.$(SCRIPT) $(ARGS)"


# ================ harness gates (2026-05-24) ================
# One-command answers to "is this code good to push?" / "is prod ready?"
# Each script is also runnable standalone — these are just convenience aliases.

preflight:
	@bash harness/scripts/preflight.sh

# --quick skips the slow validate-p0 step. Useful in tight inner-loop iterations.
preflight-quick:
	@bash harness/scripts/preflight.sh --quick

smoke-tests:
	@bash harness/scripts/smoke-tests.sh

deploy-check:
	@bash harness/scripts/deploy-check.sh

# --skip-fly avoids the flyctl auth/network calls (useful offline).
deploy-check-offline:
	@bash harness/scripts/deploy-check.sh --skip-fly

verify-deploy:
	@bash harness/scripts/verify-deploy.sh

# Secret scan — run before commit/push manually, or rely on installed git hooks.
check-secrets:
	@bash harness/scripts/check-secrets.sh --working-tree

check-secrets-history:
	@bash harness/scripts/check-secrets.sh --history

install-hooks:
	@bash harness/scripts/install-git-hooks.sh

uninstall-hooks:
	@bash harness/scripts/install-git-hooks.sh --uninstall

# Doctor — the "I just made changes, am I OK?" single command.
# Runs: preflight-quick → smoke-tests → check-secrets (working tree) → deploy-check-offline.
# Stops at first FAIL so the user can fix one thing at a time.
doctor:
	@echo "── 1/4 preflight (quick) ──"
	@bash harness/scripts/preflight.sh --quick || (echo; echo "✗ preflight failed — fix above before continuing"; exit 1)
	@echo
	@echo "── 2/4 smoke-tests ──"
	@bash harness/scripts/smoke-tests.sh || (echo; echo "✗ smoke tests failed — fix above before continuing"; exit 1)
	@echo
	@echo "── 3/4 check-secrets (working tree — staged + untracked) ──"
	@bash harness/scripts/check-secrets.sh --working-tree || (echo; echo "✗ secret found — remove before committing"; exit 1)
	@echo
	@echo "── 4/4 deploy-check (offline mode — re-run with flyctl auth for full check) ──"
	@bash harness/scripts/deploy-check.sh --skip-fly || (echo; echo "✗ deploy-check failed"; exit 1)
	@echo
	@echo "✓ doctor: ALL GREEN. To ship: make deploy-check (with fly auth) then git push origin main."
