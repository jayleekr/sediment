from lab_lib.chunker import chunk_markdown


def test_basic_split():
    md = "# A\nbody A\n\n# B\nbody B"
    cs = chunk_markdown(md, max_tokens=1500)
    assert len(cs) == 2
    assert "body A" in cs[0].content
    assert "body B" in cs[1].content
    assert cs[0].heading_path == "A"
    assert cs[1].heading_path == "B"


def test_no_headings():
    md = "just paragraphs.\n\nanother one."
    cs = chunk_markdown(md, max_tokens=1500)
    assert len(cs) == 1


def _section(heading: str, word: str, paragraphs: int = 6,
             words_per_paragraph: int = 120) -> str:
    """A section long enough to sub-split WITHIN itself.

    Paragraph breaks are load-bearing: `_split_by_paragraph` splits on blank
    lines, so a single unbroken run of text stays one chunk no matter how far
    it exceeds max_tokens. The pre-#155 fixture was one such run and got its
    two chunks from having two SECTIONS — which is why it never exercised
    within-section overlap at all, only the cross-section case it then
    asserted backwards.
    """
    # Each paragraph is tagged so a specific paragraph can be located inside a
    # chunk. Uniform filler would make "the tail carried over" trivially true.
    body = "\n\n".join(
        f"{word}{i} " * words_per_paragraph for i in range(paragraphs)
    )
    return f"# {heading}\n{body}"


def test_overlap_applied_within_a_section():
    """Consecutive chunks of the SAME section carry the previous chunk's tail.

    sediment#155: this used to build its fixture from two different headings
    and assert that chunk B contained section A's text — i.e. it asserted
    exactly the behaviour WO-7 (2026-05-23) removed on purpose. See
    test_overlap_not_applied_across_sections for why.
    """
    md = _section("A", "alpha")
    cs = chunk_markdown(md, max_tokens=200, overlap_tokens=50)
    assert len(cs) >= 2, "fixture must be long enough to split within one section"
    assert all(c.heading_path == "A" for c in cs)

    # Compare against the same split with overlap OFF. Asserting on the text
    # alone cannot distinguish "the tail was carried over" from "this chunk
    # happens to contain that word".
    plain = chunk_markdown(md, max_tokens=200, overlap_tokens=0)
    assert len(cs) == len(plain)
    assert len(cs[1].content) > len(plain[1].content), "no overlap was prepended"
    assert cs[1].content.endswith(plain[1].content)


def test_overlap_not_applied_across_sections():
    """Crossing a heading boundary must NOT carry text over.

    This is the WO-7 contract and it had no regression guard at all
    (sediment#155). It is not a tidiness rule: a chunk labelled heading_path
    "B" that opens with section A's text makes every citation into that
    overlap range point at the wrong section — which breaks "every answer
    cites" at the level that actually matters, the level of *what* it cites.
    """
    md = _section("A", "alpha") + "\n\n" + _section("B", "beta")
    cs = chunk_markdown(md, max_tokens=200, overlap_tokens=50)

    b_chunks = [c for c in cs if c.heading_path == "B"]
    assert b_chunks, "fixture produced no section-B chunks"
    assert "alpha" not in b_chunks[0].content, (
        "section B's first chunk carries section A's tail — its citations "
        "would resolve to the wrong section"
    )
    # The guard only means something if overlap is switched on at all, so
    # confirm the within-section case still fires in this same fixture.
    assert len(b_chunks) >= 2
    assert "beta" in b_chunks[1].content
