from __future__ import annotations

import copy
import json
import os
from pathlib import Path
from typing import Any

from .branding import IDENTITY

APP_DIR = Path(os.environ.get("NEO_LOCALMCP_HOME", Path.home() / ".neo-localmcp")).expanduser()
CONFIG_DIR = APP_DIR / "config"
# on-disk content is JSON despite the .yaml extension (legacy naming, kept for backward compat with existing installs) -- hand edits must use JSON syntax, no comments/YAML constructs
# renaming to .json or adding a YAML parser were both considered and rejected: too much blast radius / a new dependency for a naming-only issue
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
    # explicit env var wins -> else a caller-mutated CONFIG_PATH global -> else the default under config_dir(); re-derived live so env/test overrides don't need patching every call site
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
        # optional semantic-rerank embedding model; None -> disabled (deterministic FTS ranking unchanged). Set via set-ollama to enable.
        "embed_model": None,
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
        # hard cap on a section summary's generation length (summary + keywords combined); bounds worst-case latency if the model ignores the prompt's length instruction
        "section_summary_num_predict": 400,
        "keep_alive": "30m",
        "auto_start_local": True,
    },
    "repo": {
        "default_root": "auto",
        # None -> complete indexing; an explicit cap is reported as incomplete rather than silently appearing healthy
        "max_files": None,
        "max_file_bytes": 750_000,
        "summary_max_chars": 80_000,
        # fnmatch glob against directory name only, not full path; ".venv*"/"venv*" also cover versioned local virtualenvs an exact-name list would miss
        # ".claude" excludes worktree sibling copies of the repo, which would otherwise outrank the real working-tree file for the same reason
        "exclude_dirs": [
            ".git", ".hg", ".svn", ".vs", ".vscode", ".idea", "bin", "obj", "node_modules",
            ".venv*", "venv*", "dist", "build", "packages", ".nuget", "TestResults", "coverage",
            ".next", ".svelte-kit", ".turbo", "target", "out", "DerivedData", ".gradle",
            ".neo-localmcp", ".pytest_cache", ".claude",
        ],
        # user surface for adding excludes -- exclude_dirs above is code-owned and rebuilt from defaults on every load() (won't preserve hand-edits); put custom excludes here instead, unioned on top
        "extra_exclude_dirs": [],
        "include_extensions": TEXT_EXTENSIONS,
    },
    "memory": {
        "db_path": str(DEFAULT_DB_PATH),
        # observational only, doesn't influence ranking by itself; see retrieval_boost_* below for the separate signal that does. Config flag, not a hidden env var
        "record_context_queries": True,
        "task_query_retention": 500,
        # config-overridable surface over repo_memory.py's RETRIEVAL_BOOST_CAP/RETRIEVAL_BOOST_MIN_SHOWN defaults, so they can be calibrated without a code change
        "retrieval_boost_retention_days": 90,
        "retrieval_boost_cap": 8,
        "retrieval_boost_min_shown": 3,
    },
    "setup": {
        "install_slash_commands": True,
    },
}


def _effective_default_config() -> dict[str, Any]:
    # deep copy of DEFAULT_CONFIG with db_path re-derived live, so a test/env APP_DIR override is honored even though DEFAULT_CONFIG was built at import time
    defaults = copy.deepcopy(DEFAULT_CONFIG)
    configured_db = str(defaults.get("memory", {}).get("db_path") or "")
    if configured_db == str(_INITIAL_DEFAULT_DB_PATH):
        defaults["memory"]["db_path"] = str(default_db_path())
    return defaults


def deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    # recursive merge: dict values merge key-by-key, anything else in override replaces base outright
    result = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def ensure_config() -> Path:
    # creates the config file with effective defaults if it doesn't exist yet; never overwrites an existing one
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if not path.exists():
        path.write_text(json.dumps(_effective_default_config(), indent=2), encoding="utf-8")
    return path


def load_config() -> dict[str, Any]:
    # disk config -> deep-merged onto defaults -> legacy timeout key migrated -> exclude_dirs rebuilt code-owned (see below) -> identity refreshed
    path = ensure_config()
    raw = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    cfg = deep_merge(_effective_default_config(), raw or {})
    ollama_cfg = cfg.setdefault("ollama", {})
    legacy_timeout = int(ollama_cfg.get("timeout_seconds", 0) or 0)
    if legacy_timeout:
        ollama_cfg.setdefault("summary_timeout_seconds", 200 if legacy_timeout == 180 else legacy_timeout)
    # exclude_dirs is code-owned: a plain deep_merge would let a stale persisted list win wholesale, so a newly-added default exclude would never reach an install whose config predates it
    # rebuilt from the code default every load, unioned with repo.extra_exclude_dirs, so the guarantee holds regardless of config age
    repo_cfg = cfg.setdefault("repo", {})
    code_owned = list(_effective_default_config()["repo"]["exclude_dirs"])
    extra = repo_cfg.get("extra_exclude_dirs") or []
    repo_cfg["exclude_dirs"] = code_owned + [d for d in extra if d not in code_owned]
    cfg["identity"] = IDENTITY.as_dict()
    return cfg


def save_config(config: dict[str, Any]) -> None:
    # full overwrite (not a merge); identity is always stamped fresh, never taken from the passed-in dict
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    config["identity"] = IDENTITY.as_dict()
    path.write_text(json.dumps(config, indent=2), encoding="utf-8")


def db_path() -> Path:
    return Path(load_config().get("memory", {}).get("db_path") or default_db_path()).expanduser()


def ollama_base_url() -> str:
    return str(load_config().get("ollama", {}).get("base_url", "http://127.0.0.1:11434")).rstrip("/")
