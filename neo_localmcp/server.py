from __future__ import annotations

import functools
import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any, Optional
from urllib.parse import unquote, urlparse
from urllib.request import url2pathname

from mcp.server.fastmcp import Context, FastMCP

from .mcp_commands import editing, memory, ollama, system
from .config import load_config
from .identity import IDENTITY
from .utils import hidden_subprocess_kwargs


def _tool_guard(func):
    """Standardize the error envelope every `@mcp.tool()` needs (#30, 5.1).

    An unhandled exception from a tool call must still return a normal MCP
    string result, not crash the session -- the deterministic tools
    underneath already handle their own domain errors, so this only catches
    the unexpected. `functools.wraps` preserves `func`'s signature via
    `__wrapped__` so FastMCP's schema introspection still sees the real
    parameters, not `(*args, **kwargs)`. Applied under `@mcp.tool()`, not
    over it, so FastMCP registers the guarded coroutine.
    """
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as exc:
            return json.dumps({"ok": False, "error": str(exc)}, indent=2)
    return wrapper

SERVER_INSTRUCTIONS = (
    "Use prepare_context before broad repository search. It returns bounded, current source excerpts; source remains authoritative. "
    "Pass repo_root explicitly when possible. If omitted, neo-localmcp uses MCP workspace roots and refuses ambiguous scope. "
    "Ollama is optional: deterministic context must still be used when Ollama is unavailable, missing, cold, busy, or timed out. "
    "Use file_excerpts only for additional exact ranges and repo_lookup for precise symbols/paths. "
    "Repository status, doctor, refresh, exact-file summarization, and approved patch validation/application have dedicated tools. "
    "apply_patch defaults to check_only=true; set it false only for a developer-approved exact diff. Record verified edits with record_change."
)

mcp = FastMCP(IDENTITY.mcp_server_name, instructions=SERVER_INSTRUCTIONS)


async def _resolve_repo_root(repo_root: str, ctx: Context) -> str:
    if repo_root and repo_root not in {"auto", "."}:
        return str(Path(repo_root).expanduser().resolve())
    env_root = os.environ.get("NEO_LOCALMCP_REPO")
    if env_root:
        return str(Path(env_root).expanduser().resolve())
    configured = load_config().get("repo", {}).get("default_root")
    if configured and configured not in {"auto", "."}:
        return str(Path(str(configured)).expanduser().resolve())
    try:
        result = await ctx.request_context.session.list_roots()
        roots = list(result.roots)
    except Exception:
        roots = []
    file_roots: list[str] = []
    for root in roots:
        parsed = urlparse(str(root.uri))
        if parsed.scheme == "file":
            path = Path(url2pathname(unquote(parsed.path)))
            if parsed.netloc and parsed.netloc not in {"", "localhost"}:
                path = Path(f"//{parsed.netloc}{url2pathname(unquote(parsed.path))}")
            file_roots.append(str(path.resolve()))
    if len(file_roots) == 1:
        return file_roots[0]
    if not file_roots:
        raise ValueError("No repository root was supplied and the MCP client exposed no filesystem root. Pass repo_root explicitly or set NEO_LOCALMCP_REPO.")
    raise ValueError(f"Multiple MCP workspace roots are active: {file_roots}. Pass repo_root explicitly.")


def _context_prepare_worker(task: str, repo_root: str, max_files: int, token_budget: int, use_ollama: bool, model: Optional[str]) -> str:
    # Isolated in a subprocess -- unlike every other tool in this file, which calls
    # tools.* in-process -- because this is the heaviest, Ollama-touching path; a
    # hang or crash here must not take down the stdio server. The worker also
    # enforces UTF-8 I/O (see PYTHONIOENCODING/PYTHONUTF8 below), a subprocess
    # encoding fix from PROJECT_NOTES 1.0.1/1.0.3. Do not "simplify" this back to
    # an in-process call without re-solving both problems first.
    payload = {
        "task": task,
        "repo_root": repo_root,
        "max_files": None,
        "limit": int(max_files or 6),
        "token_budget": int(token_budget or 3000),
        "use_ollama": bool(use_ollama),
        "model": model,
        "output_format": "mcp_text",
    }
    ollama_cfg = load_config().get("ollama", {})
    timeout_seconds = 30
    if use_ollama:
        timeout_seconds = int(ollama_cfg.get("startup_timeout_seconds", 20)) + int(ollama_cfg.get("warm_timeout_seconds", 90)) + int(ollama_cfg.get("fast_timeout_seconds", 60)) + 10
    worker_env = os.environ.copy()
    worker_env["PYTHONIOENCODING"] = "utf-8"
    worker_env["PYTHONUTF8"] = "1"
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "neo_localmcp.context_worker"], input=json.dumps(payload),
            text=True, encoding="utf-8", errors="replace", capture_output=True,
            timeout=timeout_seconds, env=worker_env, stdin=None,
            **hidden_subprocess_kwargs(),
        )
    except subprocess.TimeoutExpired:
        return json.dumps({"ok": False, "error": "context worker timed out", "repo_root": repo_root, "use_ollama": use_ollama, "timeout_seconds": timeout_seconds, "fallback": "Retry with use_ollama=false; deterministic context remains available."}, indent=2)
    if proc.returncode != 0:
        return json.dumps({"ok": False, "error": "context worker failed", "exit_code": proc.returncode, "repo_root": repo_root, "stdout": (proc.stdout or "")[-2000:], "stderr": (proc.stderr or "")[-2000:]}, indent=2)
    out = proc.stdout or ""
    max_chars = min(40_000, max(8_000, int(token_budget) * 4 + 4_000))
    return out if len(out) <= max_chars else out[:max_chars] + "\n...[truncated by MCP safety cap]"


@mcp.tool()
@_tool_guard
async def prepare_context(task: str, ctx: Context, repo_root: str = "auto", token_budget: int = 3000, max_files: int = 6, use_ollama: bool = False, model: Optional[str] = None) -> str:
    """Return bounded current-source excerpts for a task before broad search."""
    root = await _resolve_repo_root(repo_root, ctx)
    return _context_prepare_worker(task, root, max_files, token_budget, use_ollama, model)


@mcp.tool()
async def context_prepare(task: str, ctx: Context, repo_root: str = "auto", token_budget: int = 3000, max_files: int = 6, use_ollama: bool = False, model: Optional[str] = None) -> str:
    """Compatibility alias for prepare_context; retained for one release."""
    return await prepare_context(task, ctx, repo_root, token_budget, max_files, use_ollama, model)


@mcp.tool()
@_tool_guard
async def file_excerpts(ranges: list[dict[str, Any]], ctx: Context, repo_root: str = "auto", max_chars: int = 20_000, retrieval_id: Optional[str] = None) -> str:
    """Read several exact current-source ranges in one bounded response.

    Pass the retrieval_id from a prior prepare_context call to record whether
    the pulled range matched what was suggested; this only feeds a capped,
    observational retrieval-memory signal and never changes what is returned.
    """
    root = await _resolve_repo_root(repo_root, ctx)
    return memory.file_excerpts(ranges, root, max_chars, retrieval_id)


@mcp.tool()
@_tool_guard
async def repo_lookup(query: str, ctx: Context, repo_root: str = "auto", limit: int = 20) -> str:
    """Perform precise persistent lookup for a symbol or path."""
    return system.repo_lookup(query, await _resolve_repo_root(repo_root, ctx), limit)


@mcp.tool()
@_tool_guard
async def record_change(summary: str, paths: list[str], ctx: Context, repo_root: str = "auto") -> str:
    """Record a verified logical change and refresh affected paths."""
    return memory.record_change(summary, paths, await _resolve_repo_root(repo_root, ctx))


@mcp.tool()
@_tool_guard
async def repo_status(ctx: Context, repo_root: str = "auto") -> str:
    """Report repository index, configuration, Git, and Ollama status without mutation."""
    return system.status(await _resolve_repo_root(repo_root, ctx))


@mcp.tool()
@_tool_guard
async def doctor(ctx: Context, repo_root: str = "auto") -> str:
    """Run the full read-only neo-localmcp, repository, configuration, and Ollama health check."""
    return system.doctor(await _resolve_repo_root(repo_root, ctx))


@mcp.tool()
@_tool_guard
async def refresh_index(ctx: Context, repo_root: str = "auto", max_files: Optional[int] = None, force: bool = False) -> str:
    """Refresh changed, stale, or missing files in the persistent repository index."""
    return system.repo_refresh(await _resolve_repo_root(repo_root, ctx), max_files, force)


@mcp.tool()
@_tool_guard
async def summarize_file(path: str, ctx: Context, repo_root: str = "auto", model: Optional[str] = None, heading: Optional[str] = None) -> str:
    """Summarize one exact current file, or one Markdown heading section of it, with the configured Ollama summary model and cache it by source hash."""
    return editing.summarize_file(path, await _resolve_repo_root(repo_root, ctx), model, heading)


@mcp.tool()
@_tool_guard
async def apply_patch(patch_text: str, ctx: Context, repo_root: str = "auto", check_only: bool = True) -> str:
    """Check or apply an exact developer-approved unified diff; defaults to validation without mutation."""
    return editing.apply_unified_patch(patch_text, await _resolve_repo_root(repo_root, ctx), check_only)


@mcp.tool()
def ollama_status(model: Optional[str] = None, purpose: str = "ranking") -> str:
    """Report endpoint, installed/loaded model state, and readiness without mutation."""
    return ollama.ollama_status(model, purpose)


@mcp.tool()
def ollama_ensure(model: Optional[str] = None, purpose: str = "ranking") -> str:
    """Ensure local Ollama and the requested model are ready; remote services are never started."""
    return ollama.ollama_ensure(model, purpose)


def main() -> None:
    # Register this server and start the stop-file watcher before entering the
    # blocking mcp.run() loop, so `neo-localmcp stop` (and the upgrade flow) can
    # ask it to exit gracefully instead of relying on an external force-kill.
    from . import __version__
    from . import lifecycle
    from .config import ensure_config

    ensure_config()
    try:
        lifecycle.register_server(__version__)
        lifecycle.start_stop_watcher()
    except Exception:
        # Never let lifecycle bookkeeping prevent the server from actually serving.
        pass
    try:
        mcp.run()
    finally:
        lifecycle.unregister_server()


if __name__ == "__main__":
    main()
