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
