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
* Allow *no* pattern-matched ``*.vercel.app`` origin at all. A hostname label
  cannot identify a tenant on a shared apex: a real preview URL
  (``sediment-git-main-<team>.vercel.app``) and an attacker-controlled
  ``evil-<team>.vercel.app`` are both a single label in front of
  ``vercel.app``, so no regex can tell them apart — anyone can register a
  project name that ends in ``-<team>``. Preview/staging origins must therefore
  be enumerated explicitly via ``SEDIMENT_CORS_EXTRA_ORIGINS`` (CSV).
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
    # Shaped like a team-scoped preview URL but attacker-registrable: on a
    # shared apex "evil-<team>" is just another project name. Kept here so any
    # future "-<slug>.vercel.app" regex — or an ops typo in
    # SEDIMENT_CORS_EXTRA_ORIGINS — fails the build instead of shipping.
    "https://evil-hypeprooflab.vercel.app",
    "https://hypeproof-ai.xyz.attacker.com",
    "https://evil.hypeproof.studio.attacker.com",
)


def _split_csv(raw: str) -> list[str]:
    return [x.strip() for x in raw.split(",") if x.strip()]


def build_cors_kwargs(env: Mapping[str, str] | None = None) -> dict:
    """Build the kwargs dict for ``CORSMiddleware`` from the environment.

    Safe by default: no ``*.vercel.app`` allowance at all, and no
    ``allow_origin_regex`` is ever emitted — the allowed set is an explicit
    list of first-party origins plus whatever ops enumerates in
    ``SEDIMENT_CORS_EXTRA_ORIGINS``. Raises ``ValueError`` if the resulting
    credentialed policy would trust a hostile origin.
    """
    env = os.environ if env is None else env

    allow_origins = list(_FIRST_PARTY_ORIGINS)
    allow_origins.extend(_split_csv(env.get("SEDIMENT_CORS_EXTRA_ORIGINS", "")))

    kwargs: dict = {
        "allow_origins": allow_origins,
        "allow_credentials": True,
        "allow_methods": ["*"],
        "allow_headers": ["*"],
    }

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
            "(sediment#80). A hostname label cannot identify a tenant on a "
            "shared apex — enumerate preview origins explicitly in "
            "SEDIMENT_CORS_EXTRA_ORIGINS instead."
        )
