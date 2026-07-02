"""Unload Neo-used Ollama models during lifecycle operations.

The shared Ollama daemon is never stopped here (see :mod:`neo_localmcp.installer.processes`
for owned-process shutdown). Unload is scoped to named models via the documented
``POST /api/generate`` ``keep_alive: 0`` contract and degrades to a warning on any
failure rather than blocking install/reinstall/uninstall.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import ollama_client
from ..config import load_config


@dataclass(frozen=True)
class ModelUnloadResult:
    model: str
    ok: bool
    state: str
    error: str | None = None


def configured_models() -> tuple[str, ...]:
    """Distinct configured fast/summary model names, in stable order."""
    cfg = load_config().get("ollama", {})
    seen: list[str] = []
    for name in (cfg.get("fast_model"), cfg.get("summary_model")):
        if name and name not in seen:
            seen.append(str(name))
    return tuple(seen)


def unload_model(model: str, timeout: float = 5.0) -> ModelUnloadResult:
    """Unload one model. Never raises; failures are reported, not propagated."""
    result = ollama_client.unload_model(model, timeout=timeout)
    return ModelUnloadResult(
        model=model,
        ok=bool(result.get("ok")),
        state=str(result.get("state") or "failed"),
        error=result.get("error"),
    )


def unload_neo_models(timeout_per_model: float = 5.0) -> tuple[ModelUnloadResult, ...]:
    """Unload every distinct configured model, bounded per model."""
    return tuple(unload_model(name, timeout=timeout_per_model) for name in configured_models())
