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


def test_overlap_applied():
    long_a = "Section A " + ("alpha " * 600)
    long_b = "Section B " + ("beta " * 600)
    md = f"# A\n{long_a}\n\n# B\n{long_b}"
    cs = chunk_markdown(md, max_tokens=400, overlap_tokens=50)
    assert len(cs) >= 2
    # Second chunk should contain tail of first
    assert "alpha" in cs[1].content
