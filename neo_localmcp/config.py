from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

from .identity import IDENTITY

APP_DIR = Path(os.environ.get("NEO_LOCALMCP_HOME", Path.home() / ".neo-localmcp")).expanduser()
CONFIG_PATH = Path(os.environ.get("NEO_LOCALMCP_CONFIG", APP_DIR / "config.yaml")).expanduser()

TEXT_EXTENSIONS = [
    ".cs", ".xaml", ".csproj", ".sln", ".json", ".xml", ".md", ".txt", ".props", ".targets",
    ".config", ".yml", ".yaml", ".toml", ".ini", ".env", ".py", ".ps1", ".bat", ".cmd", ".sh",
    ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".html", ".css", ".scss", ".sql",
    ".go", ".rs", ".java", ".kt", ".kts", ".swift", ".rb", ".php", ".dockerfile", "Dockerfile",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "identity": IDENTITY.as_dict(),
    "ollama": {
        "base_url": "http://127.0.0.1:11434",
        "summary_model": "qwen3-coder:30b",
        "fast_model": "qwen3:8b",
        "timeout_seconds": 200,
        "temperature": 0.1,
        "num_ctx": 32768,
        "keep_alive": "30m",
    },
    "repo": {
        "default_root": "auto",
        "max_files": 500,
        "max_file_bytes": 750_000,
        "summary_max_chars": 80_000,
        "exclude_dirs": [
            ".git", ".hg", ".svn", ".vs", ".vscode", ".idea", "bin", "obj", "node_modules",
            ".venv", "venv", "dist", "build", "packages", ".nuget", "TestResults", "coverage",
            ".next", ".svelte-kit", ".turbo", "target", "out", "DerivedData", ".gradle",
            ".neo-localmcp",
        ],
        "include_extensions": TEXT_EXTENSIONS,
    },
    "memory": {
        "db_path": str(APP_DIR / "repo-context.sqlite"),
    },
    "setup": {
        "install_slash_commands": True,
    },
}


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_config() -> Path:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    if not CONFIG_PATH.exists():
        CONFIG_PATH.write_text(json.dumps(DEFAULT_CONFIG, indent=2), encoding="utf-8")
    return CONFIG_PATH


def load_config() -> dict[str, Any]:
    ensure_config()
    raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8")) if CONFIG_PATH.exists() else {}
    cfg = deep_merge(DEFAULT_CONFIG, raw or {})
    # V4.2.5 migration: bump the previous default Ollama timeout from 180s to 200s.
    # Preserve explicit custom values other than the old default.
    ollama_cfg = cfg.setdefault("ollama", {})
    if int(ollama_cfg.get("timeout_seconds", 200) or 200) == 180:
        ollama_cfg["timeout_seconds"] = 200
    cfg["identity"] = IDENTITY.as_dict()
    return cfg


def save_config(config: dict[str, Any]) -> None:
    APP_DIR.mkdir(parents=True, exist_ok=True)
    config["identity"] = IDENTITY.as_dict()
    CONFIG_PATH.write_text(json.dumps(config, indent=2), encoding="utf-8")


def db_path() -> Path:
    return Path(load_config().get("memory", {}).get("db_path") or APP_DIR / "repo-context.sqlite").expanduser()


def ollama_base_url() -> str:
    return str(load_config().get("ollama", {}).get("base_url", "http://127.0.0.1:11434")).rstrip("/")
