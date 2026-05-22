"""Centralized settings — read from .env."""
from __future__ import annotations
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    # DB
    # Two-role split (sediment#16): app subject to RLS, service BYPASSRLS.
    # LOCAL dev defaults point at the dockerized PG (container `curator-pg`,
    # DB name `curator`, roles `curator_app/curator_service`). These names
    # are legacy from the pre-2026-05-15 brand rename and are KEPT for the
    # local docker stack (per CLAUDE.md: "DB cluster identity" — renaming
    # the docker container would invalidate every dev's local volumes for
    # no upside).
    # PROD overrides both via fly secrets DATABASE_URL_APP/SERVICE which
    # point at Supabase pooler with `sediment_app/sediment_service` roles
    # (created 2026-05-22, NOT BYPASSRLS / BYPASSRLS respectively).
    database_url_app: str = "postgresql+asyncpg://curator_app:curator_app_local@localhost:5433/curator"
    database_url_service: str = "postgresql+asyncpg://curator_service:curator_service_local@localhost:5433/curator"
    redis_url: str = "redis://localhost:6380/0"

    # LLM / embedding
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    # Embedding provider: gemini (default, see sediment#16) | openai | zero
    embedding_provider: str = "gemini"
    embedding_model: str = "gemini-embedding-001"
    embedding_dim: int = 1536
    # Tiered models, per provider. stream_chat picks heavy vs default by the
    # `tier` arg AND the active provider (claude model names break the Gemini
    # API and vice-versa). default = cheap path (Phase 4 worker, router);
    # heavy = chat answer composition (must refuse to fabricate — the product).
    llm_model_default: str = "claude-haiku-4-5-20251001"   # anthropic default
    llm_model_heavy: str = "claude-sonnet-4-6"             # anthropic heavy
    gemini_model_default: str = "gemini-2.5-flash"
    gemini_model_heavy: str = "gemini-2.5-pro"

    # Auth / JWT
    jwt_secret: str = "local-dev-secret-replace"
    jwt_issuer: str = "ai-curator-local"
    jwt_audience: str = "ai-curator-services"
    nextauth_secret: str = ""

    # Service ports
    sediment_platform_port: int = 10100
    sediment_langgraph_port: int = 10020
    vault_ingester_port: int = 11000
    metadata_svc_port: int = 12000
    workspace_mcp_port: int = 8888

    # Default tenant (dogfood)
    default_tenant_slug: str = "hypeproof-lab"
    default_tenant_name: str = "HypeProof Lab"

    # Public base URL — used by the device-flow user-facing approval page.
    # In dev: http://localhost:3000 (next dev server). In prod: the Vercel URL.
    public_base_url: str = "http://localhost:3000"

    # Vault paths (relative to services/sediment/)
    vault_repo_root: str = "../../.."
    vault_ingest_paths: str = "research/daily,web/src/content/columns,web/src/content/novels,web/src/content/research,novels,products,docs,PHILOSOPHY.md,AGENTS.md"

    # Cost guardrails
    cost_budget_monthly_usd: int = 200
    # 200/min per member (≈3.3 req/s). 20 was the v0 estimate; the actual
    # mix (page-all NDJSON, repeated whoami from MCP shim, dev iteration)
    # demands more headroom. Override via QUERY_RATELIMIT_PER_MIN env.
    query_ratelimit_per_min: int = 200

    # Logging
    log_level: str = "INFO"
    log_format: str = "json"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
