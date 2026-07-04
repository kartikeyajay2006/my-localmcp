from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .identity import IDENTITY

APP_DIR = Path(os.environ.get("NEO_LOCALMCP_HOME", Path.home() / ".neo-localmcp")).expanduser()
CONFIG_DIR = APP_DIR / "config"
CONFIG_PATH = Path(os.environ.get("NEO_LOCALMCP_CONFIG", CONFIG_DIR / "config.yaml")).expanduser()
SQLITE_DIR = APP_DIR / "sqlite"
DEFAULT_DB_PATH = SQLITE_DIR / "repo-context.sqlite"
CACHE_DIR = APP_DIR / "cache"
PROCESS_REGISTRY_DIR = CACHE_DIR / "processes"

_INITIAL_CONFIG_PATH = CONFIG_PATH
_INITIAL_DEFAULT_DB_PATH = DEFAULT_DB_PATH


def config_dir() -> Path:
    return APP_DIR / "config"


def config_path() -> Path:
    explicit = os.environ.get("NEO_LOCALMCP_CONFIG")
    if explicit:
        return Path(explicit).expanduser()
    if CONFIG_PATH != _INITIAL_CONFIG_PATH:
        return Path(CONFIG_PATH).expanduser()
    return config_dir() / "config.yaml"


def sqlite_dir() -> Path:
    return APP_DIR / "sqlite"


def default_db_path() -> Path:
    return sqlite_dir() / "repo-context.sqlite"


def cache_dir() -> Path:
    return APP_DIR / "cache"


def cache_path(*parts: str) -> Path:
    return cache_dir().joinpath(*parts)


def process_registry_dir() -> Path:
    return cache_path("processes")

TEXT_EXTENSIONS = [
    ".cs", ".xaml", ".csproj", ".sln", ".json", ".xml", ".md", ".txt", ".props", ".targets",
    ".config", ".yml", ".yaml", ".toml", ".ini", ".env", ".py", ".ps1", ".bat", ".cmd", ".sh",
    ".js", ".jsx", ".ts", ".tsx", ".vue", ".svelte", ".html", ".css", ".scss", ".sql",
    ".go", ".rs", ".java", ".kt", ".kts", ".swift", ".rb", ".php", ".dockerfile", "Dockerfile",
]

DEFAULT_CONFIG: dict[str, Any] = {
    "identity": IDENTITY.as_dict(),
    "ollama": {
        "enabled": True,
        "base_url": "http://127.0.0.1:11434",
        "summary_model": "qwen3-coder:30b",
        "fast_model": "qwen3:8b",
        "connect_timeout_seconds": 3,
        "health_timeout_seconds": 5,
        "startup_timeout_seconds": 20,
        "warm_timeout_seconds": 90,
        "fast_timeout_seconds": 60,
        "summary_timeout_seconds": 200,
        "failure_cooldown_seconds": 30,
        "temperature": 0.1,
        "num_ctx": 32768,
        "fast_num_ctx": 8192,
        # 1.0.7 (P7a): hard cap on a section summary's generation length. Covers both
        # the summary and keywords portions of one response; plenty for "1-2 sentences
        # + up to 8 keywords" while bounding worst-case latency if the model ignores
        # the length instruction in the prompt.
        "section_summary_num_predict": 400,
        "keep_alive": "30m",
        "auto_start_local": True,
    },
    "repo": {
        "default_root": "auto",
        # Null means complete indexing. Callers may still request an explicit cap,
        # which is reported as incomplete rather than silently appearing healthy.
        "max_files": None,
        "max_file_bytes": 750_000,
        "summary_max_chars": 80_000,
        # Glob patterns (fnmatch), matched against the directory name only, not a
        # path. A plain name with no wildcard (".git") still matches only that exact
        # name. ".venv*"/"venv*" additionally cover differently-named or versioned
        # local virtualenvs (e.g. ".venv-phase14", ".venv-nlm-v1.0.10", "venvs") that
        # an exact-name list would silently index as repository source -- see
        # PROJECT_NOTES.md 2026-07-03.
        "exclude_dirs": [
            ".git", ".hg", ".svn", ".vs", ".vscode", ".idea", "bin", "obj", "node_modules",
            ".venv*", "venv*", "dist", "build", "packages", ".nuget", "TestResults", "coverage",
            ".next", ".svelte-kit", ".turbo", "target", "out", "DerivedData", ".gradle",
            ".neo-localmcp",
            # ".claude/worktrees/" holds full sibling copies of the repo for parallel
            # agent sessions -- without this, each duplicate tools.py/repo_memory.py
            # outranks the real working-tree file (issue #28, same class of bug as
            # the .venv* case above).
            ".claude",
        ],
        "include_extensions": TEXT_EXTENSIONS,
    },
    "memory": {
        "db_path": str(DEFAULT_DB_PATH),
        # Phase 3 (1.0.6): query/result metadata recording is observational only and
        # does not influence ranking by itself; see retrieval_boost for the separate,
        # capped signal that does. Off switch lives here, not a hidden env var.
        "record_context_queries": True,
        "task_query_retention": 500,
        # Retrieval-boost tuning surface (1.0.9, P9g). Promoted from hard-coded
        # constants in repo_memory.py (RETRIEVAL_BOOST_CAP / RETRIEVAL_BOOST_MIN_SHOWN,
        # which remain the defaults) so these can be calibrated against real usage
        # without a code change, the same way retention already is. Defaults are
        # unchanged pending real multi-session usage data -- a 2026-07-01 live audit
        # confirmed the mechanism works and is conservative (a boost only appears
        # after the same task is shown >= min_shown times), so there is no
        # evidence-based case to move them yet.
        "retrieval_boost_retention_days": 90,
        "retrieval_boost_cap": 8,
        "retrieval_boost_min_shown": 3,
    },
    "setup": {
        "install_slash_commands": True,
    },
}


def _effective_default_config() -> dict[str, Any]:
    defaults = copy.deepcopy(DEFAULT_CONFIG)
    configured_db = str(defaults.get("memory", {}).get("db_path") or "")
    if configured_db == str(_INITIAL_DEFAULT_DB_PATH):
        defaults["memory"]["db_path"] = str(default_db_path())
    return defaults


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_config() -> Path:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(_effective_default_config(), indent=2), encoding="utf-8")
    return path


def load_config() -> dict[str, Any]:
    path = ensure_config()
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg = deep_merge(_effective_default_config(), raw or {})
    ollama_cfg = cfg.setdefault("ollama", {})
    legacy_timeout = int(ollama_cfg.get("timeout_seconds", 0) or 0)
    if legacy_timeout:
        ollama_cfg.setdefault("summary_timeout_seconds", 200 if legacy_timeout == 180 else legacy_timeout)
    cfg["identity"] = IDENTITY.as_dict()
    return cfg


def save_config(config: dict[str, Any]) -> None:
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config["identity"] = IDENTITY.as_dict()
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def db_path() -> Path:
    return Path(load_config().get("memory", {}).get("db_path") or default_db_path()).expanduser()


def ollama_base_url() -> str:
    return str(load_config().get("ollama", {}).get("base_url", "http://127.0.0.1:11434")).rstrip("/")
