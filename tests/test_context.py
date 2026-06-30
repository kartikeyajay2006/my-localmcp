from __future__ import annotations

import json

from neo_localmcp import tools
from neo_localmcp.query import normalize_query


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
