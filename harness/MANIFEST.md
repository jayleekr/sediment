# Validation Harness — Portable Manifest

> Reusable validation toolkit for AI/LLM products. Project-agnostic by design.
> SSL-structured (Liang et al. 2026, *From Skill Text to Skill Structure*, arXiv:2604.24026).

This manifest is the **single source of truth** for what the harness expects from a host project and what it provides in return. Drop the `harness/` directory + `.claude/agents/curator-*.md` + `.claude/skills/curator-validate/` into any project, satisfy the contracts below, and you have a 50-iteration self-improving validation loop.

---

## 0. SSL Contract Layer

Every component in the harness is described by an SSL manifest with three layers:

- **Scheduling (S)** — when invoked, frequency, idempotency
- **Structural (S)** — sequence of internal sub-steps
- **Logical (L)** — inputs, outputs, side-effects, resources

This separation is what makes components portable: you can swap projects without rewriting agent code, only the contract bindings.

---

## 1. Components

### 1.1 Subagents (`.claude/agents/curator-*.md`)

| Agent | Scheduling | Structural | Logical |
|---|---|---|---|
| `curator-validator` | on-demand or via /slash | rubric → dispatch → triage | in: phase / out: report + specialist? |
| `curator-loop-orchestrator` | mode=loop | bootstrap → loop → escalate | in: phase / out: convergence status |
| `curator-fixer` | after failed iter | parse work-order → match recipe → exec | in: work-order / out: applied / unfixable |
| `curator-rag-tuner` | on RAG failure | gather → diagnose → propose | in: report.json / out: tuning proposal |
| `curator-rls-auditor` | on RLS failure | enumerate → probe → trace | in: report.json / out: audit report |
| `curator-e2e-debugger` | on E2E failure | screenshots → classify → propose | in: iter dir / out: e2e proposal |
| `curator-rubric-author` | on feature add | diff → map → draft | in: git ref / out: rubric proposal |

### 1.2 Slash command (`.claude/skills/curator-validate/`)

`/curator-validate <phase> [single|loop] [all|rag|rls|e2e|security]`

Dispatches to the right subagent. No orchestration logic — only routing.

### 1.3 Python harness (`services/<svc>/validator/` in this project)

| Module | Purpose |
|---|---|
| `runner.py` | single-shot rubric execution |
| `loop.py` | 50-iter self-improving loop |
| `dispatch.py` | bash / sql / http / python / e2e check types |
| `e2e_runner.py` | Playwright async + screenshot |
| `fixer.py` | recipe-based auto-fix |
| `report.py` | JSON + Markdown + console |
| `types.py` | CheckResult, PhaseReport |

### 1.4 Configuration files

| File | Schema | Purpose |
|---|---|---|
| `rubric.yaml` | `contracts/rubric.schema.json` | declarative checks |
| `e2e_spec.yaml` | `contracts/e2e_spec.schema.json` | Playwright flows |
| `recipes.yaml` | `contracts/recipes.schema.json` | auto-fix recipes |
| `golden_queries.yaml` | `contracts/golden_queries.schema.json` | RAG eval set |

---

## 2. Host project contract

The harness expects:

### 2.1 Required files (in host project)

- `<project>/services/<svc>/validator/rubric.yaml` — phase-organized checks
- `<project>/services/<svc>/validator/e2e_spec.yaml` — browser flows
- `<project>/services/<svc>/validator/recipes.yaml` — auto-fix recipes
- `<project>/services/<svc>/validator/golden_queries.yaml` — for RAG products
- `<project>/Makefile` with these targets:
  - `make up` — start infra
  - `make seed` — seed default tenant + members
  - `make ingest` — populate corpus (RAG products)
  - `make platform / langgraph / ingester / metadata` — start services
  - `make validate-p0/p1/p2/p3` — single-shot
  - `make validate-loop PHASE=X` — loop

### 2.2 Required output schema

The harness writes:
- `output/validation/<phase>-<ts>.json` — single-shot report
- `output/validation/loop-<phase>-<ts>/iter-NN/report.{json,md}` — per iter
- `output/validation/loop-<phase>-<ts>/history.csv` — score curve
- `output/validation/loop-<phase>-<ts>/convergence.md` — final status
- `output/validation/loop-<phase>-<ts>/iter-NN/work-order.json` — failures needing human/specialist

### 2.3 Required environment

- Postgres ≥ 16 with pgvector ≥ 0.8 (for RAG products)
- Redis (sessions/cache, optional but recommended)
- Python 3.11+ with: fastapi, sqlalchemy[asyncio], asyncpg, pgvector, langgraph, playwright, PyYAML, httpx, structlog
- Node 20+ + Next.js (for projects with web UI)

---

## 3. Bootstrap to a new project

```bash
# From the new project's root:
python3 path/to/curator/harness/bootstrap.py \
  --target ./services/<svc>/validator \
  --project-name <name> \
  --tenant-table-list "tenants,members,artifacts,..." \
  --layers L1,L2,L3,L4
```

What bootstrap does:
1. Copies harness Python modules to `<target>/`
2. Generates skeleton `rubric.yaml` from `contracts/templates/rubric.skeleton.yaml`
3. Generates skeleton `e2e_spec.yaml`
4. Generates skeleton `recipes.yaml`
5. Generates `__main__.py` that re-uses generic runner/loop/dispatch
6. Suggests Makefile targets
7. Prints the list of agent files to copy to `.claude/agents/`

The Python harness is project-agnostic — it reads `rubric.yaml` and dispatches without hardcoding paths.

---

## 4. Agent → Harness wiring

```
User
  │
  └─ /curator-validate p1 loop
        │
        ▼
   [SKILL] curator-validate
        │
        └─ Task: subagent_type=curator-loop-orchestrator
              │
              ▼
        [AGENT] curator-loop-orchestrator
              │
              ├─ Bash: make validate-loop PHASE=p1
              │       │
              │       └─ [PYTHON] validator/loop.py
              │             ├─ runner.py → dispatch.py → checks/*.py
              │             ├─ e2e_runner.py (Playwright)
              │             └─ fixer.py (recipes)
              │
              └─ Task: subagent_type=curator-rag-tuner (if RAG fails)
                    │
                    └─ Read report.json + golden_queries.yaml
                       Write tuning-proposal-*.md
```

---

## 5. Cross-project portability check

When porting to a new project:

| Item | Edit needed? |
|---|---|
| `harness/runner.py`, `harness/loop.py`, `harness/dispatch.py` | NO — generic |
| `harness/contracts/*.schema.json` | NO — schemas are universal |
| `validator/rubric.yaml` | YES — per project |
| `validator/e2e_spec.yaml` | YES — per project |
| `validator/recipes.yaml` | YES — per project |
| `validator/checks/*.py` | YES — but copy library checks (lib_rls, lib_rag, lib_security) verbatim |
| `.claude/agents/curator-*.md` | LIGHT — edit "First: Read Context" file paths only |
| `.claude/skills/curator-validate/SKILL.md` | LIGHT — rename trigger, file paths |

The 80/20: ~80% of the code is reusable, ~20% (rubric content, project-specific check functions) is per-project.

---

## 6. Reference

- Liang, Wang, Liang, Liu (2026). "From Skill Text to Skill Structure: The Scheduling-Structural-Logical Representation for Agent Skills." arXiv:2604.24026. → SSL representation pattern.
- Schank & Abelson (1977). "Scripts, Plans, Goals and Understanding." → script theory roots.
- TEST_REQUIREMENTS.md (this project) → 11-layer test taxonomy (L1-L11).

---

*Last updated: 2026-05-05*
