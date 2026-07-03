from __future__ import annotations

import json

from neo_localmcp import tools
from neo_localmcp.query import normalize_query
from neo_localmcp.utils import extract_markdown_headings


def test_prepare_context_is_bounded_and_complete(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("def load_model():\n    return 'ready'\n" + "# filler\n" * 200, encoding="utf-8")
    (repo / "test_service.py").write_text("from service import load_model\n\ndef test_load_model():\n    assert load_model()\n", encoding="utf-8")
    raw = tools.prepare_context("debug model loading: load_model", str(repo), token_budget=300, max_files=2, output_format="json")
    result = json.loads(raw)
    assert result["repo_status"]["index_complete"] is True
    assert result["retrieval_metrics"]["estimated_tokens_returned"] <= 305
    assert len(result["read_first"]) <= 2
    assert result["context_excerpts"]


def test_colon_focus_filters_filler_and_keeps_short_milestone():
    result = normalize_query("f4 token menu: goals, architecture decisions, implementation phases, and entry-point files")
    assert "f4" in result["strong_terms"]
    assert "and" not in result["strong_terms"]
    assert "architecture" not in result["strong_terms"]


def test_explicit_document_path_outranks_keyword_noise(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "f4_token_menu_plan.md").write_text("# f4 token menu plan\n", encoding="utf-8")
    (repo / "main.py").write_text(("goals architecture decisions implementation phases\n" * 30), encoding="utf-8")
    raw = tools.prepare_context(
        "Summarize docs/f4_token_menu_plan.md: goals, architecture decisions, implementation phases",
        str(repo), token_budget=300, max_files=1, output_format="json",
    )
    result = json.loads(raw)
    assert result["read_first"][0]["path"] == "docs/f4_token_menu_plan.md"


def test_short_milestone_finds_plan_without_explicit_filename(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    (repo / "docs").mkdir(parents=True)
    (repo / "docs" / "f4_token_menu_plan.md").write_text("# f4 token menu plan\n", encoding="utf-8")
    (repo / "main.py").write_text("goals architecture decisions implementation phases and entry-point files\n", encoding="utf-8")
    raw = tools.prepare_context(
        "f4 token menu: goals, architecture decisions, implementation phases, and entry-point files to modify",
        str(repo), token_budget=300, max_files=1, output_format="json",
    )
    result = json.loads(raw)
    assert result["read_first"][0]["path"] == "docs/f4_token_menu_plan.md"


def _build_long_plan_repo(repo, *, section_phrase="checklist outliner rendering tri-state rollup"):
    """A generic numbered-milestone plan doc, deliberately not AntiNotepad/f4-specific,
    so heading-section retrieval is verified as general behavior, not project overfit."""
    (repo / "docs").mkdir(parents=True)
    lines = ["# m9 Widget Plan Implementation Plan", ""]
    for n in range(1, 8):
        title = {6: f"m9.6 list mode view: {section_phrase}"}.get(n, f"m9.{n} section {n}")
        lines.append(f"## {title}")
        lines.append("")
        if n == 4:
            lines += ["```markdown", "## m9.6 FAKE HEADING INSIDE A FENCE", "```", ""]
        body = section_phrase if n == 6 else f"unrelated filler prose for section {n}"
        lines += [body] * 60
        lines.append("")
    (repo / "docs" / "m9_widget_plan.md").write_text("\n".join(lines), encoding="utf-8")
    # A code file colliding on a generic word ("rendering") used inside the target section.
    (repo / "Renderer.cs").write_text(
        "public class Renderer {\n"
        "  private void OnRendering() { }\n"
        "  private void EnsureRenderHook() { }\n" + "  // filler rendering rendering\n" * 40,
        encoding="utf-8",
    )
    return repo / "docs" / "m9_widget_plan.md"


def test_heading_section_found_without_filename_anchor(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_long_plan_repo(repo)
    raw = tools.prepare_context(
        "m9.6 list mode view rendering checklist outliner tri-state rollup",
        str(repo), token_budget=1500, max_files=3, output_format="json",
    )
    result = json.loads(raw)
    assert result["read_first"][0]["path"] == "docs/m9_widget_plan.md"


def test_excerpt_opens_at_matched_section_not_line_one(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    doc = _build_long_plan_repo(repo)
    raw = tools.prepare_context(
        "m9.6 list mode view rendering checklist outliner tri-state rollup",
        str(repo), token_budget=1500, max_files=3, output_format="json",
    )
    result = json.loads(raw)
    excerpt = next(e for e in result["context_excerpts"] if e["path"] == "docs/m9_widget_plan.md")
    assert excerpt["start_line"] > 1
    assert excerpt.get("matched_name", "").startswith("m9.6")
    # Locate the real (non-fenced) m9.6 heading via the same extractor the indexer
    # uses, rather than a fragile raw-text search that the fenced decoy can confuse.
    headings = extract_markdown_headings(doc.read_text(encoding="utf-8"))
    real_heading = next(h for h in headings if h["name"].startswith("m9.6") and "FAKE" not in h["name"])
    assert excerpt["start_line"] == real_heading["start_line"]


def test_exact_heading_beats_generic_code_symbol_collision(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_long_plan_repo(repo)
    raw = tools.prepare_context(
        "rendering checklist outliner tri-state rollup",
        str(repo), token_budget=1500, max_files=3, output_format="json",
    )
    result = json.loads(raw)
    paths = [item["path"] for item in result["read_first"]]
    assert paths[0] == "docs/m9_widget_plan.md"


def test_filename_anchor_plus_section_terms_returns_section_not_header(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_long_plan_repo(repo)
    raw = tools.prepare_context(
        "docs/m9_widget_plan.md m9.6 list mode view rendering checklist outliner tri-state rollup",
        str(repo), token_budget=1500, max_files=3, output_format="json",
    )
    result = json.loads(raw)
    excerpt = next(e for e in result["context_excerpts"] if e["path"] == "docs/m9_widget_plan.md")
    # Even with an explicit filename anchor (which alone would centre on line 1),
    # the matched section must still win once heading evidence is present.
    assert excerpt["start_line"] > 1
    assert excerpt.get("matched_name", "").startswith("m9.6")


def test_fenced_decoy_heading_does_not_get_matched_as_a_section(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _build_long_plan_repo(repo)
    raw = tools.prepare_context(
        "FAKE HEADING INSIDE A FENCE",
        str(repo), token_budget=1500, max_files=3, output_format="json",
    )
    result = json.loads(raw)
    excerpts = [e for e in result["context_excerpts"] if e["path"] == "docs/m9_widget_plan.md"]
    for excerpt in excerpts:
        assert "FAKE" not in (excerpt.get("matched_name") or "")


def test_section_boundaries_update_after_heading_insert_and_removal(tmp_path, isolated_config):
    from neo_localmcp import repo_memory

    repo = tmp_path / "repo"
    repo.mkdir()
    doc = _build_long_plan_repo(repo)
    repo_memory.index_repo(str(repo), force=True)
    before = repo_memory.symbols_for_files(["docs/m9_widget_plan.md"], str(repo), max_per_file=100)["docs/m9_widget_plan.md"]
    before_m9_6 = next(s for s in before if s["name"].startswith("m9.6"))

    # Insert a new heading partway into the real m9.6 section body, splitting its
    # range. Locate the insertion point via the indexed heading data (not a raw
    # text search) so the fenced decoy heading earlier in the file can't confuse it.
    text = doc.read_text(encoding="utf-8")
    lines = text.splitlines()
    insert_after_line = before_m9_6["start_line"] + 3
    new_lines = lines[:insert_after_line] + ["## m9.6a inserted subsection", "new body"] + lines[insert_after_line:]
    doc.write_text("\n".join(new_lines), encoding="utf-8")
    repo_memory.refresh(str(repo))

    after = repo_memory.symbols_for_files(["docs/m9_widget_plan.md"], str(repo), max_per_file=100)["docs/m9_widget_plan.md"]
    after_names = [s["name"] for s in after]
    assert "m9.6a inserted subsection" in after_names
    after_m9_6 = next(s for s in after if s["name"].startswith("m9.6") and not s["name"].startswith("m9.6a"))
    # The original section now ends earlier because the new heading closes it off.
    assert after_m9_6["end_line"] < before_m9_6["end_line"]

    # Remove the inserted heading again and confirm the boundary reverts.
    doc.write_text(text, encoding="utf-8")
    repo_memory.refresh(str(repo))
    reverted = repo_memory.symbols_for_files(["docs/m9_widget_plan.md"], str(repo), max_per_file=100)["docs/m9_widget_plan.md"]
    reverted_m9_6 = next(s for s in reverted if s["name"].startswith("m9.6"))
    assert reverted_m9_6["end_line"] == before_m9_6["end_line"]


def test_reindexing_unchanged_markdown_file_is_a_cheap_noop(tmp_path, isolated_config):
    from neo_localmcp import repo_memory

    repo = tmp_path / "repo"
    repo.mkdir()
    _build_long_plan_repo(repo)
    first = repo_memory.index_repo(str(repo), force=True)
    assert first["indexed_or_updated"] >= 1
    second = repo_memory.refresh(str(repo))
    assert second["indexed_or_updated"] == 0
    assert second["unchanged"] == first["indexed_files"]


def test_excerpt_centers_on_highest_weight_hint_not_first_by_line(tmp_path, isolated_config):
    """Regression: a file matched at multiple unrelated line numbers must center
    its excerpt on the hint tied to the actually-relevant (highest-weight, e.g.
    strong-term) match, not whichever hint happens to sort first by line number.
    The strong term here is deliberately plain text inside another function
    (not itself a def/class symbol name), matching the real live repro where
    'num_predict' was a parameter mention inside ollama_client.py's chat(), not
    a symbol in its own right -- so the pre-existing exact-symbol-name shortcut
    does not apply and the fallback hint-centering logic is actually exercised.
    See PROJECT_NOTES.md 2026-07-03."""
    repo = tmp_path / "repo"
    repo.mkdir()
    lines = ["def entrypoint():", "    # worker text here", "    return 'ok'"]
    lines += ["    # filler"] * 100
    lines += ["def handler():", "    # marks RareMarkerNeedle usage here", "    return 1"]
    (repo / "module_a.py").write_text("\n".join(lines) + "\n", encoding="utf-8")

    raw = tools.prepare_context("worker RareMarkerNeedle", str(repo), token_budget=1500, max_files=1, output_format="json")
    result = json.loads(raw)
    excerpt = next(e for e in result["context_excerpts"] if e["path"] == "module_a.py")
    target_line = next(i for i, line in enumerate(lines, start=1) if "RareMarkerNeedle" in line)
    assert excerpt["start_line"] <= target_line <= excerpt["end_line"]
