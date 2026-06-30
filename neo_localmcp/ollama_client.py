from __future__ import annotations

import json
import time
import socket
import urllib.error
import urllib.request
from typing import Any

from .config import load_config, ollama_base_url


def ping() -> dict[str, Any]:
    base = ollama_base_url()
    try:
        with urllib.request.urlopen(f"{base}/api/tags", timeout=5) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        models = [m.get("name") for m in payload.get("models", [])]
        return {"ok": True, "base_url": base, "models": models}
    except Exception as exc:
        return {"ok": False, "base_url": base, "error": str(exc)}


def chat(prompt: str, model: str | None = None) -> dict[str, Any]:
    cfg = load_config()
    o = cfg.get("ollama", {})
    base = str(o.get("base_url", "http://127.0.0.1:11434")).rstrip("/")
    chosen_model = model or o.get("summary_model") or o.get("fast_model") or "qwen3:8b"
    body = {
        "model": chosen_model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": float(o.get("temperature", 0.1)),
            "num_ctx": int(o.get("num_ctx", 32768)),
        },
        "keep_alive": str(o.get("keep_alive", "30m")),
    }
    req = urllib.request.Request(f"{base}/api/generate", data=json.dumps(body).encode("utf-8"), headers={"Content-Type": "application/json"})
    timeout_seconds = int(o.get("timeout_seconds", 200))
    started = time.monotonic()
    try:
        with urllib.request.urlopen(req, timeout=timeout_seconds) as response:
            payload = json.loads(response.read().decode("utf-8", errors="replace"))
        elapsed = round(time.monotonic() - started, 3)
        return {
            "ok": True,
            "model": chosen_model,
            "response": payload.get("response", ""),
            "elapsed_seconds": elapsed,
            "timeout_seconds": timeout_seconds,
            "timed_out": False,
            "near_timeout": elapsed >= max(1, timeout_seconds - 10),
            "raw": {k: payload.get(k) for k in ("total_duration", "eval_count", "eval_duration")},
        }
    except urllib.error.HTTPError as exc:
        elapsed = round(time.monotonic() - started, 3)
        return {"ok": False, "model": chosen_model, "error": f"HTTP {exc.code}: {exc.read().decode('utf-8', errors='replace')}", "elapsed_seconds": elapsed, "timeout_seconds": timeout_seconds, "timed_out": False}
    except (TimeoutError, socket.timeout) as exc:
        elapsed = round(time.monotonic() - started, 3)
        return {"ok": False, "model": chosen_model, "error": f"timed out after {timeout_seconds}s", "elapsed_seconds": elapsed, "timeout_seconds": timeout_seconds, "timed_out": True}
    except Exception as exc:
        elapsed = round(time.monotonic() - started, 3)
        text = str(exc)
        timed_out = "timed out" in text.lower() or isinstance(exc, TimeoutError)
        return {"ok": False, "model": chosen_model, "error": text, "elapsed_seconds": elapsed, "timeout_seconds": timeout_seconds, "timed_out": timed_out}
