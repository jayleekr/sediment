# BOA Synthetic Corpus Layered Plan

Purpose: create a small but believable BOA-style dental office NAS corpus for a 1-2 hour lecture, then prove it works through lecture scenario tests, retrieval tests, performance tests, E2E tests, and an improvement loop.

The priority is lecture quality, not data volume. The target corpus is 360 files, with enough depth to feel real and enough control to make demos reliable.

## Automation Contract

This project must be runnable without mid-run permission prompts or human decisions. Every layer should have a non-interactive harness path before the lecture corpus is generated.

Hard rules:
- No interactive prompts.
- No destructive cleanup outside `assets/generated/boa_*`.
- No fallback behavior.
- No silent degradation.
- No placeholder replacement for missing required output formats.
- No skipping required checks.
- No switching from `ask` to `search/read` as a demo substitute.
- No zero-vector embedding as an ingest substitute for lecture approval.
- If fallback logic is found in BOA corpus code, remove it.
- No real patient, staff, phone, resident-registration, or chart export data.
- No dependency on Windows/NAS access during harness runs.
- Every command must be idempotent or write to a timestamped report.
- Failures must produce a JSON report with a concrete fix category.
- Failures must stop the current layer.

Default execution mode:
- `strict-local`
- deterministic seed
- real generated PDFs/DOCX/XLSX/images are required
- generated markdown is required
- local retrieval checks are required

Optional execution mode:
- `strict-online`
- real embedding reindex
- live Sediment API smoke
- full `sediment ask` with LLM

The strict-local harness is the first gate. strict-online is a separate gate before a live Studio demo.

## Studio Contract

Hypeproof Studio must not know anything BOA-specific.

The only Studio responsibilities are:
- hold a Sediment API base URL;
- hold or request a valid Sediment token;
- send generic `search`, `read`, `recent`, and `ask` calls;
- render returned answers, citations, refs, and metadata.

Forbidden Studio coupling:
- no BOA folder names hardcoded in Studio;
- no BOA insurer list hardcoded in Studio;
- no BOA prompt templates required for correctness;
- no Studio-side lookup table for `치아보험`, `교정`, `비급여`, or `임플란트`;
- no Studio-specific synthetic data loader;
- no Studio cleanup script.

All BOA knowledge must live in Sediment:
- `artifacts.ref`
- `artifacts.frontmatter`
- `artifacts.body`
- `chunks.content`
- retrieval ranking and citations

This means a Studio demo and a CLI demo must produce equivalent results when they use the same Sediment tenant and token. If the CLI can answer but Studio cannot, the bug is in Studio's generic Sediment connector, not in BOA-specific code.

Studio demo readiness gate:
- strict-online Sediment harness passes;
- Studio can call the same deployed Sediment base URL;
- Studio can run one generic `ask` for each BOA scenario;
- Studio response shows citation refs that begin with `assets/generated/boa_vault_md/` or the deployed BOA namespace;
- no Studio code change is required when the BOA corpus is regenerated.

Planned one-command entrypoint:

```bash
python3 services/sediment/scripts/boa_demo_harness.py --profile lecture_360 --mode strict-local
```

Planned staged commands:

```bash
python3 services/sediment/scripts/generate_boa_synthetic_files.py --profile sample_60
python3 services/sediment/scripts/validate_boa_synthetic_files.py --profile sample_60
python3 services/sediment/scripts/convert_boa_files_to_vault.py --profile sample_60
python3 services/sediment/scripts/boa_local_retrieval_check.py --profile sample_60
python3 services/sediment/scripts/boa_demo_harness.py --profile lecture_360 --mode strict-local
```

## Layer 0: Scope Guard

Goal: prevent the corpus from becoming a large synthetic-data project.

Target:
- 360 files total.
- 1,500-3,000 Sediment chunks after conversion.
- 30 golden lecture queries.
- 6 demo flows that can be completed in 1-2 hours.

Non-goals:
- No real patient data.
- No real staff personal information.
- No full EMR clone.
- No full HIRA billing engine.
- No large image/video corpus.

Success criteria:
- A lecturer can browse the generated folder tree and it looks like a Korean dental clinic NAS.
- A student can use `sediment search/read/ask` and get useful answers with citations.
- All demo queries complete without manual rescue.

Harness:
- `boa_demo_harness.py --preflight`

Preflight checks:
- taxonomy YAML parses.
- target file counts are internally consistent.
- output roots are under `assets/generated`.
- required local Python packages are present or a clear install report is produced.
- current workspace can write to `assets/generated`.

Failure action:
- Stop before writing corpus files.
- Write `assets/generated/boa_reports/preflight_failed.json`.
- Do not ask for permission.
- Do not use fallback packages or alternate output formats.

## Layer 1: Synthetic File Corpus

Goal: generate a BOA-like folder tree and document set that looks operationally real.

Output root:
- `assets/generated/boa_nas/`

Planned count:

| Area | Files | Lecture use |
|---|---:|---|
| `업무자료/보아/★보아관련★` | 55 | manuals, reporting process, AS, surgery/implant preparation |
| `업무자료/보아/☆보아 교정과☆` | 60 | orthodontic consent, MARPE, mini screw, retainer guidance |
| `업무자료/보아/데스크 업무/치아보험서류` | 95 | private dental insurance paperwork |
| `업무자료/보아/보아 스텝 (개인)/이혜진` | 70 | desk work, staff, appointment cards, interview/training |
| `업무자료/보아/보아 스텝 (개인)/이혜진/2026` | 45 | 2026 reporting, training, schedule, non-covered-fee work |
| `업무자료/보아/수가표` + `병원서류&로고` + `보아 경영지원실` | 35 | price table, logo/forms, management support |

File type mix:
- 120 `.xlsx`: schedules, fee tables, claim checklists, staff rosters.
- 95 `.docx`: manuals, consent forms, caution sheets, training documents.
- 95 `.pdf`: insurer claim forms, treatment confirmations, consent forms.
- 30 `.png`/`.jpg`: logo, badge mockups, photo-zone placeholders.
- 20 `.md`/`.json`: canonical demo notes and metadata summaries.

Each generated file must have a sidecar:
- `*.metadata.json`

Required metadata:
- `tenant_slug`
- `source_pattern`
- `folder_area`
- `doc_type`
- `year`
- `owner_role`
- `lecture_module`
- `demo_relevance`
- `contains_real_phi=false`
- `synthetic_patient_ids`
- `expected_queries`

Generation quality rules:
- Korean filenames should look like the screenshot style, including year prefixes, revision suffixes, and mixed Korean/English dental terms.
- Modification dates should be plausible: insurer base forms around 2021-2022, 2026 reports in 2026-01 to 2026-05, manuals updated around 2025-2026.
- File bodies must contain enough text for search and citations.
- Spreadsheet rows must include realistic column headers and 10-80 rows, not blank shells.
- PDFs and DOCX files must have titles, dates, checklists, and caution sections.

Harness:
- `generate_boa_synthetic_files.py`
- `validate_boa_synthetic_files.py`

Default generation must:
- remove only files previously marked by `assets/generated/boa_manifest.json`;
- create a fresh manifest with file paths, checksums, doc types, and expected queries;
- write deterministic content from a fixed seed;
- fail fast when required document libraries are missing;
- fail fast when a requested `.docx`, `.pdf`, `.xlsx`, `.png`, or `.jpg` cannot be generated as that real file type.

No fallback rule:
- Do not create `.docx.txt`, `.pdf.txt`, `.png.txt`, or any other substitute.
- Do not mark substitute files as success.
- Do not proceed to lecture approval with missing required formats.

## Layer 2: Sediment Ingest Corpus

Goal: convert the synthetic NAS files into Sediment-searchable artifacts.

Output root:
- `assets/generated/boa_vault_md/`

Conversion rule:
- Every user-facing synthetic file gets one markdown artifact.
- The markdown artifact includes frontmatter, source path, summary, key fields, and extracted body text.
- Large spreadsheets are summarized into sections rather than dumping every row.

Artifact type mapping:
- Insurance forms: `note`
- Manuals: `note`
- Clinical scenarios: `note`
- Staff/operation docs: `meeting` or `note`
- Demo answer keys: `decision` only when they are explicitly framed as decisions.

Frontmatter fields:
- `date`
- `slug`
- `lang: ko`
- `status: published`
- `boa_area`
- `doc_type`
- `lecture_module`
- `source_file`
- `synthetic: true`

Ingest recommendation:
- For local corpus validation, run markdown-level retrieval before DB ingest.
- For lecture approval, use real embeddings when demonstrating `ask` through Sediment.
- If embedding is unavailable, fail the strict-online gate instead of substituting zero vectors.

Harness:
- `convert_boa_files_to_vault.py`
- `boa_local_retrieval_check.py`

Default conversion must:
- never require running Postgres;
- never require the Sediment API;
- produce markdown artifacts and a local BM25-style index report;
- verify that all anchor scenario queries have at least one matching markdown artifact before DB ingest.

Failure action:
- If a query has no local matches, patch generator templates or taxonomy synonyms.
- Do not continue to DB ingest until local retrieval passes.
- Do not substitute a different scenario, prompt, or demo path.

## Layer 3: Lecture Scenarios

Goal: align files to teachable workflows, not just folder browsing.

Scenario 1: BOA NAS Orientation
- Question: "보아 NAS에서 데스크 업무와 교정 업무 자료는 어떻게 나뉘어 있어?"
- Expected: cites folder summary artifacts and explains desk, orthodontics, staff, insurance, reporting.

Scenario 2: Private Dental Insurance Desk Flow
- Question: "라이나 치아보험 청구할 때 필요한 서류와 데스크 확인사항을 정리해줘"
- Expected: cites 라이나 claim/treatment confirmation/privacy consent artifacts.

Scenario 3: Orthodontic Consent and Caution
- Question: "교정 유지장치 제거 동의서가 필요한 상황과 환자 안내 포인트는?"
- Expected: cites retainer consent/caution docs and gives a chairside explanation.

Scenario 4: Implant Consultation Preparation
- Question: "임플란트 상담부터 수술 전까지 데스크가 챙겨야 할 문서는?"
- Expected: cites implant preparation table, consultation checklist, consent form.

Scenario 5: 2026 Non-covered Fee Reporting
- Question: "2026 비급여 보고 제출자료 준비 체크리스트를 만들어줘"
- Expected: cites 2026 non-covered-fee report files and fee table.

Scenario 6: New Desk Staff Training
- Question: "신입 데스크 직원에게 보험청구 실수 Top 5를 교육하려면 어떤 자료를 보면 돼?"
- Expected: cites desk training, insurance checklist, common error examples.

Lecture timing:
- 10 min: show folder tree and explain why corpus is synthetic.
- 15 min: Sediment search/read basics.
- 25 min: 3 scenario demos.
- 20 min: student hands-on with 3 guided prompts.
- 10 min: validation, performance, and failure recovery discussion.

Harness:
- `boa_scenario_dry_run.py`

The dry run produces:
- scenario title;
- prompt;
- top local matching artifacts;
- expected citation refs;
- required demo command.

No fallback rule:
- If `ask` is the scenario command, `ask` must work for that scenario gate.
- Do not replace a failed `ask` scenario with `search/read`.
- Use `search/read` only for scenarios explicitly designed as `search/read` scenarios.

## Layer 4: Scenario Tests

Goal: make sure lecture prompts work before the lecture.

Golden query count:
- 30 total.
- 5 per lecture scenario.

Each golden query must define:
- `query`
- `expected_refs`
- `must_include_terms`
- `must_not_include_terms`
- `lecture_scenario`
- `minimum_citations`
- `allowed_answer_mode`: `summary`, `checklist`, `comparison`, or `workflow`

Pass criteria:
- `search recall@3 >= 0.80`
- `search recall@5 >= 0.90`
- `ask` returns at least 2 citations for workflow questions.
- No answer invents real patient names, real phone numbers, or non-existent insurer-specific rules.

Failure action:
- If recall fails: add filename synonyms, stronger frontmatter terms, or a one-paragraph summary at the top of the markdown artifact.
- If answer is vague: add scenario-specific checklist sections to the source artifact.
- If answer overclaims: add explicit "synthetic training data only" note and narrow the prompt template.

Harness:
- `services/sediment/validator/checks/boa_demo.py`
- `services/sediment/validator/golden_queries_boa.yaml`

Strict-local test path:
- use generated markdown and local token/BM25 matching;
- no Sediment server required;
- no embedding required;
- no LLM required.

Strict-online test path:
- run only when `--mode strict-online` is passed;
- use `sediment search/read`;
- use `sediment ask` for all `ask` scenarios.

No permission rule:
- if auth/server is unavailable in strict-online mode, fail the strict-online gate.
- Do not ask the user for credentials mid-run.

## Layer 5: Performance Tests

Goal: verify the lecture corpus is comfortably small and demo-safe.

Test sizes:
- `sample_60`: fast smoke corpus.
- `lecture_360`: real lecture corpus.
- `stress_1200`: separate expansion corpus to expose bottlenecks.

Metrics:
- file generation time
- markdown conversion time
- ingest time
- artifact count
- chunk count
- `/api/v1/library/search` p50/p95
- CLI `sediment search` p95
- CLI `sediment ask` time to first citation and total time

Pass criteria for `lecture_360`:
- generation < 60 seconds
- conversion < 30 seconds
- ingest < 10 minutes with real embeddings, < 2 minutes with zero embeddings
- chunk count between 1,500 and 3,000
- search p95 < 700 ms locally or < 1.2 s over deployed API
- ask retrieval stage < 2 s, excluding LLM generation

Failure action:
- If chunk count > 3,000: reduce spreadsheet row dumps and summarize tables.
- If ingest is slow: reduce corpus size or optimize batching; do not switch to zero-vector ingest for approval.
- If search p95 is slow: add explicit tenant filters in search SQL and reduce `stress_1200` scope.
- If LLM step is slow: shorten source artifacts or prompts, then rerun; do not replace failed `ask` with `search/read`.

Harness:
- `boa_perf_check.py`

Strict-local performance checks:
- file generation wall time;
- conversion wall time;
- chunk estimate using `lab_lib.chunker`;
- local retrieval latency over markdown artifacts.

Strict-online performance checks:
- API search p50/p95;
- ingest p50/p95 if a local ingester is already running;
- no automatic server startup unless explicitly passed as `--start-local-services`.

No hang rule:
- every subprocess has a timeout;
- every HTTP call has a timeout;
- no command waits on stdin;
- long-running strict-online checks fail on timeout instead of waiting.

## Layer 6: E2E Tests

Goal: prove the whole path works from synthetic files to Hypeproof Studio-facing CLI/API.

E2E path:
1. Generate `assets/generated/boa_nas/`.
2. Validate file count, metadata, and no-PII rules.
3. Convert to `assets/generated/boa_vault_md/`.
4. Ingest into a BOA demo tenant.
5. Run `sediment search` for 6 scenario anchor queries.
6. Run `sediment read` for one cited artifact per scenario.
7. Run `sediment ask` for 6 full lecture prompts.
8. Save a JSON report with timings, citations, and pass/fail.

Required E2E checks:
- CLI auth works or dev token is available.
- Tenant isolation works: BOA artifacts do not appear for a second tenant.
- Browse/recent works with `type=note`.
- Search returns expected BOA refs.
- Ask produces citation-backed answers.
- Freshness endpoint shows artifacts and chunks.

Failure action:
- If auth fails: switch demo to dev-token mode and document exact command.
- If ingest fails: isolate first failing file and run converter in strict mode.
- If expected refs do not appear: patch taxonomy terms and regenerate only affected category.
- If ask citations are weak: fix source artifacts, chunk summaries, or retrieval settings and rerun.

Harness:
- `boa_e2e.py`

Strict-local E2E:
1. Generate files.
2. Validate files.
3. Convert markdown.
4. Run local retrieval against markdown.
5. Emit lecture runbook and required commands.

Strict-online E2E:
1. Confirm token/server readiness.
2. Ingest converted markdown.
3. Run CLI `search`.
4. Run CLI `read`.
5. Run CLI `ask` for all `ask` scenarios.

Strict-online E2E is required before a live Hypeproof Studio/Sediment `ask` demo.

## Layer 7: Improvement Loop

Goal: make failure repair systematic instead of ad hoc.

Loop:
1. Run validator.
2. Classify failure.
3. Patch the smallest layer.
4. Regenerate only affected files.
5. Re-run scenario tests.
6. Promote fixed corpus snapshot.

Failure taxonomy:

| Failure | Likely layer | Fix |
|---|---|---|
| Folder does not look real | Layer 1 | adjust folder names, modification dates, file type mix |
| Search misses obvious file | Layer 2/4 | add synonyms, frontmatter, summary paragraph |
| Answer is too generic | Layer 1/2 | add richer source details and checklists |
| Answer hallucinates | Layer 4 | add negative assertions and stricter expected terms |
| Ingest too slow | Layer 5 | optimize batching, reduce row dumps, reduce corpus size |
| Demo fails due auth/API | Layer 6 | use dev token, local server script, preflight check |
| Student confused | Layer 3 | reduce prompt count, add one clearer workflow artifact |

Promotion rule:
- Only promote a corpus snapshot when all 6 anchor scenarios pass.
- Keep `sample_60` for fast debugging and `lecture_360` for actual class.

Harness:
- `boa_demo_harness.py --repair-plan`

Repair-plan output:
- `assets/generated/boa_reports/latest_repair_plan.md`
- `assets/generated/boa_reports/latest_repair_plan.json`

Repair categories:
- `taxonomy_count_mismatch`
- `missing_required_doc_type`
- `weak_local_retrieval`
- `chunk_count_too_high`
- `online_dependency_unavailable`
- `api_latency_high`
- `answer_quality_low`

Automatic fixes allowed:
- add synonyms to generated markdown summaries;
- add missing required files within the planned count by replacing lower-priority filler files;
- shorten oversized markdown table dumps;
- regenerate affected area only.

Manual fixes not allowed inside harness:
- changing repo configuration outside BOA files;
- installing packages;
- starting paid external services;
- deleting user files outside generated manifests.
- substituting a failed required step with a fallback path.

## Build Artifacts To Add Next

Implementation files:
- `services/sediment/scripts/boa_demo_harness.py`
- `services/sediment/data/boa_file_taxonomy.yaml`
- `services/sediment/scripts/generate_boa_synthetic_files.py`
- `services/sediment/scripts/validate_boa_synthetic_files.py`
- `services/sediment/scripts/convert_boa_files_to_vault.py`
- `services/sediment/scripts/boa_local_retrieval_check.py`
- `services/sediment/scripts/boa_scenario_dry_run.py`
- `services/sediment/scripts/boa_perf_check.py`
- `services/sediment/scripts/boa_e2e.py`
- `services/sediment/validator/golden_queries_boa.yaml`
- `services/sediment/validator/checks/boa_demo.py`

Generated outputs:
- `assets/generated/boa_nas/`
- `assets/generated/boa_vault_md/`
- `assets/generated/boa_reports/latest_validation.json`
- `assets/generated/boa_reports/latest_e2e.json`
