"""Curated per-role Ollama model recommendations (#101) -- no hardware inspection.

A small hardcoded table of up to 2 candidates per role ("fast" / "summary" /
"embed"), tagged with whether each is already installed. This is deliberately
hardware-blind: the VRAM/RAM-aware filtering layer on top of this table is
tracked standalone in issue #97 and slots in later without changing this
module's shape or the wizard UI built on it.
"""

from __future__ import annotations

from dataclasses import dataclass

from .. import ollama_client

# Up to 2 candidates per role, best-first. Bare names (no tag) are resolved
# against installed models via ollama_client._resolve_installed_model, the
# same tag-matching logic the rest of the installer already uses.
_ROLE_CANDIDATES: dict[str, tuple[str, ...]] = {
    "fast": ("qwen3.5:4b", "qwen3.5:9b"),
    "summary": ("gemma4:12b", "qwen3:8b"),
    "embed": ("bge-m3:latest", "mxbai-embed-large:latest"),
}


@dataclass(frozen=True)
class RecommendedModel:
    # name is the bare candidate name unless installed, in which case it's the
    # resolved installed tag (e.g. "qwen3:8b" -> "qwen3:8b:latest" if that's what's installed)
    name: str
    installed: bool


def recommend_models(role: str, installed_models: list[str]) -> list[RecommendedModel]:
    # unknown role -> empty list, never raises; known role -> its candidates, each
    # tagged installed/not via the shared tag-resolution helper
    candidates = _ROLE_CANDIDATES.get(role, ())
    recommendations = []
    for candidate in candidates:
        resolved = ollama_client._resolve_installed_model(candidate, installed_models)
        installed = resolved in installed_models
        recommendations.append(RecommendedModel(name=resolved if installed else candidate, installed=installed))
    return recommendations
