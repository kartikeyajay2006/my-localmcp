"""Unload Neo-used Ollama models during lifecycle operations.

The shared Ollama daemon is never stopped here (see :mod:`neo_localmcp.installer.processes`
for owned-process shutdown). Unload is scoped to named models via the documented
``POST /api/generate`` ``keep_alive: 0`` contract and degrades to a warning on any
failure rather than blocking install/reinstall/uninstall.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from .. import ollama_client
from ..config import load_config, save_config


@dataclass(frozen=True)
class ModelUnloadResult:
    model: str
    ok: bool
    state: str
    error: str | None = None


def configured_models() -> tuple[str, ...]:
    # distinct fast_model/summary_model names, in stable order
    cfg = load_config().get("ollama", {})
    seen: list[str] = []
    for name in (cfg.get("fast_model"), cfg.get("summary_model")):
        if name and name not in seen:
            seen.append(str(name))
    return tuple(seen)


def configure_models(
    *,
    base_url: str | None = None,
    fast_model: str | None = None,
    summary_model: str | None = None,
    num_ctx: int | None = None,
) -> dict[str, Any]:
    # only given (truthy) fields change; omitted fields keep their current persisted value
    # single place backing all 3 surfaces that let a user change these settings: mcp.ollama.set_ollama, the wizard, and setup.py config-ollama
    cfg = load_config()
    ollama_cfg = cfg.setdefault("ollama", {})
    if base_url:
        ollama_cfg["base_url"] = base_url.rstrip("/")
    if fast_model:
        ollama_cfg["fast_model"] = fast_model
    if summary_model:
        ollama_cfg["summary_model"] = summary_model
    if num_ctx:
        ollama_cfg["num_ctx"] = int(num_ctx)
    save_config(cfg)
    return ollama_cfg


def unload_model(model: str, timeout: float = 5.0) -> ModelUnloadResult:
    # never raises -- failures come back as a non-ok result, not an exception
    result = ollama_client.unload_model(model, timeout=timeout)
    return ModelUnloadResult(
        model=model,
        ok=bool(result.get("ok")),
        state=str(result.get("state") or "failed"),
        error=result.get("error"),
    )


def unload_neo_models(timeout_per_model: float = 5.0) -> tuple[ModelUnloadResult, ...]:
    # every distinct configured model, each bounded by its own timeout
    return tuple(unload_model(name, timeout=timeout_per_model) for name in configured_models())
