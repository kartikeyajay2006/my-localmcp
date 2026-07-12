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

from . import config
from .config import load_config, ollama_base_url


def _ollama_env() -> dict[str, str]:
    # explicit copy of the current process's env, read live (not cached) at spawn
    # time -- passed as Popen(..., env=...) rather than relying on ambient
    # inheritance, since some launch ancestries (e.g. Claude Desktop's extension
    # host) don't reliably pass OLLAMA_MODELS/OLLAMA_HOST through otherwise (#68)
    return dict(os.environ)


def _state_path() -> Path:
    return config.cache_path("ollama", "supervisor.json")


def _lock_path() -> Path:
    return config.cache_path("ollama", "supervisor.lock")


class SupervisorLockTimeout(RuntimeError):
    pass


class _SupervisorLock:
    # cross-process lock via atomic mkdir; a lock dir older than 2x timeout (min 120s) is presumed abandoned and reclaimed
    def __init__(self, timeout: float):
        self.timeout = timeout

    def __enter__(self) -> "_SupervisorLock":
        deadline = time.monotonic() + self.timeout
        lock_path = _lock_path()
        lock_path.parent.mkdir(parents=True, exist_ok=True)
        while True:
            try:
                lock_path.mkdir()
                (lock_path / "owner").write_text(f"{os.getpid()}\n{time.time()}\n", encoding="utf-8")
                return self
            except FileExistsError:
                try:
                    if time.time() - lock_path.stat().st_mtime > max(120, self.timeout * 2):
                        shutil.rmtree(lock_path, ignore_errors=True)
                        continue
                except OSError:
                    pass
                if time.monotonic() >= deadline:
                    raise SupervisorLockTimeout("Ollama supervisor lock timed out")
                time.sleep(0.1)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> None:
        shutil.rmtree(_lock_path(), ignore_errors=True)


def _cfg() -> dict[str, Any]:
    return load_config().get("ollama", {})


def _model_for(purpose: str, override: str | None = None) -> str:
    # override wins; else purpose "ranking"/"query" -> fast_model, "embed" -> embed_model (may be ""), else -> summary_model (fallback fast_model, then a hardcoded default)
    cfg = _cfg()
    if override:
        return override
    if purpose == "embed":
        return str(cfg.get("embed_model") or "")
    return str(cfg.get("fast_model") if purpose in {"ranking", "query"} else cfg.get("summary_model") or cfg.get("fast_model") or "qwen3:8b")


def _resolve_installed_model(requested: str, installed: list[str]) -> str:
    # requested has no tag -> try exact match, then "requested:latest", then a single unambiguous tag match; never guesses among multiple tags
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
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def _write_state(data: dict[str, Any]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2), encoding="utf-8")


def _request_json(
    path: str,
    *,
    method: str = "GET",
    body: dict[str, Any] | None = None,
    timeout: float | None = None,
) -> tuple[int, dict[str, Any]]:
    # thin sync HTTP wrapper over Ollama's API; HTTPError is caught and returned as (status, payload) same as a success, so callers only branch on the status code
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


def model_sizes(timeout: float = 5) -> dict[str, int]:
    # best-effort per-model size in bytes from /api/tags; never raises -- an empty result just means sizes aren't shown
    try:
        code, tags = _request_json("/api/tags", timeout=timeout)
        if code != 200:
            return {}
        sizes: dict[str, int] = {}
        for item in tags.get("models", []):
            name = str(item.get("name") or item.get("model") or "")
            size = item.get("size")
            if name and isinstance(size, (int, float)):
                sizes[name] = int(size)
        return sizes
    except Exception:  # noqa: BLE001 - size display is a nice-to-have, never fatal
        return {}


def model_details(timeout: float = 5) -> dict[str, dict[str, Any]]:
    # best-effort per-model size/capabilities/family from ONE /api/tags call -- capabilities
    # (e.g. "completion" vs "embedding") is Ollama's own reported model type, not a name-based
    # guess, so this is real "what kind of model is this" info at no extra network cost over
    # model_sizes(). Never raises -- an empty result just means details aren't shown.
    try:
        code, tags = _request_json("/api/tags", timeout=timeout)
        if code != 200:
            return {}
        details: dict[str, dict[str, Any]] = {}
        for item in tags.get("models", []):
            name = str(item.get("name") or item.get("model") or "")
            if not name:
                continue
            size = item.get("size")
            info = item.get("details") or {}
            details[name] = {
                "size": int(size) if isinstance(size, (int, float)) else None,
                "capabilities": [str(c) for c in item.get("capabilities") or []],
                "family": str(info.get("family") or ""),
                "parameter_size": str(info.get("parameter_size") or ""),
            }
        return details
    except Exception:  # noqa: BLE001 - detail display is a nice-to-have, never fatal
        return {}


def _elapsed(started: float) -> float:
    return round(time.monotonic() - started, 3)


def _result(ok: bool, base: dict[str, Any] | None = None, **fields: Any) -> dict[str, Any]:
    # {"ok": ok} + optional base status dict + per-call field overrides -- shared payload builder used by every ollama op below
    result: dict[str, Any] = {"ok": ok, **(base or {})}
    result.update(fields)
    return result


def status(model: str | None = None, purpose: str = "ranking") -> dict[str, Any]:
    # disabled in config -> short-circuit; else version+tags+ps calls -> resolve requested model's :latest tag -> state: ready/model_cold/model_missing/unreachable/timed_out
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
    result["elapsed_seconds"] = _elapsed(started)
    return result


def ping() -> dict[str, Any]:
    # cheap reachability check: any state other than disabled/unreachable/timed_out/failed counts as ok
    current = status()
    return _result(current.get("state") not in {"disabled", "unreachable", "timed_out", "failed"}, current)


def start_service() -> dict[str, Any]:
    # remote base_url -> refuse (can't start someone else's service); local + already reachable -> no-op; else spawn `ollama serve` detached and poll until reachable or timeout
    base = ollama_base_url()
    if not _is_local(base):
        return {"ok": False, "state": "unreachable", "base_url": base, "error": "remote Ollama services cannot be started by neo-localmcp"}
    current = status()
    if current.get("state") not in {"unreachable", "timed_out"}:
        return _result(True, current, action="already_running")
    executable = shutil.which("ollama")
    if not executable:
        return _result(False, current, error="ollama executable was not found on PATH", action="start_failed")
    kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL, "stdout": subprocess.DEVNULL, "stderr": subprocess.DEVNULL,
        "env": _ollama_env(),
    }
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
            return _result(True, current, state="reachable", action="started", owned_pid=proc.pid)
        if proc.poll() is not None:
            break
    return _result(False, current, state="failed", action="start_failed", owned_pid=proc.pid)


def stop_service() -> dict[str, Any]:
    # ownership check: only stops a process this module itself started (recorded owned_pid + matching base_url), never someone else's Ollama
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
    # already ready -> no-op; not model_cold (e.g. missing/unreachable) -> skip, nothing to warm; else -> empty-prompt generate call to force the model into memory
    chosen = _model_for(purpose, model)
    cfg = _cfg()
    current = status(chosen, purpose)
    if current.get("state") == "ready":
        return _result(True, current, action="already_loaded")
    if current.get("state") != "model_cold":
        return _result(False, current, action="warm_skipped")
    started = time.monotonic()
    try:
        code, payload = _request_json(
            "/api/generate", method="POST",
            body={"model": chosen, "prompt": "", "stream": False, "keep_alive": str(cfg.get("keep_alive", "30m"))},
            timeout=float(cfg.get("warm_timeout_seconds", 90)),
        )
    except (TimeoutError, socket.timeout) as exc:
        return _result(False, current, state="timed_out", action="warm_timed_out", error=str(exc) or "model warm-up timed out", elapsed_seconds=_elapsed(started))
    except Exception as exc:
        return _result(False, current, state="failed", action="warm_failed", error=str(exc), elapsed_seconds=_elapsed(started))
    if code == 503:
        return _result(False, current, state="busy", action="warm_deferred", error=payload.get("error"), elapsed_seconds=_elapsed(started))
    if code != 200:
        return _result(False, current, state="failed", action="warm_failed", error=payload.get("error") or f"HTTP {code}")
    ready = status(chosen, purpose)
    return _result(True, ready, state="ready", action="warmed", load_duration=payload.get("load_duration"), elapsed_seconds=_elapsed(started))


def ensure(model: str | None = None, purpose: str = "ranking", auto_start: bool = True) -> dict[str, Any]:
    # recent failure -> circuit breaker open, refuse fast instead of retrying; else under lock: status -> auto-start if unreachable -> warm if cold -> record ready/failed state for next call's circuit check
    cfg = _cfg()
    cooldown = float(cfg.get("failure_cooldown_seconds", 30))
    state = _read_state()
    failed_at = float(state.get("failed_at") or 0)
    if failed_at and time.time() - failed_at < cooldown:
        return {"ok": False, "state": "unreachable", "action": "circuit_open", "base_url": ollama_base_url(), "model": _model_for(purpose, model), "retry_after_seconds": round(cooldown - (time.time() - failed_at), 1)}
    config.cache_dir().mkdir(parents=True, exist_ok=True)
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
            return _result(ok, current)
    except SupervisorLockTimeout:
        return {"ok": False, "state": "busy", "action": "ensure_lock_timeout", "base_url": ollama_base_url(), "model": _model_for(purpose, model)}


def unload_model(model: str, timeout: float | None = None) -> dict[str, Any]:
    # keep_alive: 0 -> Ollama drops the model from memory; never raises, so reinstall/uninstall lifecycle can treat this as best-effort and never block on it
    bounded = float(timeout) if timeout is not None else float(_cfg().get("health_timeout_seconds", 5))
    started = time.monotonic()
    try:
        code, payload = _request_json("/api/generate", method="POST", body={"model": model, "prompt": "", "stream": False, "keep_alive": 0}, timeout=bounded)
    except (TimeoutError, socket.timeout) as exc:
        return _result(False, model=model, state="timed_out", action="unload_timed_out", error=str(exc) or "unload timed out", elapsed_seconds=_elapsed(started))
    except Exception as exc:
        return _result(False, model=model, state="failed", action="unload_failed", error=str(exc), elapsed_seconds=_elapsed(started))
    elapsed = _elapsed(started)
    if code == 404:
        return _result(False, model=model, state="model_missing", action="unload_skipped", error=payload.get("error") or "model not found", elapsed_seconds=elapsed)
    if code != 200:
        return _result(False, model=model, state="failed", action="unload_failed", error=payload.get("error") or f"HTTP {code}", elapsed_seconds=elapsed)
    return _result(True, model=model, state="model_cold", action="unloaded", elapsed_seconds=elapsed)


def unload(model: str | None = None, purpose: str = "ranking") -> dict[str, Any]:
    # purpose -> resolved model name -> unload_model
    chosen = _model_for(purpose, model)
    return unload_model(chosen)


def chat(prompt: str, model: str | None = None, purpose: str = "summary", num_predict: int | None = None) -> dict[str, Any]:
    # ensure model ready -> not ready -> fail fast with readiness's own error/state; else -> /api/generate, retrying once on a 503
    cfg = _cfg()
    chosen = _model_for(purpose, model)
    readiness = ensure(chosen, purpose)
    chosen = str(readiness.get("model") or chosen)
    timeout_seconds = int(cfg.get("fast_timeout_seconds", 60) if purpose in {"ranking", "query"} else cfg.get("summary_timeout_seconds", 200))
    if not readiness.get("ok"):
        return _result(False, model=chosen, purpose=purpose, error=readiness.get("error") or readiness.get("state"), timed_out=readiness.get("state") == "timed_out", timeout_seconds=timeout_seconds, ollama_status=readiness)
    options: dict[str, Any] = {"temperature": float(cfg.get("temperature", 0.1)), "num_ctx": int(cfg.get("fast_num_ctx", 8192) if purpose in {"ranking", "query"} else cfg.get("num_ctx", 32768))}
    if num_predict is not None:
        # bounds worst-case generation length/latency if the model ignores a prompt length instruction (e.g. "at most 8 keywords")
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
        elapsed = _elapsed(started)
        if code != 200:
            state = "busy" if code == 503 else ("model_missing" if code == 404 else "failed")
            return _result(False, model=chosen, purpose=purpose, state=state, error=payload.get("error") or f"HTTP {code}", elapsed_seconds=elapsed, timeout_seconds=timeout_seconds, timed_out=False, ollama_status=readiness)
        return _result(True, model=chosen, purpose=purpose, response=payload.get("response", ""), elapsed_seconds=elapsed, timeout_seconds=timeout_seconds, timed_out=False, near_timeout=elapsed >= max(1, timeout_seconds - 10), raw={key: payload.get(key) for key in ("total_duration", "load_duration", "prompt_eval_count", "prompt_eval_duration", "eval_count", "eval_duration")}, ollama_status=readiness)
    except (TimeoutError, socket.timeout) as exc:
        elapsed = _elapsed(started)
        return _result(False, model=chosen, purpose=purpose, state="timed_out", error=str(exc) or f"timed out after {timeout_seconds}s", elapsed_seconds=elapsed, timeout_seconds=timeout_seconds, timed_out=True, ollama_status=readiness)
    except Exception as exc:
        elapsed = _elapsed(started)
        return _result(False, model=chosen, purpose=purpose, state="failed", error=str(exc), elapsed_seconds=elapsed, timeout_seconds=timeout_seconds, timed_out=False, ollama_status=readiness)


def embed(text: str, model: str | None = None) -> dict[str, Any]:
    # bounded, non-blocking embedding for the optional semantic-rerank layer.
    # embed_model unset -> disabled, zero network. Service down/timed_out/model-not-installed -> skip (never auto-starts, never pulls, never raises).
    # config unset -> down -> generate: any non-ready gate short-circuits before the embed POST
    cfg = _cfg()
    chosen = _model_for("embed", model)
    if not chosen:
        return _result(False, model=None, purpose="embed", state="disabled", error="embed_model not configured")
    readiness = status(chosen, purpose="embed")  # bounded connect+health probe; no auto-start, no warm
    if readiness.get("state") not in {"ready", "model_cold"}:
        # unreachable / timed_out / model_missing / disabled -> skip this pass
        return _result(False, model=chosen, purpose="embed", state=readiness.get("state"), error=readiness.get("error") or readiness.get("state"), timed_out=readiness.get("state") == "timed_out", ollama_status=readiness)
    timeout_seconds = int(cfg.get("fast_timeout_seconds", 60))
    started = time.monotonic()
    try:
        code, payload = _request_json("/api/embed", method="POST", body={"model": chosen, "input": text, "keep_alive": str(cfg.get("keep_alive", "30m"))}, timeout=timeout_seconds)
        elapsed = _elapsed(started)
        if code != 200:
            state = "busy" if code == 503 else ("model_missing" if code == 404 else "failed")
            return _result(False, model=chosen, purpose="embed", state=state, error=payload.get("error") or f"HTTP {code}", elapsed_seconds=elapsed, timed_out=False)
        # /api/embed returns embeddings: [[...]]; tolerate the older /api/embeddings embedding: [...] shape too
        rows = payload.get("embeddings")
        vector = (rows[0] if rows else None) if isinstance(rows, list) else payload.get("embedding")
        if not vector:
            return _result(False, model=chosen, purpose="embed", state="failed", error="no embedding returned", elapsed_seconds=elapsed, timed_out=False)
        return _result(True, model=chosen, purpose="embed", vector=[float(x) for x in vector], elapsed_seconds=elapsed, timed_out=False)
    except (TimeoutError, socket.timeout) as exc:
        return _result(False, model=chosen, purpose="embed", state="timed_out", error=str(exc) or f"timed out after {timeout_seconds}s", elapsed_seconds=_elapsed(started), timed_out=True)
    except Exception as exc:
        return _result(False, model=chosen, purpose="embed", state="failed", error=str(exc), elapsed_seconds=_elapsed(started), timed_out=False)
