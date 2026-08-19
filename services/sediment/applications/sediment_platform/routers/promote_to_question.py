"""POST /api/v1/feedback/promote-to-question — file a good answer as knowledge.

sediment#144. The symmetric half of `promote_to_golden.py`.

The feedback loop only captured FAILURES. A bad answer became a golden-query
proposal; a good one sank into the conversation log and the next person asking
the same thing paid for the same retrieval and the same synthesis again. A wiki
files good answers back into `questions/` precisely so that stops happening —
that is what "knowledge compounds" means, and Sediment had only the half that
records mistakes.

Two things keep this from poisoning the corpus.

**A promoted answer is never allowed to outrank its own evidence.** It gets a
low ``confidence`` and `origin='derived'` (#140), and it keeps `derived_from`
links to the chunks it was built from (#141). Answers grounded on answers is
the failure mode that turns a knowledge layer into a rumour mill, and the
evidence chain is what makes it visible when it starts.

**The bar is evidence, not popularity.** A thumbs-up alone does not qualify: an
answer can be liked and wrong. It must also have passed grounding and, where a
judge has scored it, cleared the faithfulness floor.
"""
from __future__ import annotations

import json
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import text

from lab_lib.auth import Identity, require_identity
from lab_lib.db import app_session
from lab_lib.links import create_link
from lab_lib.settings import settings
from lab_lib.visibility import DEFAULT_VISIBILITY

import httpx

router = APIRouter(tags=["feedback"])

INGESTER_URL = f"http://localhost:{settings.vault_ingester_port}/v1/ingest/document"

#: Minimum faithfulness a judged answer must clear. Unjudged answers are NOT
#: rejected — most answers are never judged, and refusing them would make this
#: endpoint useless. A judged-and-failing answer is a different matter: we were
#: told it is unfaithful.
MIN_FAITHFULNESS = 0.7

#: Deliberately low. A page derived from an answer is weaker evidence than the
#: sources that answer cited, and it must rank that way.
PROMOTED_ANSWER_CONFIDENCE = 0.3


class PromoteQuestionReq(BaseModel):
    message_id: str = Field(..., description="Assistant message to file (the GOOD one)")
    title: Optional[str] = Field(
        default=None, max_length=120,
        description="Page title. Defaults to the user's question.")
    note: Optional[str] = Field(
        default=None, max_length=500,
        description="Why this answer is worth keeping.")


def _slug(s: str) -> str:
    import re
    out = re.sub(r"[^\w가-힣]+", "-", s.strip().lower()).strip("-")
    return (out or "question")[:80]


def _page_markdown(title: str, question: str, answer: str,
                   refs: list[str], note: Optional[str]) -> str:
    import yaml
    fm = {
        "type": "question",
        "title": title,
        "question": question,
        "answer_source": "promoted_answer",
        "confidence": PROMOTED_ANSWER_CONFIDENCE,
        "sources": refs,
    }
    if note:
        fm["note"] = note
    block = yaml.safe_dump(fm, allow_unicode=True, sort_keys=False).strip()
    body = [f"---\n{block}\n---\n", f"# {title}\n", f"**Q.** {question}\n", answer.strip(), ""]
    if refs:
        body.append("\n## Evidence\n")
        body += [f"- `{r}`" for r in refs]
        body.append("")
    return "\n".join(body)


@router.post("/promote-to-question", status_code=201)
async def promote_to_question(req: PromoteQuestionReq,
                              identity: Identity = Depends(require_identity)):
    """File a good answer as a citable `question` page.

    Rate-limited by the tenant middleware's default budget on /feedback/*,
    same as promote-to-golden.
    """
    async with app_session(identity.tenant_id) as s:
        r = await s.execute(text("""
            SELECT m.id::text, m.content, m.grounding_status, m.citations,
                   u.content AS user_query,
                   (SELECT min(js.score) FROM message_judge_scores js
                     WHERE js.message_id = m.id AND js.judge = 'faithfulness')
                     AS faithfulness,
                   EXISTS (SELECT 1 FROM message_signals sg
                            WHERE sg.message_id = m.id AND sg.kind = 'thumbs_up')
                     AS has_thumbs_up
            FROM messages m
            LEFT JOIN messages u
              ON u.conv_id = m.conv_id AND u.role = 'user' AND u.ts < m.ts
             AND u.ts = (SELECT max(ts) FROM messages
                          WHERE conv_id = m.conv_id AND role = 'user' AND ts < m.ts)
            WHERE m.id = CAST(:mid AS uuid)
              AND m.role = 'assistant' AND m.archived = false
        """), {"mid": req.message_id})
        row = r.first()
        if row is None:
            raise HTTPException(status_code=404, detail="message_id not found")
        (mid, answer, grounding_status, citations,
         user_query, faithfulness, has_thumbs_up) = row

        if not user_query:
            raise HTTPException(
                status_code=400,
                detail="no preceding user message — a question page needs its question")

        # The bar is evidence, not popularity. Each of these is a separate way
        # for a liked answer to still be one we must not file as knowledge.
        if not has_thumbs_up:
            raise HTTPException(
                status_code=409,
                detail="answer has no thumbs_up — only answers someone vouched for")
        if grounding_status != "ok":
            raise HTTPException(
                status_code=409,
                detail=f"grounding_status is {grounding_status!r}, not 'ok' — an "
                       "ungrounded answer is exactly what must not become a source")
        if faithfulness is not None and faithfulness < MIN_FAITHFULNESS:
            raise HTTPException(
                status_code=409,
                detail=f"faithfulness {faithfulness:.2f} is below {MIN_FAITHFULNESS} — "
                       "a judge already said this answer is not faithful to its sources")

        if isinstance(citations, str):
            citations = json.loads(citations or "[]")
        citations = citations or []
        refs = [c.get("ref") for c in citations if isinstance(c, dict) and c.get("ref")]
        chunk_ids = [c.get("chunk_id") for c in citations
                     if isinstance(c, dict) and c.get("chunk_id")]
        source_artifact_ids = [c.get("artifact_id") for c in citations
                               if isinstance(c, dict) and c.get("artifact_id")]

        title = req.title or (user_query.strip().splitlines()[0][:120])
        ref = f"question/{_slug(title)}"
        # sediment#162: two members promoting answers to the same question slug
        # is a real race — the second would otherwise silently replace the
        # first's page. Read the current rev in the same session that decided
        # the ref, and arm the optimistic lock with it.
        rv = await s.execute(text("""
            SELECT rev FROM artifacts
            WHERE tenant_id = current_tenant_id() AND ref = :ref
        """), {"ref": ref})
        rev_row = rv.first()
        expected_rev = int(rev_row[0]) if rev_row else None
        markdown = _page_markdown(title, user_query.strip(), answer, refs, req.note)

    # Ingest outside the read session — the ingester owns its own transaction
    # and uses the service role.
    async with httpx.AsyncClient() as client:
        resp = await client.post(INGESTER_URL, timeout=120, json={
            "tenant_id": str(identity.tenant_id),
            "ref": ref,
            "type": "question",
            "body": markdown,
            "origin": "derived",
            "confidence": PROMOTED_ANSWER_CONFIDENCE,
            "visibility": DEFAULT_VISIBILITY,
            "source_ref": f"promote-question:{mid}",
            "expected_rev": expected_rev,
        })
    if resp.status_code == 409:
        # Interactive caller, unlike distill's batch path: tell them to re-read
        # rather than silently overwriting the other promotion.
        raise HTTPException(
            status_code=409,
            detail=(f"{ref!r} was updated by someone else while this promotion "
                    "was being prepared — re-read the page and retry"))
    if resp.status_code != 200:
        raise HTTPException(status_code=502,
                            detail=f"ingest failed ({resp.status_code}) — page not created")
    artifact_id = resp.json().get("artifact_id")

    linked = 0
    async with app_session(identity.tenant_id) as s:
        # The evidence chain is what keeps this from becoming a rumour mill:
        # every promoted page can be traced to the chunks that produced it, so
        # answers-grounded-on-answers is visible rather than inferred.
        for src_artifact in dict.fromkeys(source_artifact_ids):
            if str(src_artifact) == str(artifact_id):
                continue
            try:
                if await create_link(
                    s, str(identity.tenant_id), artifact_id, str(src_artifact),
                    "derived_from",
                    evidence_chunk_ids=chunk_ids,
                    note="promoted answer",
                    created_by=identity.member_id,
                ):
                    linked += 1
            except ValueError:
                continue
        await s.execute(text("""
            INSERT INTO events (tenant_id, source, kind, member_id, payload)
            VALUES (current_tenant_id(), 'web', 'question.promoted', :mid,
                    CAST(:p AS jsonb))
        """), {
            "mid": identity.member_id,
            "p": json.dumps({"message_id": mid, "ref": ref,
                             "artifact_id": artifact_id, "note": req.note},
                            default=str),
        })

    return {"ref": ref, "artifact_id": artifact_id,
            "confidence": PROMOTED_ANSWER_CONFIDENCE,
            "evidence_links": linked}
