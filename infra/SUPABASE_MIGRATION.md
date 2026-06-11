# P2 — pgvector via Supabase (the biggest answer-quality lever)

**Why.** Fly `postgres-flex` has no `pgvector`. The MVP applied an *adapted*
`init-fly.sql` (`embedding` as `text`, HNSW dropped) → retrieval is **BM25-only**:
paraphrased / conceptual queries that don't share lexical tokens miss. A tool
that misses "what you meant" won't earn habit-replacement (S3). Supabase ships
pgvector, so the **original `infra/init.sql` works unchanged** — this is an
infra move, **not a schema or code change** (the retrieval graph already does
hybrid `<=>` + `ts_rank`).

## Key facts (verified in-repo)

- `infra/init.sql` IS the pgvector-ready schema: `CREATE EXTENSION vector`,
  `chunks.embedding vector(1536)`, `chunks_embedding_hnsw` HNSW, `tsv` GIN.
- Embedding: OpenAI `text-embedding-3-small`, **1536d** (`settings.embedding_dim`).
- Retrieval: `applications/.../sediment_graph.py` already hybrid (vector +
  BM25). No code change to "turn on" pgvector — it just needs the column to
  actually be `vector` (it is, on Supabase).
- 563 artifacts is the known-good ingest count (MVP reference, NEXT.md).

## Steps

> 🔒 = gated (Jay / credentials).  ⚙️ = scripted/automatable by me.

1. 🔒 **Create Supabase project** (free tier OK for 8 users). Region: choose
   closest to Fly `nrt` (e.g. `ap-northeast-1`). Note the connection string.
2. ⚙️ **Apply schema** (unchanged `init.sql`):
   ```bash
   psql "$SUPABASE_DB_URL" -f infra/init.sql
   ```
   `init.sql` is idempotent (`IF NOT EXISTS`) and self-contains the `vector`
   extension + RLS roles. (Single-tenant dogfood may keep the MVP's
   `ALTER ROLE <app> BYPASSRLS` shortcut; full RLS also works.)
3. 🔒 **Seed** tenant + members against the new DB:
   ```bash
   DATABASE_URL="$SUPABASE_DB_URL" make seed     # github_login included now
   ```
4. ⚙️ **Re-ingest 563 artifacts** against the new DB (portable helper added —
   `scripts/reingest_to.sh`), pointing at the vault content checkout:
   ```bash
   DATABASE_URL="$SUPABASE_DB_URL" \
     bash services/sediment/scripts/reingest_to.sh /path/to/hypeprooflab
   ```
   (Embeddings cost: 563 docs × ~few chunks × `text-embedding-3-small`
   ≈ a few cents; needs `OPENAI_API_KEY`.)
5. ⚙️ **Recall check — the acceptance gate.** Run the regression BEFORE
   repointing prod (against BM25 Fly) and AFTER (against Supabase):
   ```bash
   services/sediment/.venv/bin/python -m validator.checks.regression_rag
   ```
   Acceptance: `recall@3` on `validator/golden_queries.yaml` measurably up
   vs the BM25-only baseline. If not up → do NOT repoint; investigate.
6. 🔒 **Repoint prod**: `fly secrets set DATABASE_URL="$SUPABASE_DB_URL"
   --app hypeproof-sediment`. ⚠️ asyncpg rejects libpq `sslmode=`; Supabase
   requires SSL. Use `?ssl=require` (NOT `sslmode=require`). `start.sh`
   already auto-converts `sslmode`→`ssl`, but set it correctly to be safe.
7. ⚙️ **Verify live**: sign in, ask a *paraphrased* question whose answer is
   not lexically in the doc — it should now retrieve (it wouldn't on BM25).

## Rollback

DATABASE_URL is the only switch. Keep the Fly PG running until step 7 passes;
`fly secrets set DATABASE_URL=<old fly PG>` reverts instantly (no data loss —
the Fly PG is untouched by this migration).

## Not in scope here

Moving the **billing/usage** history is optional for dogfood (regenerable).
Conversation history: if continuity matters, `pg_dump`
`conversations,messages,events` from Fly PG and restore into Supabase between
steps 2 and 5 — flagged, not done by default (dogfood can start fresh).
