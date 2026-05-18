"""Heading-aware Markdown chunker.

Strategy:
  1. Split by H1/H2/H3 headings preserving hierarchy.
  2. If any section > max_tokens, sub-split by paragraph.
  3. Add overlap of ~200 tokens between consecutive chunks.
"""
from __future__ import annotations
import re
from dataclasses import dataclass

import tiktoken

_enc = tiktoken.get_encoding("cl100k_base")


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

    # Apply overlap (prepend tail of previous chunk to current)
    if overlap_tokens > 0 and len(chunks) > 1:
        new_chunks: list[Chunk] = [chunks[0]]
        for i in range(1, len(chunks)):
            prev_tokens = _enc.encode(chunks[i - 1].content)
            tail_tokens = prev_tokens[-overlap_tokens:]
            tail = _enc.decode(tail_tokens)
            merged = tail + "\n\n" + chunks[i].content
            new_chunks.append(Chunk(seq=i, content=merged, heading_path=chunks[i].heading_path))
        chunks = new_chunks

    return chunks
