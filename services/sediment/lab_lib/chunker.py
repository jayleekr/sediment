"""Heading-aware Markdown chunker.

Strategy:
  1. Split by H1/H2/H3 headings preserving hierarchy.
  2. If any section > max_tokens, sub-split by paragraph.
  3. Add overlap of ~200 tokens between consecutive chunks of the SAME section.

WO-7 2026-05-23 (REPORT.md HIGH × 2):
  - Section-crossing overlap no longer mislabels heading_path. Previously
    chunk N (section B) prepended the tail of chunk N-1 (section A) but kept
    heading_path "B" — citation provenance for the overlap text pointed at
    the wrong section, breaking architectural law #2 (every answer cites).
    Now: overlap is only applied when consecutive chunks share heading_path.
  - tiktoken tail-decode no longer prepends `\\ufffd` (replacement char) to
    Korean overlap. cl100k_base splits multi-byte UTF-8 mid-codepoint when
    you slice the last N tokens. The decoded string starts with one or more
    replacement chars → embedding model treats every overlap chunk as
    "junk-prefix + content" and KO recall drops. Now: we walk forward past
    any leading `\\ufffd` before joining.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")

# Unicode REPLACEMENT CHARACTER — what tiktoken's decode emits for broken
# multi-byte sequences. We strip leading occurrences when computing overlap
# so Korean / Japanese / Chinese chunks don't gain a junk prefix.
_REPLACEMENT = "�"


@dataclass
class Chunk:
    seq: int
    content: str
    heading_path: str  # e.g., "Section A > Subsection 1"


def _ntok(s: str) -> int:
    return len(_enc.encode(s))


def _split_by_paragraph(text: str, max_tokens: int) -> list[str]:
    paragraphs = [p.strip() for p in re.split(r"\n{2,}", text) if p.strip()]
    out: list[str] = []
    current = ""
    for p in paragraphs:
        candidate = (current + "\n\n" + p) if current else p
        if _ntok(candidate) > max_tokens and current:
            out.append(current)
            current = p
        else:
            current = candidate
    if current:
        out.append(current)
    return out


def _clean_tail_tokens(tokens: list[int], n: int) -> str:
    """Return the last ``n`` tokens decoded to a string with no leading
    `\\ufffd` (UTF-8 replacement) chars.

    tiktoken's cl100k_base assigns multi-token sequences to Korean / Japanese
    / Chinese characters (and many emoji). Slicing the last N tokens often
    cuts a multi-token codepoint in the middle, and ``decode`` produces a
    replacement char for the broken halves. Embedding models treat these as
    junk prefix.

    We start with ``tokens[-n:]`` and pull additional context tokens to the
    left until the decoded string begins cleanly (or until we've consumed
    half the source). Worst case: prefix stays — better than truncating
    arbitrary text.
    """
    if not tokens or n <= 0:
        return ""
    n = min(n, len(tokens))
    cap = min(len(tokens), n + 16)  # at most 16 extra tokens of left-walk
    while n <= cap:
        decoded = _enc.decode(tokens[-n:])
        if not decoded.startswith(_REPLACEMENT):
            return decoded
        n += 1
    # Give up — strip leading replacement chars so we at least don't ship the
    # broken bytes. Better imperfect content than guaranteed junk prefix.
    return _enc.decode(tokens[-n:]).lstrip(_REPLACEMENT)


def chunk_markdown(text: str, max_tokens: int = 1500, overlap_tokens: int = 200) -> list[Chunk]:
    """Returns list of Chunk preserving heading hierarchy in heading_path."""
    lines = text.split("\n")
    sections: list[tuple[str, list[str]]] = []  # (heading_path, lines)
    stack: list[str] = []
    buf: list[str] = []

    def flush():
        if buf:
            path = " > ".join(stack) if stack else ""
            sections.append((path, list(buf)))
            buf.clear()

    for line in lines:
        m = re.match(r"^(#{1,3})\s+(.+)$", line)
        if m:
            flush()
            level = len(m.group(1))
            title = m.group(2).strip()
            stack = stack[: level - 1] + [title]
            buf.append(line)
        else:
            buf.append(line)
    flush()

    # If file had no headings, treat all as one section
    if not sections:
        sections = [("", lines)]

    chunks: list[Chunk] = []
    seq = 0
    for path, slines in sections:
        text = "\n".join(slines).strip()
        if not text:
            continue
        if _ntok(text) <= max_tokens:
            chunks.append(Chunk(seq=seq, content=text, heading_path=path))
            seq += 1
        else:
            for sub in _split_by_paragraph(text, max_tokens):
                chunks.append(Chunk(seq=seq, content=sub, heading_path=path))
                seq += 1

    # Apply overlap — but ONLY between chunks of the same section.
    # Section-crossing overlap would put text from section A into chunk N
    # whose heading_path is section B; citation provenance for the overlap
    # range would be wrong (law #2 violation). Clean section boundaries are
    # the safer default — no overlap across them.
    if overlap_tokens > 0 and len(chunks) > 1:
        new_chunks: list[Chunk] = [chunks[0]]
        for i in range(1, len(chunks)):
            curr = chunks[i]
            prev = chunks[i - 1]
            if prev.heading_path != curr.heading_path:
                # Different section — no overlap, preserve clean boundary.
                new_chunks.append(Chunk(seq=i, content=curr.content,
                                        heading_path=curr.heading_path))
                continue
            prev_tokens = _enc.encode(prev.content)
            tail = _clean_tail_tokens(prev_tokens, overlap_tokens)
            merged = tail + "\n\n" + curr.content if tail else curr.content
            new_chunks.append(Chunk(seq=i, content=merged,
                                    heading_path=curr.heading_path))
        chunks = new_chunks

    return chunks
