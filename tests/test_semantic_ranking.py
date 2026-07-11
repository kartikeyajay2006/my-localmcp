from __future__ import annotations

import json

import pytest

from neo_localmcp import config, ollama_client
from neo_localmcp.installer import configure_models
from neo_localmcp.retrieval import repo_memory
from neo_localmcp.mcp import memory

pytestmark = pytest.mark.retrieval


# --- config -----------------------------------------------------------------

def test_embed_model_defaults_to_unset(isolated_config):
    cfg = config.load_config()
    # key is present and explicitly None (documented default), not merely absent
    assert "embed_model" in cfg["ollama"]
    assert cfg["ollama"]["embed_model"] is None


# --- ollama_client.embed() bounded + never-blocks ---------------------------

def test_set_ollama_persists_embed_model(isolated_config):
    configure_models(embed_model="nomic-embed-text")
    assert config.load_config()["ollama"]["embed_model"] == "nomic-embed-text"
    # an omitted embed_model on a later call keeps the persisted value (same rule as fast/summary_model)
    configure_models(fast_model="qwen3:8b")
    assert config.load_config()["ollama"]["embed_model"] == "nomic-embed-text"


def test_embed_disabled_when_model_unset_makes_no_network_call(monkeypatch, isolated_config):
    def boom(*args, **kwargs):
        raise AssertionError("embed must not touch the network when embed_model is unset")
    monkeypatch.setattr(ollama_client, "_request_json", boom)
    monkeypatch.setattr(ollama_client, "status", boom)
    result = ollama_client.embed("some task text")
    assert result["ok"] is False
    assert result["state"] == "disabled"


def test_embed_skips_when_ollama_unreachable(monkeypatch, isolated_config):
    monkeypatch.setitem(config.load_config()["ollama"], "embed_model", "nomic-embed-text")
    monkeypatch.setattr(ollama_client, "_cfg", lambda: {"embed_model": "nomic-embed-text", "fast_timeout_seconds": 60})
    monkeypatch.setattr(ollama_client, "status", lambda *a, **k: {"state": "unreachable", "model": "nomic-embed-text", "error": "down"})
    def no_generate(path, **kwargs):
        raise AssertionError("must not POST to embed endpoint when service is unreachable")
    monkeypatch.setattr(ollama_client, "_request_json", no_generate)
    result = ollama_client.embed("task", model="nomic-embed-text")
    assert result["ok"] is False


def test_embed_returns_vector_when_ready(monkeypatch, isolated_config):
    monkeypatch.setattr(ollama_client, "_cfg", lambda: {"embed_model": "nomic-embed-text", "fast_timeout_seconds": 60, "keep_alive": "30m"})
    monkeypatch.setattr(ollama_client, "status", lambda *a, **k: {"state": "ready", "model": "nomic-embed-text"})
    def fake(path, method="GET", body=None, timeout=None):
        assert path == "/api/embed"
        assert body["model"] == "nomic-embed-text"
        return 200, {"embeddings": [[0.1, 0.2, 0.3]]}
    monkeypatch.setattr(ollama_client, "_request_json", fake)
    result = ollama_client.embed("task", model="nomic-embed-text")
    assert result["ok"] is True
    assert result["vector"] == [0.1, 0.2, 0.3]


def test_embed_never_raises_on_timeout(monkeypatch, isolated_config):
    import socket
    monkeypatch.setattr(ollama_client, "_cfg", lambda: {"embed_model": "nomic-embed-text", "fast_timeout_seconds": 60, "keep_alive": "30m"})
    monkeypatch.setattr(ollama_client, "status", lambda *a, **k: {"state": "ready", "model": "nomic-embed-text"})
    monkeypatch.setattr(ollama_client, "_request_json", lambda *a, **k: (_ for _ in ()).throw(socket.timeout("slow")))
    result = ollama_client.embed("task", model="nomic-embed-text")
    assert result["ok"] is False
    assert result.get("timed_out") is True


# --- repo_memory storage + lazy generation ----------------------------------

def _set_embed_model(model="fake-embed"):
    # persist to the on-disk config the same way set-ollama does (load_config rebuilds from disk each call)
    cfg = config.load_config()
    cfg["ollama"]["embed_model"] = model
    config.save_config(cfg)


def _enable_embed_model(monkeypatch, vector_for=None):
    # configure a fake embed model + deterministic embed() so tests never touch a real Ollama
    _set_embed_model()

    def fake_embed(text, model=None):
        vec = vector_for(text) if vector_for else [float(len(text) % 7), 1.0, 2.0]
        return {"ok": True, "vector": vec, "model": "fake-embed"}
    monkeypatch.setattr(ollama_client, "embed", fake_embed)


def _write_repo(tmp_path, name="repo", files=None):
    repo = tmp_path / name
    repo.mkdir()
    for fname, body in (files or {"service.py": "def load_model():\n    return True\n"}).items():
        (repo / fname).write_text(body, encoding="utf-8")
    return repo


def test_stored_vector_roundtrips(tmp_path, isolated_config):
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    conn = repo_memory.connect()
    rid = repo_memory.upsert_repo(conn, repo)
    repo_memory.store_file_embedding(conn, rid, "service.py", "fake-embed", "hash123", [0.5, -0.25, 1.5])
    conn.commit()
    got = repo_memory.get_file_embeddings(conn, rid, ["service.py"])
    assert "service.py" in got
    assert got["service.py"]["content_hash"] == "hash123"
    assert got["service.py"]["vector"] == pytest.approx([0.5, -0.25, 1.5])


def test_index_generates_embeddings_when_configured(tmp_path, isolated_config, monkeypatch):
    _enable_embed_model(monkeypatch)
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    conn = repo_memory.connect()
    rid = repo_memory.upsert_repo(conn, repo)
    assert repo_memory.repo_has_embeddings(conn, rid) is True
    got = repo_memory.get_file_embeddings(conn, rid, ["service.py"])
    assert got["service.py"]["vector"]
    # embedding content_hash matches the file's stored sha256 (so a content change invalidates it)
    file_hash = conn.execute("SELECT sha256 FROM files WHERE repo_id=? AND path=?", (rid, "service.py")).fetchone()["sha256"]
    assert got["service.py"]["content_hash"] == file_hash


def test_index_writes_no_embeddings_when_model_unset(tmp_path, isolated_config, monkeypatch):
    monkeypatch.setattr(ollama_client, "embed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("embed must not be called when embed_model unset")))
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    conn = repo_memory.connect()
    rid = repo_memory.upsert_repo(conn, repo)
    assert repo_memory.repo_has_embeddings(conn, rid) is False


def test_index_completes_when_ollama_unavailable(tmp_path, isolated_config, monkeypatch):
    _set_embed_model()
    monkeypatch.setattr(ollama_client, "embed", lambda *a, **k: {"ok": False, "state": "unreachable", "model": "fake-embed"})
    repo = _write_repo(tmp_path)
    result = repo_memory.index_repo(repo)
    assert result["ok"] is True  # indexing never blocked or errored on a down Ollama
    conn = repo_memory.connect()
    rid = repo_memory.upsert_repo(conn, repo)
    assert repo_memory.repo_has_embeddings(conn, rid) is False


def test_embedding_regenerates_on_content_change(tmp_path, isolated_config, monkeypatch):
    _enable_embed_model(monkeypatch)
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    conn = repo_memory.connect()
    rid = repo_memory.upsert_repo(conn, repo)
    first_hash = repo_memory.get_file_embeddings(conn, rid, ["service.py"])["service.py"]["content_hash"]
    (repo / "service.py").write_text("def load_model():\n    return False  # changed\n", encoding="utf-8")
    repo_memory.index_repo(repo)
    conn = repo_memory.connect()
    second = repo_memory.get_file_embeddings(conn, rid, ["service.py"])["service.py"]
    file_hash = conn.execute("SELECT sha256 FROM files WHERE repo_id=? AND path=?", (rid, "service.py")).fetchone()["sha256"]
    assert second["content_hash"] == file_hash
    assert second["content_hash"] != first_hash


# --- ranking blend in context_prepare ---------------------------------------

_TWO_FILES = {
    "alpha.py": "# alpha\ndef load_model():\n    return handle_service_request()\n",
    "beta.py": "# beta\ndef load_model():\n    return handle_service_request()\n",
}


def _vector_by_marker(text):
    # query and beta -> same direction (cos 1); alpha -> orthogonal (cos 0)
    if "beta" in text:
        return [1.0, 0.0, 0.0]
    if "alpha" in text:
        return [0.0, 1.0, 0.0]
    return [1.0, 0.0, 0.0]  # the task/query string


def test_no_semantic_rerank_when_embed_model_unset_is_byte_identical(tmp_path, isolated_config, monkeypatch):
    """The core 12b invariant: with embed_model unset (no embeddings in the
    repo), output must be identical AND embed() must never be called."""
    repo = _write_repo(tmp_path, files=_TWO_FILES)
    repo_memory.index_repo(repo)
    baseline = memory.prepare_context("load_model handle_service_request", str(repo), token_budget=800, max_files=4, output_format="json")
    # now prove the semantic path is a strict no-op: embed() must never fire
    monkeypatch.setattr(ollama_client, "embed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("embed must not be called when no embeddings exist")))
    again = memory.prepare_context("load_model handle_service_request", str(repo), token_budget=800, max_files=4, output_format="json")
    # retrieval_id differs per call; compare everything else
    a, b = json.loads(baseline), json.loads(again)
    a.pop("retrieval_id"), b.pop("retrieval_id")
    assert a["read_first"] == b["read_first"]
    assert a["candidate_files"] == b["candidate_files"]


def test_semantic_rerank_promotes_closer_candidate(tmp_path, isolated_config, monkeypatch):
    _enable_embed_model(monkeypatch, vector_for=_vector_by_marker)
    repo = _write_repo(tmp_path, files=_TWO_FILES)
    repo_memory.index_repo(repo)  # generates per-file embeddings via the fake embed
    raw = memory.prepare_context("load_model handle_service_request", str(repo), token_budget=800, max_files=4, output_format="json")
    result = json.loads(raw)
    paths = [c["path"] for c in result["candidate_files"]]
    assert "beta.py" in paths and "alpha.py" in paths
    # beta's stored vector matches the query direction (cos 1), alpha is orthogonal (cos 0) -> beta ranks first
    assert paths.index("beta.py") < paths.index("alpha.py")


def test_semantic_rerank_falls_back_when_task_embed_unavailable(tmp_path, isolated_config, monkeypatch):
    """Embeddings exist in the repo, but embedding the task string fails at
    query time (Ollama went down) -> no crash, FTS order stands."""
    _enable_embed_model(monkeypatch, vector_for=_vector_by_marker)
    repo = _write_repo(tmp_path, files=_TWO_FILES)
    repo_memory.index_repo(repo)
    monkeypatch.setattr(ollama_client, "embed", lambda *a, **k: {"ok": False, "state": "unreachable"})
    raw = memory.prepare_context("load_model handle_service_request", str(repo), token_budget=800, max_files=4, output_format="json")
    result = json.loads(raw)
    assert result["read_first"]  # still returns deterministic FTS results


# --- 12c: semantic matching for retrieval-boost memory ----------------------

def _seed_boost_row(conn, rid, term_key, path, shown, followed, corrected, heading_name=""):
    conn.execute(
        "INSERT INTO retrieval_boost(repo_id, term_key, path, heading_name, shown_count, followed_count, corrected_count, last_updated_at) VALUES(?,?,?,?,?,?,?,?)",
        (rid, term_key, path, heading_name, shown, followed, corrected, repo_memory.now_iso()),
    )


def _seed_task_query_with_vector(conn, rid, term_key, vector, query="task"):
    conn.execute(
        "INSERT INTO task_queries(repo_id, query, result_json, retrieval_id, term_key, embed_model, query_vector, created_at) VALUES(?,?,?,?,?,?,?,?)",
        (rid, query, "{}", "rid-" + term_key, term_key, "fake-embed", repo_memory._vector_to_blob(vector), repo_memory.now_iso()),
    )


def test_record_task_query_stores_embedding_when_configured(tmp_path, isolated_config, monkeypatch):
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)  # embed_model unset here: indexing itself must not touch embed()
    _set_embed_model()
    monkeypatch.setattr(ollama_client, "embed", lambda text, model=None: {"ok": True, "vector": [1.0, 0.0, 0.0], "model": "fake-embed"})
    conn = repo_memory.connect(); rid = repo_memory.upsert_repo(conn, repo)
    repo_memory.record_task_query("fix login bug", repo, retrieval_id="r1", term_key="bug|fix|login", sections=[], tool_version="x")
    row = conn.execute("SELECT embed_model, query_vector FROM task_queries WHERE repo_id=? AND retrieval_id=?", (rid, "r1")).fetchone()
    assert row["embed_model"] == "fake-embed"
    assert repo_memory._blob_to_vector(row["query_vector"]) == pytest.approx([1.0, 0.0, 0.0])


def test_record_task_query_stores_no_embedding_when_model_unset(tmp_path, isolated_config, monkeypatch):
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    monkeypatch.setattr(ollama_client, "embed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("embed must not be called when embed_model unset")))
    conn = repo_memory.connect(); rid = repo_memory.upsert_repo(conn, repo)
    repo_memory.record_task_query("fix login bug", repo, retrieval_id="r2", term_key="bug|fix|login", sections=[], tool_version="x")
    row = conn.execute("SELECT embed_model, query_vector FROM task_queries WHERE repo_id=? AND retrieval_id=?", (rid, "r2")).fetchone()
    assert row["embed_model"] is None
    assert row["query_vector"] is None


def test_record_task_query_skips_embedding_gracefully_when_ollama_down(tmp_path, isolated_config, monkeypatch):
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    _set_embed_model()
    monkeypatch.setattr(ollama_client, "embed", lambda *a, **k: {"ok": False, "state": "unreachable"})
    conn = repo_memory.connect(); rid = repo_memory.upsert_repo(conn, repo)
    repo_memory.record_task_query("fix login bug", repo, retrieval_id="r3", term_key="bug|fix|login", sections=[], tool_version="x")
    row = conn.execute("SELECT query, embed_model, query_vector FROM task_queries WHERE repo_id=? AND retrieval_id=?", (rid, "r3")).fetchone()
    assert row["query"] == "fix login bug"  # the row itself is never blocked by a failed embed
    assert row["embed_model"] is None
    assert row["query_vector"] is None


def test_boost_map_exact_match_never_calls_embed(tmp_path, isolated_config, monkeypatch):
    """Zero-cost first path: when the exact term_key already has boost rows,
    the semantic fallback must not even probe embed()."""
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    _set_embed_model()
    monkeypatch.setattr(ollama_client, "embed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("embed must not be called when an exact term_key match exists")))
    conn = repo_memory.connect(); rid = repo_memory.upsert_repo(conn, repo)
    _seed_boost_row(conn, rid, "bug|fix|login", "service.py", shown=3, followed=3, corrected=0)
    conn.commit()
    boost_map = repo_memory.get_boost_map(repo, "bug|fix|login", ["service.py"], query="fix login bug")
    assert boost_map == {("service.py", ""): 3}


def test_boost_map_semantic_fallback_matches_paraphrased_task(tmp_path, isolated_config, monkeypatch):
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    _set_embed_model()
    conn = repo_memory.connect(); rid = repo_memory.upsert_repo(conn, repo)
    # task A ("fix login bug") built up real boost evidence under its own term_key
    _seed_boost_row(conn, rid, "bug|fix|login", "auth.py", shown=4, followed=4, corrected=0)
    _seed_task_query_with_vector(conn, rid, "bug|fix|login", [1.0, 0.0, 0.0], query="fix login bug")
    conn.commit()
    # task B ("debug auth failure") is a different term_key with no boost rows of its own,
    # but its embedding is a near-exact match to task A's
    monkeypatch.setattr(ollama_client, "embed", lambda text, model=None: {"ok": True, "vector": [0.99, 0.01, 0.0], "model": "fake-embed"})
    boost_map = repo_memory.get_boost_map(repo, "auth|debug|failure", ["auth.py"], query="debug auth failure")
    assert boost_map  # nonzero
    assert 0 < boost_map[("auth.py", "")] < 4  # down-weighted, never full credit


def test_boost_map_semantic_fallback_ignores_unrelated_task(tmp_path, isolated_config, monkeypatch):
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    _set_embed_model()
    conn = repo_memory.connect(); rid = repo_memory.upsert_repo(conn, repo)
    _seed_boost_row(conn, rid, "bug|fix|login", "auth.py", shown=4, followed=4, corrected=0)
    _seed_task_query_with_vector(conn, rid, "bug|fix|login", [1.0, 0.0, 0.0], query="fix login bug")
    conn.commit()
    # task C's embedding is orthogonal (cos=0) -- unrelated, must get nothing
    monkeypatch.setattr(ollama_client, "embed", lambda text, model=None: {"ok": True, "vector": [0.0, 1.0, 0.0], "model": "fake-embed"})
    boost_map = repo_memory.get_boost_map(repo, "billing|charge|invoice", ["auth.py"], query="charge a customer invoice")
    assert boost_map == {}


def test_boost_map_semantic_fallback_disabled_when_embed_model_unset(tmp_path, isolated_config, monkeypatch):
    """Regression: even with a stored cross-term_key embedding sitting in the
    DB (e.g. from a previously-configured session), embed_model unset now
    must leave get_boost_map's fallback fully inert -- zero network, zero result."""
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    conn = repo_memory.connect(); rid = repo_memory.upsert_repo(conn, repo)
    _seed_boost_row(conn, rid, "bug|fix|login", "auth.py", shown=4, followed=4, corrected=0)
    _seed_task_query_with_vector(conn, rid, "bug|fix|login", [1.0, 0.0, 0.0], query="fix login bug")
    conn.commit()
    monkeypatch.setattr(ollama_client, "embed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("embed must not be called when embed_model unset")))
    boost_map = repo_memory.get_boost_map(repo, "auth|debug|failure", ["auth.py"], query="debug auth failure")
    assert boost_map == {}


def test_boost_map_without_query_arg_behaves_exactly_as_before(tmp_path, isolated_config, monkeypatch):
    """Regression: existing callers that never pass `query` (the pre-12c
    signature) must be unaffected -- no crash, no embed() call, exact-match-only."""
    repo = _write_repo(tmp_path)
    repo_memory.index_repo(repo)
    _set_embed_model()
    monkeypatch.setattr(ollama_client, "embed", lambda *a, **k: (_ for _ in ()).throw(AssertionError("embed must not be called without a query")))
    conn = repo_memory.connect(); rid = repo_memory.upsert_repo(conn, repo)
    _seed_boost_row(conn, rid, "bug|fix|login", "auth.py", shown=4, followed=4, corrected=0)
    conn.commit()
    assert repo_memory.get_boost_map(repo, "bug|fix|login", ["auth.py"]) == {("auth.py", ""): 4}
    assert repo_memory.get_boost_map(repo, "auth|debug|failure", ["auth.py"]) == {}


def test_prepare_context_applies_semantic_boost_to_paraphrased_task(tmp_path, isolated_config, monkeypatch):
    """End-to-end: prior sessions confirmed 'm9 widget rollup' points at
    docs/plan.md; a differently-worded but semantically close follow-up task
    should now surface a memory-boost reason too, not start from zero."""
    # every query embeds to the same fixed vector -- isolates the test to "does a
    # different-wording follow-up get matched," not embedding-model realism
    _enable_embed_model(monkeypatch, vector_for=lambda text: [1.0, 0.0, 0.0])
    repo = tmp_path / "repo"; repo.mkdir()
    (repo / "docs").mkdir()
    lines = ["# m9 Widget Plan", "", "## m9.2 beta widget rollup", ""] + ["beta body widget rollup"] * 30
    (repo / "docs" / "plan.md").write_text("\n".join(lines), encoding="utf-8")

    for _ in range(4):
        raw = memory.prepare_context("m9.2 beta widget rollup", str(repo), token_budget=1000, max_files=2, output_format="json")
        result = json.loads(raw)
        excerpt = result["context_excerpts"][0]
        memory.file_excerpts([{"path": excerpt["path"], "start_line": excerpt["start_line"], "end_line": excerpt["end_line"]}], str(repo), retrieval_id=result["retrieval_id"])

    raw2 = memory.prepare_context("please review the widget rollup section", str(repo), token_budget=1000, max_files=2, output_format="json")
    reasons = json.loads(raw2)["read_first"][0]["reasons"]
    assert any("memory boost" in r for r in reasons)
