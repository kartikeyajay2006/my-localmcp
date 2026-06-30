from __future__ import annotations

import json

from neo_localmcp import tools


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
