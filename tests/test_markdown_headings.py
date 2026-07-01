from __future__ import annotations

from neo_localmcp.utils import extract_markdown_headings, extract_symbols


def test_extracts_atx_headings_with_levels_and_ranges():
    text = "\n".join([
        "# Title",
        "intro line",
        "## Section A",
        "body a1",
        "body a2",
        "## Section B",
        "body b1",
        "### Section B.1",
        "body b1.1",
        "## Section C",
        "body c1",
    ])
    headings = extract_markdown_headings(text)
    names = [h["name"] for h in headings]
    assert names == ["Title", "Section A", "Section B", "Section B.1", "Section C"]

    by_name = {h["name"]: h for h in headings}
    # Section A ends right before Section B starts.
    assert by_name["Section A"]["start_line"] == 3
    assert by_name["Section A"]["end_line"] == 5
    # Section B.1 (level 3) ends before Section C (level 2) closes the parent too.
    assert by_name["Section B.1"]["start_line"] == 8
    assert by_name["Section B.1"]["end_line"] == 9
    assert by_name["Section B"]["start_line"] == 6
    assert by_name["Section B"]["end_line"] == 9
    # Last heading runs to EOF.
    assert by_name["Section C"]["end_line"] == 11
    assert all(h["kind"] == "heading" for h in headings)


def test_fenced_code_blocks_do_not_produce_heading_symbols():
    text = "\n".join([
        "# Real Heading",
        "```markdown",
        "## Fake heading inside fence",
        "```",
        "~~~",
        "### Another fake, tilde fence",
        "~~~",
        "## Second Real Heading",
    ])
    headings = extract_markdown_headings(text)
    names = [h["name"] for h in headings]
    assert names == ["Real Heading", "Second Real Heading"]


def test_indented_code_block_heading_like_text_is_ignored():
    text = "\n".join([
        "# Real",
        "    ## not a heading, 4-space indented code",
        "## Also Real",
    ])
    headings = extract_markdown_headings(text)
    assert [h["name"] for h in headings] == ["Real", "Also Real"]


def test_closing_hashes_and_blank_heading_text_are_handled():
    text = "\n".join([
        "## Trailing hashes ##",
        "###",  # no text after marker -> not a heading
        "## Next",
    ])
    headings = extract_markdown_headings(text)
    assert [h["name"] for h in headings] == ["Trailing hashes", "Next"]


def test_extract_symbols_routes_markdown_to_headings():
    text = "# A\nbody\n## B\nbody2\n"
    syms = extract_symbols(text, "markdown")
    assert [s["kind"] for s in syms] == ["heading", "heading"]
    assert [s["name"] for s in syms] == ["A", "B"]


def test_extract_symbols_non_markdown_unaffected():
    text = "def foo():\n    return 1\n"
    syms = extract_symbols(text, "python")
    assert syms and syms[0]["kind"] == "function"
    assert syms[0]["name"] == "foo"
