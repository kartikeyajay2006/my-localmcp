"""Ollama-facing MCP tools.

A thin MCP/CLI surface over the daemon primitives in ``ollama_client`` and the
lifecycle-scoped model config in ``installer.ollama``: ``set_ollama``,
``ollama_status``, ``ollama_ensure``, and the ``ollama_control`` dispatcher.
It configures and reports readiness; it never owns daemon or config policy.
"""

from __future__ import annotations

from ..installer import configure_models as installer_configure_models
from ..ollama_client import chat, ensure as ensure_ollama, start_service, status as ollama_state, stop_service, unload as unload_ollama, warm as warm_ollama
from ._shared import json_out


def set_ollama(base_url: str | None = None, summary_model: str | None = None, fast_model: str | None = None, num_ctx: int | None = None) -> str:
    # writes model config to disk, then reports live daemon status
    # does NOT invalidate/regenerate existing summaries on a model swap (see CLAUDE.md known gaps, issue #8)
    ollama_cfg = installer_configure_models(
        base_url=base_url, fast_model=fast_model, summary_model=summary_model, num_ctx=num_ctx,
    )
    return json_out({"ok": True, "ollama": ollama_cfg, "status": ollama_state()})


def ollama_status(model: str | None = None, purpose: str = "ranking") -> str:
    # purpose "ranking"/"query" -> fast_model, else -> summary_model
    return json_out(ollama_state(model, purpose))


def ollama_ensure(model: str | None = None, purpose: str = "ranking") -> str:
    # confirms model reachable, auto-starting/warming the daemon if needed
    return json_out(ensure_ollama(model, purpose))


def ollama_control(action: str, model: str | None = None, purpose: str = "ranking") -> str:
    # single dispatcher for all daemon actions: action string -> lambda -> invoke
    # unknown action -> error payload instead of raising, since this is MCP-facing
    actions = {
        "status": lambda: ollama_state(model, purpose),
        "ensure": lambda: ensure_ollama(model, purpose),
        "start": start_service,
        "warm": lambda: warm_ollama(model, purpose),
        "unload": lambda: unload_ollama(model, purpose),
        "stop": stop_service,
        "test": lambda: chat("Reply with exactly: ok", model=model, purpose=purpose),
    }
    if action not in actions:
        return json_out({"ok": False, "error": f"unknown Ollama action: {action}"})
    return json_out(actions[action]())
