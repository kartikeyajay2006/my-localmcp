from __future__ import annotations

from neo_localmcp import ollama_client
from neo_localmcp.installer import ollama


def test_configured_models_are_distinct_and_ordered(isolated_config):
    ollama_client.load_config()  # ensure config materializes before reading
    models = ollama.configured_models()
    assert models == ("qwen3:8b", "qwen3-coder:30b")


def test_configured_models_dedupes_identical_fast_and_summary(monkeypatch, isolated_config):
    from neo_localmcp import config

    monkeypatch.setattr(
        config,
        "DEFAULT_CONFIG",
        {**config.DEFAULT_CONFIG, "ollama": {**config.DEFAULT_CONFIG["ollama"], "summary_model": "qwen3:8b"}},
    )
    models = ollama.configured_models()
    assert models == ("qwen3:8b",)


def test_unload_neo_models_sends_bounded_keep_alive_zero_per_distinct_model(monkeypatch, isolated_config):
    requests: list[dict] = []

    def fake(path, **kwargs):
        assert path == "/api/generate"
        assert kwargs["timeout"] == 5.0
        requests.append(kwargs["body"])
        return 200, {}

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    results = ollama.unload_neo_models(timeout_per_model=5.0)

    assert len(requests) == 2
    for body in requests:
        assert body["keep_alive"] == 0
        assert body["stream"] is False
    assert {r.model for r in results} == {"qwen3:8b", "qwen3-coder:30b"}
    assert all(r.ok and r.state == "model_cold" for r in results)


def test_unload_neo_models_unloads_duplicated_models_once(monkeypatch, isolated_config):
    from neo_localmcp import config

    monkeypatch.setattr(
        config,
        "DEFAULT_CONFIG",
        {**config.DEFAULT_CONFIG, "ollama": {**config.DEFAULT_CONFIG["ollama"], "summary_model": "qwen3:8b"}},
    )
    calls: list[str] = []

    def fake(path, **kwargs):
        calls.append(kwargs["body"]["model"])
        return 200, {}

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    results = ollama.unload_neo_models()

    assert calls == ["qwen3:8b"]
    assert len(results) == 1


def test_unload_model_degrades_on_connection_refused(monkeypatch, isolated_config):
    def fake(path, **kwargs):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    result = ollama.unload_model("qwen3:8b", timeout=5.0)

    assert result.ok is False
    assert result.state == "failed"
    assert "connection refused" in (result.error or "")


def test_unload_model_degrades_on_timeout(monkeypatch, isolated_config):
    def fake(path, **kwargs):
        raise TimeoutError("timed out")

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    result = ollama.unload_model("qwen3:8b", timeout=5.0)

    assert result.ok is False
    assert result.state == "timed_out"


def test_unload_model_degrades_on_missing_model(monkeypatch, isolated_config):
    def fake(path, **kwargs):
        return 404, {"error": "model 'qwen3:8b' not found"}

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    result = ollama.unload_model("qwen3:8b", timeout=5.0)

    assert result.ok is False
    assert result.state == "model_missing"


def test_unload_model_degrades_on_http_error(monkeypatch, isolated_config):
    def fake(path, **kwargs):
        return 500, {"error": "internal error"}

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    result = ollama.unload_model("qwen3:8b", timeout=5.0)

    assert result.ok is False
    assert result.state == "failed"
    assert result.error == "internal error"


def test_unload_neo_models_never_raises_and_returns_promptly_on_full_degradation(monkeypatch, isolated_config):
    def fake(path, **kwargs):
        raise ConnectionRefusedError("connection refused")

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    results = ollama.unload_neo_models(timeout_per_model=5.0)

    assert len(results) == 2
    assert all(not r.ok for r in results)


def test_unload_model_never_stops_the_ollama_process(monkeypatch, isolated_config):
    """No unload code path may call process-termination primitives."""
    import subprocess

    def fail_popen(*args, **kwargs):
        raise AssertionError("unload must never spawn/stop a process")

    monkeypatch.setattr(subprocess, "Popen", fail_popen)

    def fake(path, **kwargs):
        return 200, {}

    monkeypatch.setattr(ollama_client, "_request_json", fake)
    ollama.unload_neo_models()
