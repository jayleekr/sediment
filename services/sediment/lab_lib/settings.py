"""Centralized settings — read from .env."""
from __future__ import annotations
import os
import sys
from functools import lru_cache
from pydantic_settings import BaseSettings, SettingsConfigDict


# Sentinel value — if jwt_secret ever equals this in production, refuse to boot.
# Keeps the value committed in source AND blocks anyone who forgets to set the
# real secret in Fly/Vercel. 2026-05-23 fix (FIX-B) — previously the default
# was just a string with no guard, so a missing JWT_SECRET env in prod would
# silently sign every JWT with a value published in the repo. One curl = cross-
# tenant Identity forgery (see output/reviews/20260523-gstack-full/REPORT.md
# CRIT #2 confirmed by two independent reviewers).
_JWT_SECRET_DEV_SENTINEL = "local-dev-secret-replace"


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
    # DEFAULT is the sentinel; production MUST override via fly secret JWT_SECRET.
    # `validate_runtime_secrets()` enforces this at app boot.
    jwt_secret: str = _JWT_SECRET_DEV_SENTINEL
    jwt_issuer: str = "ai-curator-local"
    jwt_audience: str = "ai-curator-services"
    nextauth_secret: str = ""

    # Onboarding service key — required by /api/v1/onboard to mint the first
    # tenant + admin JWT. MUST be set in prod; the route compares with
    # secrets.compare_digest. Empty default + boot guard means dev-mode skip.
    sediment_onboarding_secret: str = ""

    # Open-relay shared secret for nginx → api.anthropic.com proxy. When empty,
    # the proxy refuses all requests (nginx-side check). Set in fly secrets so
    # only callers with the header can use Sediment's NRT egress.
    anthropic_proxy_secret: str = ""

    # Stripe webhook signing secret. WO-1 2026-05-23 — the webhook handler
    # uses stripe.Webhook.construct_event with this to verify signature +
    # timestamp tolerance. Empty default + boot guard means a missing secret
    # in prod won't silently accept forged subscription events.
    stripe_webhook_secret: str = ""

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


def _is_prod() -> bool:
    """Heuristic: any of these env signals = production runtime."""
    if os.environ.get("SEDIMENT_ENV") == "prod":
        return True
    if os.environ.get("FLY_APP_NAME"):  # set on Fly machines
        return True
    if os.environ.get("VERCEL"):  # set on Vercel
        return True
    return False


def validate_runtime_secrets() -> None:
    """Hard-fail at app boot if jwt_secret is the dev sentinel in production.

    Called once from each FastAPI app's startup. Dev/test/CI skip silently.
    Importing this in tests with the default sentinel is fine — only the
    explicit prod env signals trigger the assertion.
    """
    s = get_settings()
    if not _is_prod():
        return
    problems: list[str] = []
    if s.jwt_secret == _JWT_SECRET_DEV_SENTINEL:
        problems.append(
            "jwt_secret is still the dev sentinel "
            f"({_JWT_SECRET_DEV_SENTINEL!r}). Set the Fly secret JWT_SECRET "
            "to a 32+ byte random string."
        )
    if len(s.jwt_secret) < 32:
        problems.append(
            f"jwt_secret is too short ({len(s.jwt_secret)} chars). "
            "Use at least 32 bytes (e.g. `python -c 'import secrets; "
            "print(secrets.token_urlsafe(48))'`)."
        )
    if not s.sediment_onboarding_secret:
        problems.append(
            "sediment_onboarding_secret is empty in prod. /api/v1/onboard "
            "would mint admin JWTs to anyone. Set fly secret "
            "SEDIMENT_ONBOARDING_SECRET."
        )
    # Stripe webhook secret only required when Stripe is actively wired —
    # presence of STRIPE_SECRET_KEY (outbound API key) is the signal.
    # Phase 7 stub deployments without Stripe shouldn't fail boot.
    stripe_active = bool(os.environ.get("STRIPE_SECRET_KEY"))
    if stripe_active and not s.stripe_webhook_secret:
        problems.append(
            "stripe_webhook_secret is empty in prod and STRIPE_SECRET_KEY is "
            "set (Stripe active). /api/v1/billing/webhook would accept forged "
            "subscription events and flip tenants to cancelled/suspended at "
            "attacker will. Set fly secret STRIPE_WEBHOOK_SECRET (from Stripe "
            "dashboard → developers → webhooks → signing secret)."
        )
    if problems:
        for p in problems:
            print(f"FATAL SECRET CONFIG: {p}", file=sys.stderr)
        # Exit non-zero — supervisord/Fly will restart and fail loud rather
        # than silently boot a vulnerable instance.
        raise SystemExit(2)
