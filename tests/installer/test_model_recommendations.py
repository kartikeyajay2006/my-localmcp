from __future__ import annotations

from neo_localmcp.installer.model_recommendations import recommend_models


def test_installed_candidate_resolves_to_exact_installed_tag():
    recs = recommend_models("summary", ["gemma4:12b", "qwen3:8b"])
    assert recs[0].name == "gemma4:12b"
    assert recs[0].installed is True


def test_installed_candidate_resolves_bare_name_to_installed_tag():
    recs = recommend_models("embed", ["bge-m3:latest"])
    assert recs[0].name == "bge-m3:latest"
    assert recs[0].installed is True


def test_not_installed_candidate_keeps_bare_name():
    recs = recommend_models("fast", [])
    assert recs[0].name == "qwen3.5:4b"
    assert recs[0].installed is False


def test_each_role_returns_at_most_two_recommendations():
    for role in ("fast", "summary", "embed"):
        assert len(recommend_models(role, [])) <= 2


def test_unknown_role_returns_empty_list_not_raise():
    assert recommend_models("nonexistent-role", ["qwen3:8b"]) == []
