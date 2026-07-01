from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import subprocess
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from .config import APP_DIR, load_config, ollama_base_url

STATE_PATH = APP_DIR / "ollama-supervisor.json"
LOCK_PATH = APP_DIR / "ollama-supervisor.lock"


class SupervisorLockTimeout(RuntimeError):
    pass


class _SupervisorLock:
    """Small cross-process lock using atomic directory creation."""

    def __init__(self, timeout: float):
        self.timeout = timeout

    def __enter__(self) -> "_SupervisorLock":
        deadline = time.monotonic() + self.timeout
        APP_DIR.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                LOCK_PATH.mkdir()
                (LOCK_PATH / "owner").write_text(f"{os.getpid()}\n{time.time()}\n", encoding="utf-8")
                return self
            except FileExistsError:
                try:
                    if time.time() - LOCK_PATH.stat().st_mtime > max(120, self.timeout * 2):
                        shutil.rmtree(LOCK_PATH, ignore_errors=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise SupervisorLockTimeout("Ollama supervisor lock timed out")
                time.sleep(0.1)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        shutil.rmtree(LOCK_PATH, ignore_errors=True)


def _cfg() -> dict[str, Any]:
    return load_config().get("ollama", {})


def _model_for(purpose: str, override: str | None = None) -> str:
    cfg = _cfg()
    if override:
        return override
    return str(cfg.get("fast_model") if purpose in {"ranking", "query"} else cfg.get("summary_model") or cfg.get("fast_model") or "qwen3:8b")


def _resolve_installed_model(requested: str, installed: list[str]) -> str:
    """Resolve Ollama's conventional omitted ``:latest`` tag without guessing tags."""
    if requested in installed or ":" in requested:
        return requested
    latest = f"{requested}:latest"
    if latest in installed:
        return latest
    matches = [name for name in installed if name.rsplit(":", 1)[0] == requested]
    return matches[0] if len(matches) == 1 else requested


def _is_local(base_url: str) -> bool:
    host = (urlparse(base_url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"}


def _read_state() -> dict[str, Any]:
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(data: dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _request_json(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> tuple[int, dict[str, Any]]:
    base = ollama_base_url()
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data, method=method,
        headers={"Content-Type": "application/json"} if data is not None else {},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout or float(_cfg().get("health_timeout_seconds", 5))) as response:
            raw = response.read().decode("utf-8", errors="replace")
            return int(response.status), json.loads(raw or "{}")
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw or "{}")
        except json.JSONDecodeError:
            payload = {"error": raw or str(exc)}
        return int(exc.code), payload


def status(model: str | None = None, purpose: str = "ranking") -> dict[str, Any]:
    cfg = _cfg()
    base = ollama_base_url()
    chosen = _model_for(purpose, model)
    started = time.monotonic()
    result: dict[str, Any] = {
        "state": "disabled" if not cfg.get("enabled", True) else "unreachable",
        "base_url": base,
        "local": _is_local(base),
        "model": chosen,
        "purpose": purpose,
        "installed": False,
        "loaded": False,
        "action": "status",
    }
    if result["state"] == "disabled":
        return result
    try:
        _, version = _request_json("/api/version", timeout=float(cfg.get("connect_timeout_seconds", 3)))
        code, tags = _request_json("/api/tags", timeout=float(cfg.get("health_timeout_seconds", 5)))
        if code != 200:
            raise RuntimeError(tags.get("error") or f"HTTP {code}")
        ps_code, running = _request_json("/api/ps", timeout=float(cfg.get("health_timeout_seconds", 5)))
        installed_models = [str(item.get("name") or item.get("model") or "") for item in tags.get("models", [])]
        resolved = _resolve_installed_model(chosen, installed_models)
        if resolved != chosen:
            result["requested_model"] = chosen
            chosen = resolved
            result["model"] = chosen
        loaded_models = running.get("models", []) if ps_code == 200 else []
        loaded = next((item for item in loaded_models if chosen in {item.get("name"), item.get("model")}), None)
        result.update({
            "state": "ready" if loaded else ("model_cold" if chosen in installed_models else "model_missing"),
            "version": version.get("version"),
            "installed": chosen in installed_models,
            "loaded": bool(loaded),
            "installed_models": installed_models,
            "loaded_models": [str(item.get("name") or item.get("model") or "") for item in loaded_models],
        })
        if loaded:
            result.update({key: loaded.get(key) for key in ("size", "size_vram", "processor", "expires_at") if loaded.get(key) is not None})
    except (TimeoutError, socket.timeout) as exc:
        result.update({"state": "timed_out", "error": str(exc) or "health check timed out"})
    except Exception as exc:
        result.update({"state": "unreachable", "error": str(exc)})
    result["elapsed_seconds"] = round(time.monotonic() - started, 3)
    return result


def ping() -> dict[str, Any]:
    current = status()
    return {"ok": current.get("state") not in {"disabled", "unreachable", "timed_out", "failed"}, **current}


def start_service() -> dict[str, Any]:
    base = ollama_base_url()
    if not _is_local(base):
        return {"ok": False, "state": "unreachable", "base_url": base, "error": "remote Ollama services cannot be started by neo-localmcp"}
    current = status()
    if current.get("state") not in {"unreachable", "timed_out"}:
        return {"ok": True, **current, "action": "already_running"}
    executable = shutil.which("ollama")
    if not executable:
        return {"ok": False, **current, "error": "ollama executable was not found on PATH", "action": "start_failed"}
    kwargs: dict[str, Any] = {"stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS
    else:
        kwargs["start_new_session"] = True
    proc = subprocess.Popen([executable, "serve"], **kwargs)
    _write_state({"owned_pid": proc.pid, "base_url": base, "started_at": time.time()})
    deadline = time.monotonic() + float(_cfg().get("startup_timeout_seconds", 20))
    while time.monotonic() < deadline:
        time.sleep(0.5)
        current = status()
        if current.get("state") not in {"unreachable", "timed_out"}:
            return {"ok": True, **current, "state": "reachable", "action": "started", "owned_pid": proc.pid}
        if proc.poll() is not None:
            break
    return {"ok": False, **current, "state": "failed", "action": "start_failed", "owned_pid": proc.pid}


def stop_service() -> dict[str, Any]:
    state = _read_state()
    pid = int(state.get("owned_pid") or 0)
    if not pid or state.get("base_url") != ollama_base_url():
        return {"ok": False, "state": "failed", "error": "refusing to stop an Ollama service not started by neo-localmcp"}
    try:
        os.kill(pid, signal.SIGTERM)
        _write_state({})
        return {"ok": True, "state": "disabled", "action": "stopped", "owned_pid": pid}
    except OSError as exc:
        return {"ok": False, "state": "failed", "action": "stop_failed", "error": str(exc), "owned_pid": pid}


def warm(model: str | None = None, purpose: str = "ranking") -> dict[str, Any]:
    chosen = _model_for(purpose, model)
    cfg = _cfg()
    current = status(chosen, purpose)
    if current.get("state") == "ready":
        return {"ok": True, **current, "action": "already_loaded"}
    if current.get("state") != "model_cold":
        return {"ok": False, **current, "action": "warm_skipped"}
    started = time.monotonic()
    try:
        code, payload = _request_json(
            "/api/generate", method="POST",
            body={"model": chosen, "prompt": "", "stream": False, "keep_alive": str(cfg.get("keep_alive", "30m"))},
            timeout=float(cfg.get("warm_timeout_seconds", 90)),
        )
    except (TimeoutError, socket.timeout) as exc:
        return {"ok": False, **current, "state": "timed_out", "action": "warm_timed_out", "error": str(exc) or "model warm-up timed out", "elapsed_seconds": round(time.monotonic() - started, 3)}
    except Exception as exc:
        return {"ok": False, **current, "state": "failed", "action": "warm_failed", "error": str(exc), "elapsed_seconds": round(time.monotonic() - started, 3)}
    if code == 503:
        return {"ok": False, **current, "state": "busy", "action": "warm_deferred", "error": payload.get("error"), "elapsed_seconds": round(time.monotonic() - started, 3)}
    if code != 200:
        return {"ok": False, **current, "state": "failed", "action": "warm_failed", "error": payload.get("error") or f"HTTP {code}"}
    ready = status(chosen, purpose)
    return {"ok": True, **ready, "state": "ready", "action": "warmed", "load_duration": payload.get("load_duration"), "elapsed_seconds": round(time.monotonic() - started, 3)}


def ensure(model: str | None = None, purpose: str = "ranking", auto_start: bool = True) -> dict[str, Any]:
    cfg = _cfg()
    cooldown = float(cfg.get("failure_cooldown_seconds", 30))
    state = _read_state()
    failed_at = float(state.get("failed_at") or 0)
    if failed_at and time.time() - failed_at < cooldown:
        return {"ok": False, "state": "unreachable", "action": "circuit_open", "base_url": ollama_base_url(), "model": _model_for(purpose, model), "retry_after_seconds": round(cooldown - (time.time() - failed_at), 1)}
    APP_DIR.mkdir(parents=True, exist_ok=True)
    try:
        with _SupervisorLock(float(cfg.get("startup_timeout_seconds", 20)) + float(cfg.get("warm_timeout_seconds", 90))):
            current = status(model, purpose)
            if current.get("state") in {"unreachable", "timed_out"} and auto_start and cfg.get("auto_start_local", True) and current.get("local"):
                current = start_service()
                if current.get("ok"):
                    current = status(model, purpose)
            if current.get("state") == "model_cold":
                current = warm(str(current.get("model") or model or ""), purpose)
            ok = current.get("state") == "ready"
            if ok:
                preserved = {key: value for key, value in _read_state().items() if key in {"owned_pid", "base_url", "started_at"}}
                _write_state(preserved)
            elif current.get("state") in {"unreachable", "timed_out", "failed"}:
                existing = _read_state()
                existing["failed_at"] = time.time()
                _write_state(existing)
            return {"ok": ok, **current}
    except SupervisorLockTimeout:
        return {"ok": False, "state": "busy", "action": "ensure_lock_timeout", "base_url": ollama_base_url(), "model": _model_for(purpose, model)}


def unload(model: str | None = None, purpose: str = "ranking") -> dict[str, Any]:
    chosen = _model_for(purpose, model)
    code, payload = _request_json("/api/generate", method="POST", body={"model": chosen, "prompt": "", "stream": False, "keep_alive": 0}, timeout=float(_cfg().get("health_timeout_seconds", 5)))
    return {"ok": code == 200, "state": "model_cold" if code == 200 else "failed", "action": "unloaded" if code == 200 else "unload_failed", "model": chosen, "error": payload.get("error")}


def chat(prompt: str, model: str | None = None, purpose: str = "summary", num_predict: int | None = None) -> dict[str, Any]:
    cfg = _cfg()
    chosen = _model_for(purpose, model)
    readiness = ensure(chosen, purpose)
    chosen = str(readiness.get("model") or chosen)
    timeout_seconds = int(cfg.get("fast_timeout_seconds", 60) if purpose in {"ranking", "query"} else cfg.get("summary_timeout_seconds", 200))
    if not readiness.get("ok"):
        return {"ok": False, "model": chosen, "purpose": purpose, "error": readiness.get("error") or readiness.get("state"), "timed_out": readiness.get("state") == "timed_out", "timeout_seconds": timeout_seconds, "ollama_status": readiness}
    options: dict[str, Any] = {"temperature": float(cfg.get("temperature", 0.1)), "num_ctx": int(cfg.get("fast_num_ctx", 8192) if purpose in {"ranking", "query"} else cfg.get("num_ctx", 32768))}
    if num_predict is not None:
        # Bounds worst-case generation length/latency. Without this a model that
        # ignores a length instruction in the prompt (e.g. "keywords: at most 8")
        # can run to the model's own max output before returning at all.
        options["num_predict"] = int(num_predict)
    body = {
        "model": chosen, "prompt": prompt, "stream": False,
        "options": options,
        "keep_alive": str(cfg.get("keep_alive", "30m")),
    }
    started = time.monotonic()
    try:
        code, payload = _request_json("/api/generate", method="POST", body=body, timeout=timeout_seconds)
        if code == 503:
            time.sleep(1)
            code, payload = _request_json("/api/generate", method="POST", body=body, timeout=timeout_seconds)
        elapsed = round(time.monotonic() - started, 3)
        if code != 200:
            state = "busy" if code == 503 else ("model_missing" if code == 404 else "failed")
            return {"ok": False, "model": chosen, "purpose": purpose, "state": state, "error": payload.get("error") or f"HTTP {code}", "elapsed_seconds": elapsed, "timeout_seconds": timeout_seconds, "timed_out": False, "ollama_status": readiness}
        return {"ok": True, "model": chosen, "purpose": purpose, "response": payload.get("response", ""), "elapsed_seconds": elapsed, "timeout_seconds": timeout_seconds, "timed_out": False, "near_timeout": elapsed >= max(1, timeout_seconds - 10), "raw": {key: payload.get(key) for key in ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration")}, "ollama_status": readiness}
    except (TimeoutError, socket.timeout) as exc:
        elapsed = round(time.monotonic() - started, 3)
        return {"ok": False, "model": chosen, "purpose": purpose, "state": "timed_out", "error": str(exc) or f"timed out after {timeout_seconds}s", "elapsed_seconds": elapsed, "timeout_seconds": timeout_seconds, "timed_out": True, "ollama_status": readiness}
    except Exception as exc:
        elapsed = round(time.monotonic() - started, 3)
        return {"ok": False, "model": chosen, "purpose": purpose, "state": "failed", "error": str(exc), "elapsed_seconds": elapsed, "timeout_seconds": timeout_seconds, "timed_out": False, "ollama_status": readiness}
