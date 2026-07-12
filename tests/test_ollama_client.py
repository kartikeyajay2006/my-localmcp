from __future__ import annotations

import pytest

from neo_localmcp import ollama_client

pytestmark = pytest.mark.ollama


def test_status_distinguishes_cold_model(monkeypatch, isolated_config):
    def fake(path, **kwargs):
        if path == "/api/version":
            return 200, {"version": "1.2.3"}
        if path == "/api/tags":
            return 200, {"models": [{"name": "qwen3:8b"}]}
        if path == "/api/ps":
            return 200, {"models": []}
        raise AssertionError(path)

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    result = ollama_client.status(purpose="ranking")
    assert result["state"] == "model_cold"
    assert result["installed"] is True
    assert result["loaded"] is False


def test_missing_model_never_warms_or_pulls(monkeypatch, isolated_config):
    monkeypatch.setattr(ollama_client, "status", lambda *args, **kwargs: {"state": "model_missing", "model": "missing", "local": True})
    monkeypatch.setattr(ollama_client, "warm", lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("warm should not run")))
    result = ollama_client.ensure("missing")
    assert result["ok"] is False
    assert result["state"] == "model_missing"


def test_ranking_uses_fast_model_and_small_context(monkeypatch, isolated_config):
    captured = {}
    monkeypatch.setattr(ollama_client, "ensure", lambda *args, **kwargs: {"ok": True, "state": "ready"})

    def fake(path, **kwargs):
        captured.update(kwargs.get("body") or {})
        return 200, {"response": "ranked", "eval_count": 1}

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    result = ollama_client.chat("rank this", purpose="ranking")
    assert result["ok"] is True
    assert captured["model"] == "qwen3:8b"
    assert captured["options"]["num_ctx"] == 8192


def test_busy_is_retried_once_then_reported(monkeypatch, isolated_config):
    calls = 0
    monkeypatch.setattr(ollama_client, "ensure", lambda *args, **kwargs: {"ok": True, "state": "ready"})

    def fake(path, **kwargs):
        nonlocal calls
        calls += 1
        return 503, {"error": "server busy"}

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    monkeypatch.setattr(ollama_client.time, "sleep", lambda _: None)
    result = ollama_client.chat("rank this", purpose="ranking")
    assert calls == 2
    assert result["state"] == "busy"


def test_remote_service_is_never_started(monkeypatch, isolated_config):
    monkeypatch.setattr(ollama_client, "ollama_base_url", lambda: "http://remote-host:11434")
    result = ollama_client.start_service()
    assert result["ok"] is False
    assert "remote" in result["error"]


def test_warm_timeout_is_not_reported_as_lock_contention(monkeypatch, isolated_config):
    monkeypatch.setattr(ollama_client, "status", lambda *args, **kwargs: {"state": "model_cold", "model": "qwen3:8b", "local": True})
    monkeypatch.setattr(ollama_client, "_request_json", lambda *args, **kwargs: (_ for _ in ()).throw(TimeoutError("warm timeout")))
    result = ollama_client.warm(purpose="ranking")
    assert result["state"] == "timed_out"
    assert result["action"] == "warm_timed_out"


def test_status_resolves_omitted_latest_tag(monkeypatch, isolated_config):
    def fake(path, **kwargs):
        if path == "/api/version":
            return 200, {"version": "1.2.3"}
        if path == "/api/tags":
            return 200, {"models": [{"name": "qwen3-coder:latest"}]}
        if path == "/api/ps":
            return 200, {"models": []}
        raise AssertionError(path)

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    result = ollama_client.status("qwen3-coder")
    assert result["state"] == "model_cold"
    assert result["model"] == "qwen3-coder:latest"
    assert result["requested_model"] == "qwen3-coder"


def test_model_details_reports_size_capabilities_and_family(monkeypatch, isolated_config):
    def fake(path, **kwargs):
        assert path == "/api/tags"
        return 200, {"models": [
            {"name": "qwen3:8b", "size": 5_200_000_000,
             "capabilities": ["completion", "tools"],
             "details": {"family": "qwen3", "parameter_size": "8.2B"}},
            {"name": "bge-m3:latest", "size": 1_157_672_605,
             "capabilities": ["embedding"],
             "details": {"family": "bert", "parameter_size": "566.70M"}},
        ]}
    monkeypatch.setattr(ollama_client, "_request_json", fake)
    details = ollama_client.model_details()
    assert details["qwen3:8b"]["size"] == 5_200_000_000
    assert details["qwen3:8b"]["capabilities"] == ["completion", "tools"]
    assert details["qwen3:8b"]["family"] == "qwen3"
    assert details["qwen3:8b"]["parameter_size"] == "8.2B"
    assert details["bge-m3:latest"]["capabilities"] == ["embedding"]


def test_model_details_never_raises_on_failure(monkeypatch, isolated_config):
    def fake(path, **kwargs):
        raise ConnectionError("down")
    monkeypatch.setattr(ollama_client, "_request_json", fake)
    assert ollama_client.model_details() == {}


def test_model_details_never_raises_on_non_200(monkeypatch, isolated_config):
    monkeypatch.setattr(ollama_client, "_request_json", lambda path, **kw: (500, {"error": "boom"}))
    assert ollama_client.model_details() == {}
