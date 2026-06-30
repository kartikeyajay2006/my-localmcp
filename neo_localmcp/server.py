from __future__ import annotations

from typing import Optional
import json
import os
import subprocess
import sys

from mcp.server.fastmcp import FastMCP

from . import tools
from .client_setup import client_status
from .identity import IDENTITY

mcp = FastMCP(IDENTITY.mcp_server_name)


def _context_prepare_worker(task: str, repo_root: str, max_files: int, limit: int, use_ollama: bool, model: Optional[str]) -> str:
    """Run context_prepare in a short-lived worker process.

    Claude Code/Desktop can leave long-running MCP stdio server processes wedged if a
    context tool call stalls. V4.2.5 isolates context_prepare in a subprocess, returns
    ultra-small plain text by default, and kills the worker on timeout.
    """
    payload = {
        "task": task,
        "repo_root": repo_root,
        "max_files": int(max_files or 80),
        "limit": int(limit or 5),
        "use_ollama": bool(use_ollama),
        "model": model,
        "output_format": "mcp_text",
    }
    timeout_seconds = 230 if use_ollama else 25
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "neo_localmcp.context_worker"],
            input=json.dumps(payload),
            text=True,
            encoding="utf-8",
            errors="replace",
            capture_output=True,
            timeout=timeout_seconds,
            env=os.environ.copy(),
        )
    except subprocess.TimeoutExpired:
        return (
            "neo-localmcp context_prepare timed out.\n"
            f"version: 0.4.2.5\nrepo_root: {repo_root}\n"
            f"task: {task}\nuse_ollama: {use_ollama}\n"
            "No source code was changed. Try CLI `neo-localmcp context ...` or run `neo-localmcp doctor`."
        )
    if proc.returncode != 0:
        stderr = (proc.stderr or "").strip()[-3000:]
        stdout = (proc.stdout or "").strip()[-3000:]
        return (
            "neo-localmcp context_prepare worker failed.\n"
            f"version: 0.4.2.5\nexit_code: {proc.returncode}\nrepo_root: {repo_root}\n"
            f"task: {task}\nstdout:\n{stdout}\nstderr:\n{stderr}"
        )
    out = proc.stdout or ""
    max_chars = 9000 if use_ollama else 8000
    if len(out) > max_chars:
        out = out[:max_chars] + "\n...[truncated by neo-localmcp MCP safety cap]"
    return out


@mcp.tool()
def status(repo_root: str = "auto") -> str:
    """Fast status: config path, repo context DB counts, stale files, and Ollama reachability."""
    return tools.status(repo_root)


@mcp.tool()
def doctor(repo_root: str = "auto") -> str:
    """Health check and command/tool inventory for neo-localmcp."""
    return tools.doctor(repo_root)




@mcp.tool()
def where(repo_root: str = "auto") -> str:
    """Show install/config paths, current repo root, repo context DB, and configured Ollama defaults."""
    return tools.where(repo_root)


@mcp.tool()
def model_status() -> str:
    """Show configured Ollama models and currently reachable Ollama model list."""
    return tools.model_status()


@mcp.tool()
def clients_status() -> str:
    """Show detected Claude Code, Claude Desktop, Codex CLI, and Codex Desktop config paths and MCP blocks."""
    import json
    return json.dumps(client_status(), indent=2, ensure_ascii=False)


@mcp.tool()
def repo_index(repo_root: str = "auto", max_files: Optional[int] = None, force: bool = False) -> str:
    """Hash-aware index of repository files and symbols. Does not generate or change source code."""
    return tools.repo_index(repo_root, max_files=max_files, force=force)


@mcp.tool()
def repo_refresh(repo_root: str = "auto", max_files: Optional[int] = None, force: bool = False) -> str:
    """Refresh stale repository context entries by file hash."""
    return tools.repo_refresh(repo_root, max_files=max_files, force=force)


@mcp.tool()
def repo_reindex(repo_root: str = "auto", max_files: Optional[int] = None) -> str:
    """Force rebuild repository context with the current indexer version. Use after upgrading neo-localmcp if context output looks stale."""
    return tools.repo_reindex(repo_root, max_files=max_files)


@mcp.tool()
def reset_repo(repo_root: str = "auto") -> str:
    """Delete only the current repo's indexed context from the shared DB. Keeps config and other repos. CLI requires --yes; MCP call is explicit by tool name."""
    return tools.reset_repo(repo_root)


@mcp.tool()
def test_determinism(task: str, repo_root: str = "auto", runs: int = 5, reset_repo_first: bool = False, reindex_first: bool = False) -> str:
    """Run a deterministic/no-Ollama context query multiple times and report whether read order, evidence, and guidance are stable."""
    return tools.test_determinism(task, repo_root, runs=runs, reset_repo_first=reset_repo_first, reindex_first=reindex_first)


@mcp.tool()
def repo_lookup(query: str, repo_root: str = "auto", limit: int = 20) -> str:
    """Search persistent repository context for files and symbols. Prefer context_prepare before broad repo search because it normalizes natural/hybrid tasks and ranks source files by intent."""
    return tools.repo_lookup(query, repo_root, limit)


@mcp.tool()
def file_context(path: str, repo_root: str = "auto", around_line: Optional[int] = None, context_lines: int = 40) -> str:
    """Return cached context and symbols for one file, with optional line excerpt from current source. Use after context_prepare identifies high-value files/line ranges."""
    return tools.file_context(path, repo_root, around_line, context_lines)


@mcp.tool()
def context_prepare(task: str, repo_root: str = "auto", max_files: int = 80, limit: int = 5, use_ollama: bool = False, model: Optional[str] = None) -> str:
    """Use before broad repo search. Fast deterministic/no-Ollama by default. V4.2.5 returns ultra-small plain text through an isolated worker process for Claude/Codex MCP safety. Accepts natural language or hybrid input, e.g. 'debug settings persistence: BackdropMaterial, LoadSettingsAsync, MainViewModel'. Set use_ollama=true only when optional local Ollama reranking is worth the latency. Verify current source before edits. neo-localmcp never generates source code."""
    return _context_prepare_worker(task, repo_root, max_files, limit, use_ollama, model)


@mcp.tool()
def context_prepare_json(task: str, repo_root: str = "auto", max_files: int = 80, limit: int = 5, use_ollama: bool = False, model: Optional[str] = None) -> str:
    """Diagnostic context_prepare variant returning compact JSON. Prefer context_prepare for normal Claude/Codex work."""
    return tools.context_prepare(task, repo_root, max_files=max_files, limit=limit, use_ollama=use_ollama, model=model, output_format="mcp_json")


@mcp.tool()
def summarize_file(path: str, repo_root: str = "auto", model: Optional[str] = None) -> str:
    """Use Ollama to summarize one file into repository working context. Does not write source code."""
    return tools.summarize_file(path, repo_root, model)


@mcp.tool()
def apply_unified_patch(patch_text: str, repo_root: str = "auto", check_only: bool = False) -> str:
    """Apply an exact approved unified diff via git apply. neo-localmcp does not generate the patch."""
    return tools.apply_unified_patch(patch_text, repo_root, check_only=check_only)


@mcp.tool()
def record_change(summary: str, paths: list[str], repo_root: str = "auto") -> str:
    """Record a completed change and re-index the listed paths."""
    return tools.record_change(summary, paths, repo_root)


@mcp.tool()
def set_ollama(base_url: Optional[str] = None, summary_model: Optional[str] = None, fast_model: Optional[str] = None, num_ctx: Optional[int] = None) -> str:
    """Update Ollama URL/model defaults used by summarization/ranking."""
    return tools.set_ollama(base_url, summary_model, fast_model, num_ctx)


def main() -> None:
    mcp.run()


if __name__ == "__main__":
    main()
