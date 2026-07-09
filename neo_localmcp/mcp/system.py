"""System and repository-management MCP tools.

Config and health (``init``, ``status``, ``where``, ``model_status``,
``doctor``) plus the repository index lifecycle (``repo_index``,
``repo_reindex``, ``repo_refresh``, ``repo_lookup``, ``reset_repo``,
``reset_all``). No context ranking or summarization -- those belong to
``memory`` and ``editing``.
"""

from __future__ import annotations

from ..retrieval import repo_memory
from ..config import CONFIG_PATH, ensure_config, load_config
from ..branding import IDENTITY
from ..ollama_client import ping
from ..repo_utils import repo_root_or_cwd
from ._shared import json_out


def init() -> str:
    path = ensure_config()
    return json_out({
        "ok": True,
        "product": IDENTITY.product_name,
        "config_path": str(path),
        "next": [
            "Run client setup once from anywhere: neo-localmcp config clients setup --client all",
            "Then cd into the repo you want analyzed: cd /path/to/your/repo",
            "Index that repo: neo-localmcp index",
            "Ask for context: neo-localmcp context \"debug feature X: KnownSymbol, FileName.cs\"",
        ],
    })


def status(repo_root: str = "auto") -> str:
    return json_out({"product": IDENTITY.as_dict(), "config_path": str(CONFIG_PATH), "repo": repo_memory.status(repo_root), "ollama": ping()})


def where(repo_root: str = "auto") -> str:
    cfg = load_config()
    root = repo_root_or_cwd(repo_root)
    return json_out({
        "product": IDENTITY.product_name,
        "installed_command_hint": "neo-localmcp",
        "config_path": str(CONFIG_PATH),
        "current_repo": str(root),
        "repo_db": str(repo_memory.db_path()),
        "ollama_base_url": cfg.get("ollama", {}).get("base_url"),
        "summary_model": cfg.get("ollama", {}).get("summary_model"),
        "note": "Run index/context from the repo you want analyzed. Client setup (neo-localmcp config clients setup) can be run once from anywhere.",
    })


def model_status() -> str:
    cfg = load_config()
    return json_out({
        "ollama_config": cfg.get("ollama", {}),
        "ollama_ping": ping(),
        "note": "Context is deterministic by default in V1. Use --ollama-rank or use_ollama=true for optional Ollama ranking.",
    })


def doctor(repo_root: str = "auto") -> str:
    from .. import mcp_server_lifecycle as lifecycle
    cfg = load_config()
    checks = {
        "config_exists": CONFIG_PATH.exists(),
        "db_open": True,
        "ollama": ping(),
        "repo": repo_memory.status(repo_root),
        "running_servers": lifecycle.list_servers(prune=True),
        "rules": [
            "neo-localmcp retrieves, indexes, summarizes, ranks, and applies exact approved patches.",
            "neo-localmcp does not generate source code or make engineering decisions.",
            "Claude/Codex reason and create exact patches.",
            "Context lookup is deterministic by default; Ollama ranking is opt-in with --ollama-rank or MCP use_ollama=true.",
            "Run `neo-localmcp --help` for the full, authoritative command inventory.",
        ],
        "config": {"ollama_base_url": cfg.get("ollama", {}).get("base_url"), "summary_model": cfg.get("ollama", {}).get("summary_model"), "db_path": cfg.get("memory", {}).get("db_path")},
    }
    return json_out({"ok": True, **checks})


def repo_index(repo_root: str = "auto", max_files: int | None = None, force: bool = False) -> str:
    return json_out(repo_memory.index_repo(repo_root, max_files=max_files, force=force))


def repo_reindex(repo_root: str = "auto", max_files: int | None = None) -> str:
    return json_out(repo_memory.index_repo(repo_root, max_files=max_files, force=True))


def reset_repo(repo_root: str = "auto") -> str:
    return json_out(repo_memory.reset_repo(repo_root))


def reset_all() -> str:
    return json_out(repo_memory.reset_all())


def repo_refresh(repo_root: str = "auto", max_files: int | None = None, force: bool = False) -> str:
    return json_out(repo_memory.refresh(repo_root, force=force, max_files=max_files))


def repo_lookup(query: str, repo_root: str = "auto", limit: int = 20) -> str:
    return json_out(repo_memory.lookup(query, repo_root, limit=limit))
