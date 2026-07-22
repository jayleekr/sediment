"""Credentialed CORS policy — one source of truth for both FastAPI apps.

Threat model (sediment#80). The previous config paired ``allow_credentials=True``
with ``allow_origin_regex=r"https://([a-z0-9-]+\\.)?(vercel\\.app|hypeproof-ai\\.xyz|hypeproof\\.studio)"``.
``*.vercel.app`` is a **shared, multi-tenant apex**: anyone can deploy
``attacker.vercel.app``. A credentialed wildcard over that apex lets any such
origin issue requests carrying the victim's cookies/session AND read the
responses — a cross-site credential-leak / CSRF-read primitive.

Policy enforced here:

* Allow only first-party origins we own: localhost dev + the custom domains
  (``sediment.hypeproof-ai.xyz``, ``hypeproof-ai.xyz``, ``hypeproof.studio``).
  Prod frontend lives on the custom domain, so this is all prod needs.
* Allow Vercel *preview* origins ONLY when scoped to this project's Vercel team
  slug — the trailing ``-<slug>.vercel.app`` label Vercel appends to every
  deployment URL, which an off-team attacker cannot forge. Off by default
  (secure); ops opts in per environment via ``SEDIMENT_VERCEL_TEAM_SLUG``.
* Refuse to build a credentialed config that also matches a hostile probe
  origin — defense in depth so a broad regex can't quietly regress back in.

Staging/preview rollout notes live in ``docs/design/11-deployment.md`` §CORS.
"""
from __future__ import annotations

import os
import re
from typing import Mapping

# First-party origins we control. Explicit list — never a wildcard.
_FIRST_PARTY_ORIGINS = [
    "http://localhost:3000",
    "http://127.0.0.1:3000",
    "https://sediment.hypeproof-ai.xyz",
    "https://hypeproof-ai.xyz",
    "https://hypeproof.studio",
]

# Origins that must NEVER be allowed under allow_credentials=True. The safety
# self-check (_assert_safe) refuses any credentialed config that would match one
# of these — catching a broad shared-apex regex regressing back into the code.
_HOSTILE_PROBES = (
    "https://evil.vercel.app",
    "https://attacker.vercel.app",
    "https://vercel.app",
    "https://hypeproof-ai.xyz.attacker.com",
    "https://evil.hypeproof.studio.attacker.com",
)


def _split_csv(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def build_cors_kwargs(env: Mapping[str, str] | None = None) -> dict:
    """Build the kwargs dict for ``CORSMiddleware`` from the environment.

    Safe by default: no ``*.vercel.app`` allowance unless a team slug is
    explicitly configured. Raises ``ValueError`` if the resulting credentialed
    policy would trust a hostile origin.
    """
    env = os.environ if env is None else env

    allow_origins = list(_FIRST_PARTY_ORIGINS)
    allow_origins.extend(_split_csv(env.get("SEDIMENT_CORS_EXTRA_ORIGINS", "")))

    allow_origin_regex: str | None = None
    team_slug = env.get("SEDIMENT_VERCEL_TEAM_SLUG", "").strip().lower()
    if team_slug:
        slug = re.escape(team_slug)
        # Vercel preview URL shape: "<project>-<git-or-hash>-<team-slug>.vercel.app".
        # The team slug is the final label before ".vercel.app" and cannot be
        # forged by a deployment outside the team, so require it explicitly
        # instead of trusting the whole "*.vercel.app" apex.
        allow_origin_regex = rf"https://[a-z0-9-]+-{slug}\.vercel\.app"

    kwargs: dict = {
        "allow_origins": allow_origins,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }
    if allow_origin_regex is not None:
        kwargs["allow_origin_regex"] = allow_origin_regex

    _assert_safe(kwargs)
    return kwargs


def _origin_matches(kwargs: Mapping, origin: str) -> bool:
    """Mirror Starlette's CORSMiddleware.is_allowed_origin decision.

    Starlette allows an origin if it is in ``allow_origins`` OR the
    ``allow_origin_regex`` *fullmatches* it (see starlette.middleware.cors).
    """
    if origin in kwargs.get("allow_origins", []):
        return True
    rx = kwargs.get("allow_origin_regex")
    return bool(rx and re.compile(rx).fullmatch(origin))


def _assert_safe(kwargs: Mapping) -> None:
    """Forbid combining allow_credentials=True with a wildcard/broad origin.

    Completes the acceptance criterion in sediment#80 ("allow_credentials=True와
    wildcard/광범위 regex 조합을 금지하는 검증을 추가한다").
    """
    if not kwargs.get("allow_credentials"):
        return
    if "*" in kwargs.get("allow_origins", []):
        raise ValueError(
            "CORS misconfig: allow_credentials=True with wildcard '*' origin is "
            "forbidden (browsers reject it AND it would defeat credential scoping)."
        )
    hits = [p for p in _HOSTILE_PROBES if _origin_matches(kwargs, p)]
    if hits:
        raise ValueError(
            "CORS misconfig: this credentialed policy would allow hostile "
            f"origin(s) {hits}. A broad regex over a shared apex (e.g. "
            "*.vercel.app) must not be combined with allow_credentials=True "
            "(sediment#80). Scope Vercel previews to the team slug instead."
        )
