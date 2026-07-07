from __future__ import annotations

import json
from typing import Any


def json_out(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def _ns_to_seconds(ns: Any) -> float | None:
    try:
        if ns is None:
            return None
        return round(float(ns) / 1_000_000_000, 3)
    except Exception:
        return None


def _format_model_timing(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None
    raw = result.get("raw") or {}
    return {
        "ok": result.get("ok"),
        "model": result.get("model"),
        "total_seconds": _ns_to_seconds(raw.get("total_duration")),
        "eval_seconds": _ns_to_seconds(raw.get("eval_duration")),
        "eval_count": raw.get("eval_count"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "timeout_seconds": result.get("timeout_seconds"),
        "timed_out": bool(result.get("timed_out")),
        "near_timeout": bool(result.get("near_timeout")),
        "error": result.get("error"),
    }


def _slim_status_for_nesting(status: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop the verbose installed_models list from a second, nested copy of an Ollama
    status dict. The top-level ollama_status key in the same response keeps the full
    list; embedding it again inside ollama_summary/ollama_ranking is pure duplication."""
    if not status:
        return status
    return {k: v for k, v in status.items() if k != "installed_models"}
