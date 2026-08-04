"""Entity extraction — the producer that `mentions` links were waiting for.

sediment#168.

#140 added `entity` to the artifact type CHECK and #141 added a `mentions` link
kind. Neither had a producer, so "information from several sources links up
organically" was a schema, not a behaviour: the same project could appear in a
Discord transcript, a spec and a decision page, and nothing recorded that those
were the same thing.

Three deliberate limits, each one a case where being wrong costs more than
being absent.

**No people.** Deciding that "라이언", "ryan-k" and "Ryan Kim" are one person
needs identity data this system does not have. A wrong merge attributes one
colleague's words to another; a wrong split makes a person look like two.
Both are worse than no page.

**Mentions live in the link table, never in the page body.** If the page listed
what mentions it, every new mention would rewrite the body, which means a
revision (#138), a full chunk delete-and-re-embed, and a `rev` bump — per
mention. The body only changes when the entity's own description changes.

**Learned aliases do not touch ranking.** They land in `tenant_aliases` with
`target_kind='entity'`, and `lab_lib.aliases.build_index` ignores that kind
(pinned by test_tenant_aliases). So the vocabulary accumulates for later use
without silently re-weighting anybody's search results today.
"""
from __future__ import annotations

import re
from typing import Any, Optional

from sqlalchemy import text

from lab_lib.links import create_link
from lab_lib.logging import get_logger
from lab_lib.prompts import load_strategy, render_messages
from lab_lib.settings import settings

log = get_logger("entities")

ENTITY_KINDS = ("project", "repo", "product", "org")

#: Cap per document. A source that appears to name more distinct entities than
#: this is almost always producing noise — a glossary page, a link dump, or a
#: model that started listing every proper noun it saw.
MAX_ENTITIES_PER_SOURCE = 12


def entity_slug(name: str) -> str:
    """Stable slug for an entity name. Empty/symbol-only names are rejected by
    returning '' so the caller can skip rather than create `entity/unknown`."""
    s = re.sub(r"\s+", "-", (name or "").strip().lower())
    s = re.sub(r"[^0-9a-z가-힣\-]", "", s)
    return s.strip("-")[:60]


def entity_ref(name: str) -> str:
    return f"entity/{entity_slug(name)}"


def entity_markdown(entity: dict) -> tuple[str, str]:
    """Canonical page for one entity. Returns (ref, body).

    The body is intentionally thin: name, kind, aliases, one description. What
    mentions this entity is NOT written here — see the module docstring.
    """
    import yaml

    name = (entity.get("name") or "").strip()
    kind = entity.get("kind")
    aliases = [a for a in (entity.get("aliases") or []) if a and a.strip()]
    desc = (entity.get("description") or "").strip() or "(not described in this source)"
    fm = {
        "type": "entity",
        "name": name,
        "entity_kind": kind,
        "slug": entity_slug(name),
        "aliases": aliases,
    }
    fm_block = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    body_lines = [f"---\n{fm_block}\n---\n", f"# {name}\n", desc, ""]
    if aliases:
        body_lines.append("\n**Also referred to as:** " + ", ".join(f"`{a}`" for a in aliases) + "\n")
    return entity_ref(name), "\n".join(body_lines)


def _dedupe(entities: list[dict]) -> list[dict]:
    """One entry per slug, keeping the highest-confidence spelling and merging
    the aliases of the ones dropped — otherwise a document that says both
    "Sediment" and "sediment" produces two pages for one thing."""
    best: dict[str, dict] = {}
    for e in entities:
        slug = entity_slug(e.get("name") or "")
        if not slug:
            continue
        aliases = list(e.get("aliases") or [])
        if slug in best:
            kept = best[slug]
            for a in [e.get("name")] + aliases:
                if a and a != kept.get("name") and a not in kept.setdefault("aliases", []):
                    kept["aliases"].append(a)
            if float(e.get("confidence") or 0) > float(kept.get("confidence") or 0):
                # Higher-confidence spelling wins the canonical name; the
                # displaced one becomes an alias rather than being lost.
                displaced = kept.get("name")
                kept["name"] = e.get("name")
                kept["confidence"] = e.get("confidence")
                if displaced and displaced not in kept["aliases"]:
                    kept["aliases"].append(displaced)
        else:
            best[slug] = {**e, "aliases": aliases}
    return list(best.values())


def filter_entities(raw: list[dict], *, threshold: float) -> list[dict]:
    """Drop malformed, low-confidence and over-quota candidates.

    Kind is validated rather than trusted: the tool schema constrains it, but a
    value outside ENTITY_KINDS would fail the artifact type CHECK at ingest
    time and take the whole distill run's error budget with it.
    """
    out: list[dict] = []
    for e in raw or []:
        if not isinstance(e, dict):
            continue
        if not entity_slug(e.get("name") or ""):
            continue
        if e.get("kind") not in ENTITY_KINDS:
            continue
        if float(e.get("confidence") or 0) < threshold:
            continue
        out.append(e)
    out = _dedupe(out)
    out.sort(key=lambda e: float(e.get("confidence") or 0), reverse=True)
    return out[:MAX_ENTITIES_PER_SOURCE]


async def extract_entities(text_body: str, *, tenant_id: Optional[str] = None) -> list[dict]:
    """Ask the model which workspace entities this document names.

    Returns [] rather than raising on any failure. Entity extraction is an
    enrichment pass at the tail of distill; losing it must not cost the
    decisions and transcripts that ran before it.
    """
    if not settings.anthropic_api_key or settings.anthropic_api_key == "sk-ant-...":
        return []
    try:
        strategy = load_strategy("entities", "base", tenant_id=tenant_id)
    except Exception as e:
        log.warning("entities.strategy_load_failed", err=str(e)[:200])
        return []

    body = (text_body or "").strip()
    if len(body) < strategy.min_body_chars:
        return []

    try:
        from anthropic import AsyncAnthropic
        from lab_lib.cost_tracker import record_call

        client = AsyncAnthropic(api_key=settings.anthropic_api_key)
        msgs = render_messages(strategy, user_text=body[:32000])
        resp = await client.messages.create(
            model=settings.llm_model_default,
            max_tokens=1536,
            system=strategy.system_prompt,
            tools=[strategy.tool_schema],
            tool_choice={"type": "tool", "name": strategy.tool_schema["name"]},
            messages=msgs,
        )
        try:
            await record_call(
                model=str(resp.model), agent="entities", strategy=strategy.name,
                prompt_version=strategy.prompt_version,
                tokens_in=int(resp.usage.input_tokens),
                tokens_out=int(resp.usage.output_tokens),
            )
        except Exception:
            pass  # cost tracking must never block extraction

        raw: list[dict] = []
        for block in resp.content:
            if getattr(block, "type", None) == "tool_use":
                raw = (getattr(block, "input", {}) or {}).get("entities") or []
                break
        return filter_entities(raw, threshold=strategy.confidence_threshold)
    except Exception as e:
        log.warning("entities.extract_failed", err=str(e)[:200])
        return []


async def learn_aliases(session, tenant_id: str, entity: dict) -> int:
    """Record surface variants in tenant_aliases as source='learned'.

    This is the path sediment#139 designed for and left empty: the table
    already allows 'learned', but nothing wrote it, so tenant vocabulary could
    only ever be seeded or hand-edited.

    Low confidence on purpose. `build_index` ignores target_kind='entity'
    entirely today, so these rows accumulate without changing anybody's ranking
    — the point is to have the vocabulary when something is ready to use it,
    not to start steering retrieval from an LLM guess.
    """
    name = (entity.get("name") or "").strip()
    variants = {a.strip() for a in (entity.get("aliases") or []) if a and a.strip()}
    if not variants:
        # A canonical name with no observed variants teaches nothing — the
        # alias table exists to map OTHER spellings onto it.
        return 0
    variants.add(name)
    written = 0
    for alias in variants:
        if not alias:
            continue
        try:
            r = await session.execute(text("""
                INSERT INTO tenant_aliases
                    (tenant_id, alias, target_kind, target_value, source, confidence)
                VALUES (CAST(:tid AS uuid), :alias, 'entity', :target, 'learned', 0.3)
                ON CONFLICT (tenant_id, alias, target_kind) DO NOTHING
                RETURNING id
            """), {"tid": str(tenant_id), "alias": alias.lower(), "target": name})
            if r.first():
                written += 1
        except Exception as e:
            log.warning("entities.alias_failed", alias=alias, err=str(e)[:150])
    return written


async def link_mention(session, tenant_id: str, source_artifact_id: str,
                       entity_artifact_id: str, *,
                       evidence_chunk_ids: Optional[list[str]] = None) -> bool:
    """Record that a document mentions an entity. Idempotent (#141)."""
    if not source_artifact_id or not entity_artifact_id:
        return False
    if str(source_artifact_id) == str(entity_artifact_id):
        return False
    try:
        created = await create_link(
            session, tenant_id, source_artifact_id, entity_artifact_id, "mentions",
            evidence_chunk_ids=evidence_chunk_ids or [],
            note="entity extraction",
        )
        return bool(created)
    except Exception as e:
        log.warning("entities.link_failed", err=str(e)[:150])
        return False


def summarize(counters: dict[str, Any]) -> str:
    return (f"entities: {counters.get('pages', 0)} page(s), "
            f"{counters.get('mentions', 0)} mention link(s), "
            f"{counters.get('aliases', 0)} learned alias(es)")
