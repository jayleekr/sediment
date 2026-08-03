"""sediment#168 — the producer that `mentions` links were waiting for.

#140 added `entity` to the artifact type CHECK; #141 added a `mentions` link
kind. Neither had a producer, so "information from several sources links up
organically" was a schema rather than a behaviour.

These tests pin the three limits where being WRONG costs more than being
absent — no people, no body churn, no silent ranking change — plus the
filtering that keeps a bad extraction from becoming a permanent retrieval hub.
"""
from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from lab_lib.entities import (
    ENTITY_KINDS,
    MAX_ENTITIES_PER_SOURCE,
    entity_markdown,
    entity_ref,
    entity_slug,
    filter_entities,
    learn_aliases,
    link_mention,
)
from lab_lib.prompts import load_strategy

REPO = Path(__file__).resolve().parents[3]
SVC = REPO / "services" / "sediment"


def _read(rel: str) -> str:
    return (SVC / rel).read_text()


def _ent(name="Sediment", kind="project", conf=0.9, aliases=None, desc="A memory layer."):
    e = {"name": name, "kind": kind, "confidence": conf, "description": desc}
    if aliases is not None:
        e["aliases"] = aliases
    return e


# ---------------------------------------------------------------------------
# Slug / ref
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("name,expected", [
    ("Sediment", "sediment"),
    ("AI 큐레이터", "ai-큐레이터"),
    ("  Spaced  Name  ", "spaced-name"),
])
def test_slug_normalizes(name, expected):
    assert entity_slug(name) == expected


def test_slug_is_idempotent():
    for n in ("Sediment", "AI 큐레이터", "a-b-c"):
        assert entity_slug(entity_slug(n)) == entity_slug(n)


@pytest.mark.parametrize("bad", ["", "   ", "!!!", "@#$"])
def test_unusable_names_produce_no_slug(bad):
    """Returning '' lets the caller skip rather than create `entity/unknown`,
    which would become a junk drawer every bad extraction links into."""
    assert entity_slug(bad) == ""


def test_ref_is_namespaced():
    assert entity_ref("Sediment") == "entity/sediment"


# ---------------------------------------------------------------------------
# Page shape — the body must not churn
# ---------------------------------------------------------------------------

def test_page_carries_identity_not_a_mention_list():
    """If the page listed what mentions it, every new mention would rewrite the
    body — a revision (#138), a full chunk re-embed, and a rev bump per
    mention. Mentions live in the link table."""
    _ref, body = entity_markdown(_ent(aliases=["세디먼트"]))
    fm = yaml.safe_load(body.split("---")[1])
    assert fm["type"] == "entity"
    assert fm["name"] == "Sediment"
    assert fm["entity_kind"] == "project"
    assert fm["aliases"] == ["세디먼트"]
    assert "A memory layer." in body
    for leak in ("mention", "Mentioned in", "discord/"):
        assert leak not in body


def test_page_is_stable_across_calls():
    """Same entity in → byte-identical page out, so a second run produces no
    revision and no re-embed."""
    a = entity_markdown(_ent(aliases=["세디먼트"]))
    b = entity_markdown(_ent(aliases=["세디먼트"]))
    assert a == b


def test_missing_description_is_stated_not_invented():
    _ref, body = entity_markdown(_ent(desc=""))
    assert "(not described in this source)" in body


# ---------------------------------------------------------------------------
# Filtering — a wrong entity page is worse than a missing one
# ---------------------------------------------------------------------------

def test_low_confidence_is_dropped():
    kept = filter_entities([_ent(conf=0.4), _ent(name="Other", conf=0.9)], threshold=0.75)
    assert [e["name"] for e in kept] == ["Other"]


def test_unknown_kind_is_dropped_not_passed_through():
    """The tool schema constrains kind, but a stray value would fail the
    artifact type CHECK at ingest and burn the run's error budget."""
    kept = filter_entities([_ent(kind="person"), _ent(kind="concept")], threshold=0.5)
    assert kept == []


def test_every_accepted_kind_is_a_valid_artifact_type():
    """entity pages are written with type='entity', but kind drives nothing
    else — guard against someone wiring kind into the artifact type later."""
    assert set(ENTITY_KINDS) == {"project", "repo", "product", "org"}
    assert "person" not in ENTITY_KINDS


def test_nameless_entries_are_dropped():
    kept = filter_entities([{"name": "!!!", "kind": "project", "confidence": 1.0}],
                           threshold=0.5)
    assert kept == []


def test_case_variants_collapse_to_one_page():
    """A document that says both "Sediment" and "sediment" names one thing."""
    kept = filter_entities(
        [_ent(name="Sediment", conf=0.9), _ent(name="sediment", conf=0.8)],
        threshold=0.5)
    assert len(kept) == 1
    assert kept[0]["name"] == "Sediment"
    # The displaced spelling survives as an alias rather than being lost.
    assert "sediment" in kept[0]["aliases"]


def test_higher_confidence_spelling_wins_the_canonical_name():
    kept = filter_entities(
        [_ent(name="sediment", conf=0.6), _ent(name="Sediment", conf=0.95)],
        threshold=0.5)
    assert kept[0]["name"] == "Sediment"
    assert "sediment" in kept[0]["aliases"]


def test_over_quota_extraction_is_capped():
    """A source that appears to name dozens of entities is producing noise —
    a glossary page, a link dump, or a model listing every proper noun."""
    many = [_ent(name=f"Proj{i}", conf=0.9) for i in range(MAX_ENTITIES_PER_SOURCE + 8)]
    assert len(filter_entities(many, threshold=0.5)) == MAX_ENTITIES_PER_SOURCE


def test_empty_extraction_is_a_valid_result():
    assert filter_entities([], threshold=0.5) == []
    assert filter_entities(None, threshold=0.5) == []


# ---------------------------------------------------------------------------
# Alias learning — must not steer ranking
# ---------------------------------------------------------------------------

class _Rec:
    def __init__(self, rows=1):
        self.rows = rows

    def first(self):
        return ("id",) if self.rows else None


class _Session:
    def __init__(self):
        self.params: list[dict] = []

    async def execute(self, _stmt, params=None):
        self.params.append(params or {})
        return _Rec()

    async def commit(self):
        pass


async def test_aliases_are_learned_at_low_confidence_under_entity_kind():
    """`build_index` ignores target_kind='entity' (pinned in
    test_tenant_aliases), so these rows accumulate without re-weighting
    anybody's search results from an LLM guess."""
    s = _Session()
    n = await learn_aliases(s, "t", _ent(aliases=["세디먼트", "sed"]))
    assert n == 3  # two variants + the canonical name
    for p in s.params:
        assert p["target"] == "Sediment"
    sql = " ".join(str(x) for x in s.params)
    assert all(p["alias"] == p["alias"].lower() for p in s.params), "aliases must be normalized"
    assert "세디먼트" in sql


async def test_no_variants_teaches_nothing_and_writes_nothing():
    """The alias table maps OTHER spellings onto a name; a lone canonical name
    is not an alias."""
    s = _Session()
    assert await learn_aliases(s, "t", _ent(aliases=[])) == 0
    assert s.params == []


# ---------------------------------------------------------------------------
# Mention links
# ---------------------------------------------------------------------------

async def test_mention_link_needs_both_ends():
    s = _Session()
    assert await link_mention(s, "t", "", "e1") is False
    assert await link_mention(s, "t", "a1", "") is False


async def test_entity_page_does_not_mention_itself():
    s = _Session()
    assert await link_mention(s, "t", "same", "same") is False


# ---------------------------------------------------------------------------
# Prompt contract
# ---------------------------------------------------------------------------

def test_entities_strategy_loads_with_its_own_tool_schema():
    st = load_strategy("entities", "base")
    assert st.tool_schema["name"] == "record_entities"
    assert st.confidence_threshold >= 0.6, (
        "a wrong entity page becomes a retrieval hub; the floor must exceed distill's"
    )


def test_prompt_forbids_people_in_both_prose_and_guards():
    """The single most important instruction: resolving whether two spellings
    are one person needs identity data this system does not have."""
    st = load_strategy("entities", "base")
    assert "Never emit a person as an entity" in st.system_prompt
    assert "DO NOT extract" in st.system_prompt
    raw = yaml.safe_load(_read("prompts/entities/base.yaml"))
    assert any("person" in g.lower() for g in raw["guards"])
    kinds = raw["tool_schema"]["input_schema"]["properties"]["entities"]["items"]["properties"]["kind"]["enum"]
    assert "person" not in kinds
    assert set(kinds) == set(ENTITY_KINDS)


def test_prompt_allows_an_empty_answer():
    """Inventing entities to fill the schema poisons the graph permanently."""
    st = load_strategy("entities", "base")
    assert "empty" in st.system_prompt.lower()


# ---------------------------------------------------------------------------
# Wiring
# ---------------------------------------------------------------------------

def test_extraction_runs_last_in_the_source_loop():
    """Enrichment tail: its failure must not cost the decisions, actions and
    transcript the source already produced."""
    src = _read("scripts/distill.py")
    assert src.index('summary["actions"] += 1') < src.index("await _process_entities(")


def test_extraction_requires_a_landed_source_artifact():
    """A `mentions` link needs something to point FROM — that is what #161
    created. Conversations have no shared-vault artifact and are skipped."""
    src = _read("scripts/distill.py")
    assert "if not source_artifact_id:" in _read("scripts/distill.py")
    assert "source_artifact_ids.get(s[\"src\"])" in src


@pytest.mark.parametrize("counter", ["entity_pages", "entity_mentions", "entity_aliases"])
def test_run_summary_reports_entity_work(counter):
    assert f'"{counter}"' in _read("scripts/distill.py")
