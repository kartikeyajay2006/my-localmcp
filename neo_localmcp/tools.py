from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from . import repo_memory
from .config import CONFIG_PATH, ensure_config, load_config, save_config
from .identity import IDENTITY
from .ollama_client import chat, ping
from .query import category_boost, classify_path, extract_file_references, normalize_query
from .utils import read_text_file, rel, repo_root_or_cwd, rg_search, safe_path, run_command


LINE_HINT_MAX_PER_FILE = 5
READ_FIRST_MAX = 5
CONTEXT_QUERY_RECORD_ENV = "NEO_LOCALMCP_RECORD_CONTEXT_QUERIES"


def json_out(data: Any) -> str:
    return json.dumps(data, indent=2, ensure_ascii=False, default=str)


def _ns_to_seconds(ns: Any) -> float | None:
    try:
        if ns is None:
            return None
        return round(float(ns) / 1_000_000_000, 3)
    except Exception:
        return None


def _format_model_timing(result: dict[str, Any] | None) -> dict[str, Any] | None:
    if not result:
        return None
    raw = result.get("raw") or {}
    return {
        "ok": result.get("ok"),
        "model": result.get("model"),
        "total_seconds": _ns_to_seconds(raw.get("total_duration")),
        "eval_seconds": _ns_to_seconds(raw.get("eval_duration")),
        "eval_count": raw.get("eval_count"),
        "elapsed_seconds": result.get("elapsed_seconds"),
        "timeout_seconds": result.get("timeout_seconds"),
        "timed_out": bool(result.get("timed_out")),
        "near_timeout": bool(result.get("near_timeout")),
        "error": result.get("error"),
    }


def _hint_sort_key(hint: str) -> tuple[int, int, str]:
    m = re.search(r"(\d+)", hint)
    # Direct search/index line hints are rendered as "around line N". Prefer them over generic symbol-list hints
    # like "MainViewModel around line 11" so the agent sees the task-relevant lines first.
    direct_priority = 0 if hint.startswith("around line") or hint.startswith("lines ") else 1
    if m:
        return (direct_priority, int(m.group(1)), hint)
    return (2, 10**9, hint)


def _compact_line_hints(hints: list[str], max_hints: int = LINE_HINT_MAX_PER_FILE) -> list[str]:
    # Stable compacting: one hint per line number. Prefer named hints over generic "around line N".
    best_by_line: dict[int | str, str] = {}
    for hint in sorted(hints, key=_hint_sort_key):
        m = re.search(r"(\d+)", hint)
        key: int | str = int(m.group(1)) if m else hint
        current = best_by_line.get(key)
        if current is None:
            best_by_line[key] = hint
        else:
            current_direct = current.startswith("around line") or current.startswith("lines ")
            hint_direct = hint.startswith("around line") or hint.startswith("lines ")
            # Direct search/index hits should beat generic symbol-list hints for the same line.
            if hint_direct and not current_direct:
                best_by_line[key] = hint
            elif current_direct == hint_direct and len(hint) > len(current):
                best_by_line[key] = hint
    cleaned = sorted(best_by_line.values(), key=_hint_sort_key)
    return cleaned[:max_hints]


def _render_context_text(data: dict[str, Any]) -> str:
    lines: list[str] = []
    interp = data.get("interpreted_query", {})
    repo = data.get("repo_status", {})
    lines.append("neo-localmcp context")
    lines.append(f"Repo: {data.get('repo_root')}")
    lines.append(f"Intent: {interp.get('intent')} | policy: {interp.get('ranking_policy')}")
    lines.append(f"Ollama ranking: {'on' if data.get('ollama_ranking') else 'off'}")
    if interp.get("strong_terms"):
        lines.append("Strong terms: " + ", ".join(interp.get("strong_terms", [])))
    if interp.get("weak_terms"):
        lines.append("Weak terms: " + ", ".join(interp.get("weak_terms", [])))
    if interp.get("ignored_terms"):
        lines.append("Ignored filler: " + ", ".join(interp.get("ignored_terms", [])))
    git = repo.get("git") or {}
    if git:
        lines.append(f"Git: branch={git.get('branch')} commit={str(git.get('commit') or '')[:12]} dirty={git.get('dirty_files')}")
    if repo.get("indexer_rebuild_recommended"):
        lines.append("Index note: indexer version changed; run `neo-localmcp reindex` for a clean rebuild.")
    lines.append("")
    lines.append("Read first:")
    for idx, item in enumerate(data.get("read_first", []), start=1):
        ranges = _compact_line_hints(item.get("line_hints") or [])
        range_text = "; ".join(ranges) if ranges else "line hints unavailable"
        lines.append(f"  {idx}. {item.get('path')} [{item.get('category')}, score {item.get('score')}] — {range_text}")
        for reason in (item.get("reasons") or [])[:3]:
            lines.append(f"     - {reason}")
    if data.get("candidate_files"):
        read_paths = {item.get("path") for item in data.get("read_first", [])}
        others = [item for item in data.get("candidate_files", []) if item.get("path") not in read_paths]
        if others:
            lines.append("")
            lines.append("Other candidates:")
            for item in others[:10]:
                lines.append(f"  - {item.get('path')} [{item.get('category')}, score {item.get('score')}] — {', '.join(_compact_line_hints(item.get('line_hints') or [], 2)) or 'no line hints'}")
    guidance = data.get("agent_guidance") or []
    if guidance:
        lines.append("")
        lines.append("Agent guidance:")
        for item in guidance:
            lines.append(f"  - {item}")
    timing = data.get("ollama_timing")
    if timing:
        lines.append("")
        if timing.get("ok"):
            lines.append(f"Ollama: model={timing.get('model')} total={timing.get('total_seconds')}s eval={timing.get('eval_seconds')}s tokens={timing.get('eval_count')}")
        else:
            lines.append(f"Ollama: failed model={timing.get('model')} error={timing.get('error')}")
    ranking = data.get("ollama_ranking") or {}
    if ranking.get("response"):
        lines.append("")
        lines.append("Ollama ranking:")
        lines.append(ranking.get("response", "").strip())
    return "\n".join(lines)



def _mcp_compact_context(data: dict[str, Any]) -> dict[str, Any]:
    """Compact context response for MCP clients.

    Claude Desktop/Code and Codex clients can hang or feel slow if a tool returns the
    full diagnostic search payload. Keep the default MCP result small and useful,
    while leaving the CLI JSON path able to expose full diagnostics.
    """
    repo = data.get("repo_status") or {}
    git = repo.get("git") or {}

    def compact_item(item: dict[str, Any], reason_limit: int = 3, hint_limit: int = 5) -> dict[str, Any]:
        return {
            "path": item.get("path"),
            "category": item.get("category"),
            "score": item.get("score"),
            "line_hints": _compact_line_hints(item.get("line_hints") or [], hint_limit),
            "reasons": (item.get("reasons") or [])[:reason_limit],
        }

    read_paths = {item.get("path") for item in data.get("read_first", [])}
    other_candidates = [item for item in data.get("candidate_files", []) if item.get("path") not in read_paths]
    compact: dict[str, Any] = {
        "product": IDENTITY.product_name,
        "mode": "mcp_compact_agent_context",
        "mcp_response_version": "0.4.2.5",
        "task": data.get("task"),
        "repo_root": data.get("repo_root"),
        "repo": {
            "repo_id": repo.get("repo_id"),
            "db_path": repo.get("db_path"),
            "counts": repo.get("counts"),
            "stale_files": repo.get("stale_files"),
            "missing_files": repo.get("missing_files"),
            "git": {
                "branch": git.get("branch"),
                "commit": git.get("commit"),
                "dirty_files": git.get("dirty_files"),
            },
            "indexer_rebuild_recommended": repo.get("indexer_rebuild_recommended"),
        },
        "interpreted_query": data.get("interpreted_query"),
        "read_first": [compact_item(item) for item in data.get("read_first", [])],
        "other_candidates": [compact_item(item, reason_limit=2, hint_limit=2) for item in other_candidates[:5]],
        "agent_guidance": data.get("agent_guidance") or [],
        "instructions_for_agent": data.get("instructions_for_agent") or [],
        "ollama": {
            "requested": bool(data.get("ollama_requested")),
            "used": bool((data.get("ollama_ranking") or {}).get("ok")),
            "timing": data.get("ollama_timing"),
            "ranking": data.get("ollama_ranking"),
        },
        "note": "This MCP response is compact by default to avoid client hangs. Use the CLI with --format json for full search diagnostics.",
    }
    return compact


def _sanitize_ollama_advisory(text: str, max_chars: int = 1200) -> str:
    """Return a short, client-safe Ollama advisory.

    Deterministic READ FIRST is authoritative. Ollama text is advisory and can be
    verbose or malformed near timeout boundaries, so the MCP response only includes
    a bounded, line-aware excerpt.
    """
    cleaned = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(text or "")).strip()
    cleaned = re.sub(r"[ \t]+", " ", cleaned)
    lines = [line.rstrip() for line in cleaned.splitlines()]
    useful: list[str] = []
    keep = False
    for line in lines:
        s = line.strip()
        if not s:
            if useful and useful[-1] != "":
                useful.append("")
            continue
        lower = s.lower().strip("*# ")
        if lower.startswith(("recommended read order", "key line ranges", "risk", "do not read yet")):
            keep = True
            useful.append(s)
            continue
        if keep or re.match(r"^[-*]?\s*\d+[.)]\s+", s) or "risk" in lower:
            useful.append(s)
        if len("\n".join(useful)) >= max_chars:
            break
    excerpt = "\n".join(useful).strip() or cleaned[:max_chars].strip()
    if len(excerpt) > max_chars:
        cut = excerpt[:max_chars]
        last_newline = cut.rfind("\n")
        excerpt = (cut[:last_newline] if last_newline > 400 else cut).rstrip()
        excerpt += "\n...[ollama advisory truncated by neo-localmcp]"
    return excerpt


def _mcp_tiny_context_text(data: dict[str, Any]) -> str:
    """Ultra-small plain-text context for MCP clients.

    Some MCP clients handle plain text more reliably than nested JSON for tool results.
    This is the default server response in V4.2.5. It intentionally omits diagnostic
    search payloads; the CLI JSON path remains available for full debugging.
    """
    repo = data.get("repo_status") or {}
    git = repo.get("git") or {}
    interp = data.get("interpreted_query") or {}
    lines: list[str] = []
    lines.append("neo-localmcp context_prepare")
    lines.append(f"version: 0.4.2.5")
    lines.append(f"repo_root: {data.get('repo_root')}")
    if git:
        lines.append(f"git: branch={git.get('branch')} commit={str(git.get('commit') or '')[:12]} dirty={git.get('dirty_files')}")
    lines.append(f"intent: {interp.get('intent')} policy: {interp.get('ranking_policy')}")
    strong = interp.get("strong_terms") or []
    weak = interp.get("weak_terms") or []
    if strong:
        lines.append("strong_terms: " + ", ".join(str(x) for x in strong[:8]))
    if weak:
        lines.append("weak_terms: " + ", ".join(str(x) for x in weak[:8]))
    timing = data.get("ollama_timing") or {}
    if data.get("ollama_requested"):
        if timing.get("ok"):
            elapsed = timing.get("elapsed_seconds") or timing.get("total_seconds")
            lines.append(f"ollama: used=true model={timing.get('model')} elapsed={elapsed}s timeout={timing.get('timeout_seconds')}s")
            if timing.get("near_timeout"):
                lines.append("ollama_note: completed near timeout; deterministic READ FIRST remains authoritative")
        elif timing.get("timed_out"):
            lines.append(f"ollama: timed_out=true model={timing.get('model')} timeout={timing.get('timeout_seconds')}s")
        else:
            lines.append(f"ollama: used=false model={timing.get('model')} error={timing.get('error')}")
    else:
        lines.append("ollama: off")
    lines.append("")
    lines.append("READ FIRST")
    for idx, item in enumerate(data.get("read_first", [])[:5], start=1):
        hints = _compact_line_hints(item.get("line_hints") or [], 5)
        lines.append(f"{idx}. {item.get('path')} [{item.get('category')}, score {item.get('score')}]")
        if hints:
            lines.append("   lines: " + "; ".join(hints))
        for reason in (item.get("reasons") or [])[:3]:
            lines.append(f"   why: {reason}")
    guidance = data.get("agent_guidance") or []
    if guidance:
        lines.append("")
        lines.append("GUIDANCE")
        for item in guidance[:5]:
            lines.append(f"- {item}")
    ranking_obj = data.get("ollama_ranking") or {}
    ranking = ranking_obj.get("response") if ranking_obj.get("ok") else None
    if ranking:
        lines.append("")
        lines.append("OLLAMA ADVISORY (non-authoritative)")
        lines.append(_sanitize_ollama_advisory(str(ranking), max_chars=1200))
    elif data.get("ollama_requested") and ranking_obj:
        lines.append("")
        lines.append("OLLAMA ADVISORY (non-authoritative)")
        if ranking_obj.get("timed_out"):
            lines.append(f"unavailable: timed out after {ranking_obj.get('timeout_seconds')}s; deterministic READ FIRST remains authoritative")
        else:
            lines.append(f"unavailable: {ranking_obj.get('error') or 'no advisory returned'}; deterministic READ FIRST remains authoritative")
    lines.append("")
    lines.append("Note: Source files/tests are edit truth. neo-localmcp never generates code; it only narrows context and can apply exact approved patches.")
    return "\n".join(lines)

def _format(data: dict[str, Any], output_format: str = "json") -> str:
    if output_format == "text" and data.get("mode") == "agent_ready_natural_context":
        return _render_context_text(data)
    if output_format in {"mcp_text", "mcp_tiny", "agent_text"} and data.get("mode") == "agent_ready_natural_context":
        return _mcp_tiny_context_text(data)
    if output_format in {"mcp", "mcp_json", "compact_json"} and data.get("mode") == "agent_ready_natural_context":
        return json_out(_mcp_compact_context(data))
    return json_out(data)


def init() -> str:
    path = ensure_config()
    return json_out({
        "ok": True,
        "product": IDENTITY.product_name,
        "config_path": str(path),
        "next": [
            "Run setup once from anywhere: neo-localmcp setup --client all",
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
        "note": "Run index/context from the repo you want analyzed. Setup can be run once from anywhere.",
    })


def model_status() -> str:
    cfg = load_config()
    return json_out({
        "ollama_config": cfg.get("ollama", {}),
        "ollama_ping": ping(),
        "note": "Context is deterministic by default in V4.2.5. Use --ollama-rank or use_ollama=true to call /api/generate for ranking.",
    })


def doctor(repo_root: str = "auto") -> str:
    cfg = load_config()
    checks = {
        "config_exists": CONFIG_PATH.exists(),
        "db_open": True,
        "ollama": ping(),
        "repo": repo_memory.status(repo_root),
        "rules": [
            "neo-localmcp retrieves, indexes, summarizes, ranks, and applies exact approved patches.",
            "neo-localmcp does not generate source code or make engineering decisions.",
            "Claude/Codex reason and create exact patches.",
            "Context lookup is deterministic by default; Ollama ranking is opt-in with --ollama-rank or MCP use_ollama=true.",
        ],
        "commands": ["init", "where", "doctor", "status", "clients", "setup", "serve", "index", "reindex", "reset-repo", "reset-all", "test-determinism", "refresh", "lookup", "file", "context", "summarize", "apply-patch", "record-change", "model status"],
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


def _stable_context_projection(data: dict[str, Any]) -> dict[str, Any]:
    """Keep the fields that must be identical for deterministic context tests."""
    repo = data.get("repo_status") or {}
    git = repo.get("git") or {}
    def project_item(item: dict[str, Any]) -> dict[str, Any]:
        return {
            "path": item.get("path"),
            "category": item.get("category"),
            "score": item.get("score"),
            "reasons": item.get("reasons") or [],
            "line_hints": item.get("line_hints") or [],
        }
    return {
        "task": data.get("task"),
        "repo_id": repo.get("repo_id"),
        "git_commit": git.get("commit"),
        "interpreted_query": data.get("interpreted_query"),
        "read_first": [project_item(x) for x in data.get("read_first", [])],
        "candidate_files": [project_item(x) for x in data.get("candidate_files", [])],
        "agent_guidance": data.get("agent_guidance") or [],
    }


def _stable_hash(data: dict[str, Any]) -> str:
    canonical = json.dumps(_stable_context_projection(data), sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def test_determinism(task: str, repo_root: str = "auto", runs: int = 5, max_files: int = 80, limit: int = 10, reset_repo_first: bool = False, reindex_first: bool = False) -> str:
    if runs < 2:
        runs = 2
    root = repo_root_or_cwd(repo_root)
    setup_actions: list[dict[str, Any]] = []
    if reset_repo_first:
        setup_actions.append({"reset_repo": repo_memory.reset_repo(root)})
    if reindex_first or reset_repo_first:
        setup_actions.append({"reindex": repo_memory.index_repo(root, max_files=max_files, force=True)})
    outputs: list[dict[str, Any]] = []
    hashes: list[str] = []
    for i in range(runs):
        raw = context_prepare(task, root, max_files=max_files, limit=limit, use_ollama=False, output_format="json")
        data = json.loads(raw)
        projection = _stable_context_projection(data)
        digest = _stable_hash(data)
        outputs.append(projection)
        hashes.append(digest)
    first = hashes[0]
    mismatches = [i + 1 for i, h in enumerate(hashes) if h != first]
    diff_summary: list[dict[str, Any]] = []
    if mismatches:
        base = outputs[0]
        for idx in mismatches[:3]:
            other = outputs[idx - 1]
            diff_summary.append({
                "run": idx,
                "base_read_first": [(x.get("path"), x.get("score")) for x in base.get("read_first", [])],
                "this_read_first": [(x.get("path"), x.get("score")) for x in other.get("read_first", [])],
                "base_candidates": [(x.get("path"), x.get("score")) for x in base.get("candidate_files", [])],
                "this_candidates": [(x.get("path"), x.get("score")) for x in other.get("candidate_files", [])],
            })
    return json_out({
        "ok": not mismatches,
        "repo_root": str(root),
        "runs": runs,
        "reset_repo_first": reset_repo_first,
        "reindex_first": reindex_first or reset_repo_first,
        "setup_actions": setup_actions,
        "hashes": hashes,
        "unique_hashes": sorted(set(hashes)),
        "mismatches": mismatches,
        "diff_summary": diff_summary,
        "stable_projection_fields": ["task", "repo_id", "git_commit", "interpreted_query", "read_first", "candidate_files", "agent_guidance"],
        "note": "Ollama is intentionally disabled for determinism tests; use context --ollama-rank separately for model behavior.",
    })


def repo_refresh(repo_root: str = "auto", max_files: int | None = None, force: bool = False) -> str:
    return json_out(repo_memory.refresh(repo_root, force=force, max_files=max_files))


def repo_lookup(query: str, repo_root: str = "auto", limit: int = 20) -> str:
    return json_out(repo_memory.lookup(query, repo_root, limit=limit))


def file_context(path: str, repo_root: str = "auto", around_line: int | None = None, context_lines: int = 40) -> str:
    return json_out(repo_memory.file_context(path, repo_root, around_line=around_line, context_lines=context_lines))


def _resolve_reference(ref: str, indexed_files: list[str]) -> list[str]:
    ref_norm = ref.replace("\\", "/").strip("/")
    out: list[str] = []
    for path in sorted(indexed_files):
        if path == ref_norm or path.endswith("/" + ref_norm) or Path(path).name.lower() == Path(ref_norm).name.lower():
            if path not in out:
                out.append(path)
    return out[:5]


def _line_hint_from_reason(reason: str) -> str | None:
    m = re.search(r"line (\d+)", reason)
    if m:
        n = int(m.group(1))
        return f"around line {n}"
    m = re.search(r"lines? ([0-9][0-9,\- ]+)", reason)
    if m:
        return "lines " + m.group(1).strip()
    return None


def _add_candidate(candidates: dict[str, dict[str, Any]], path: str, reason: str, score: int, intent: str, *, line_hint: str | None = None, allow_reason_line_hint: bool = True) -> None:
    category = classify_path(path)
    if path not in candidates:
        candidates[path] = {"path": path, "category": category, "score": category_boost(category, intent), "reasons": [], "line_hints": []}
    item = candidates[path]
    # Score each unique evidence reason once. This prevents duplicate FTS/search rows or repeated refreshes
    # from changing deterministic scores across identical runs.
    is_new_reason = reason not in item["reasons"]
    if is_new_reason:
        item["reasons"].append(reason)
        item["score"] += score
    hint = line_hint if line_hint is not None else (_line_hint_from_reason(reason) if allow_reason_line_hint else None)
    if hint and hint not in item["line_hints"]:
        item["line_hints"].append(hint)


def _group_line_hints_for_guidance(item: dict[str, Any]) -> str:
    hints = _compact_line_hints(item.get("line_hints") or [], max_hints=4)
    return ", ".join(hints) if hints else "relevant lines"


def _agent_guidance(read_first: list[dict[str, Any]], interpreted: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    if read_first:
        compact = []
        for idx, item in enumerate(read_first[:READ_FIRST_MAX], start=1):
            compact.append(f"{idx}. {item['path']} ({_group_line_hints_for_guidance(item)})")
        lines.append("Read first: " + " | ".join(compact))
    lines.extend([
        "Do not grep broadly yet; use this result to narrow the first reads.",
        "For risky edits, verify current source before deciding.",
        "Use docs/status as orientation; source files and tests are the edit truth.",
        "If editing, produce an exact patch. neo-localmcp can apply exact approved patches only.",
    ])
    if interpreted.get("ignored_terms"):
        lines.append("The query parser ignored filler words; include known symbols/files after ':' to improve precision.")
    return lines


def _term_score(term: str, interpreted: dict[str, Any], strong: int, weak: int) -> int:
    return strong if term in interpreted.get("strong_terms", []) else weak


def context_prepare(task: str, repo_root: str = "auto", max_files: int = 80, limit: int = 10, use_ollama: bool = False, model: str | None = None, output_format: str = "json") -> str:
    root = repo_root_or_cwd(repo_root)
    status_data = repo_memory.status(root)
    if status_data["counts"].get("files", 0) == 0:
        repo_memory.index_repo(root, max_files=max_files, force=False)
    elif status_data.get("stale_files", 0) or status_data.get("missing_files", 0) or status_data.get("indexer_rebuild_recommended"):
        repo_memory.refresh(root, max_files=max_files, force=status_data.get("indexer_rebuild_recommended", False))

    interpreted = normalize_query(task)
    terms: list[str] = interpreted.get("search_terms") or []
    if not terms:
        terms = [task]
    indexed_files = repo_memory.list_indexed_files(root)
    candidates: dict[str, dict[str, Any]] = {}
    search_results: dict[str, list[dict[str, Any]]] = {}
    symbol_hits: list[dict[str, Any]] = []
    followed_references: list[dict[str, Any]] = []
    intent = interpreted.get("intent", "context")

    for term in terms[:20]:
        lookup_data = repo_memory.lookup(term, root, limit=limit * 3)
        for hit in lookup_data.get("hits", []):
            target = str(hit.get("target", ""))
            path = target.split(":", 1)[0]
            if path:
                _add_candidate(candidates, path, f"index hit for '{term}': {target}", _term_score(term, interpreted, 7, 4), intent)
        for sym in lookup_data.get("symbols", []):
            symbol_hits.append(sym)
            path = sym.get("file_path")
            if path:
                _add_candidate(candidates, path, f"symbol '{sym.get('name')}' line {sym.get('start_line')}", _term_score(term, interpreted, 16, 10), intent)
        rows = rg_search(term, root, max_results=30)
        rows = sorted(rows, key=lambda r: (str(r.get("path", "")), int(r.get("line") or 0), str(r.get("text", ""))))
        search_results[term] = rows
        for row in rows[:12]:
            path = row.get("path")
            if not path:
                continue
            weight = _term_score(term, interpreted, 8, 4)
            _add_candidate(candidates, path, f"search term '{term}' line {row.get('line')}", weight, intent)
            if classify_path(path) in {"docs", "status", "instructions"}:
                for ref in extract_file_references(row.get("text", "")):
                    for resolved in _resolve_reference(ref, indexed_files):
                        # Promote referenced source file, but do NOT copy the docs/status line number onto the source file.
                        _add_candidate(candidates, resolved, f"source reference from {path} line {row.get('line')}: {ref}", 12, intent, allow_reason_line_hint=False)
                        record = {"from": path, "line": row.get("line"), "reference": ref, "resolved": resolved}
                        if record not in followed_references:
                            followed_references.append(record)

    for term in terms:
        for resolved in _resolve_reference(term, indexed_files):
            _add_candidate(candidates, resolved, f"direct file reference '{term}'", 18, intent, allow_reason_line_hint=False)

    for item in candidates.values():
        item["reasons"] = sorted(item.get("reasons", []))
        item["line_hints"] = _compact_line_hints(item.get("line_hints", []))

    ranked = sorted(candidates.values(), key=lambda item: (-int(item.get("score", 0)), str(item.get("category", "")), str(item.get("path", ""))))
    ranked = ranked[:max(limit, 1)]
    read_first = [item for item in ranked if item.get("category") in {"source", "test", "config"}][: min(READ_FIRST_MAX, limit)]
    if len(read_first) < min(READ_FIRST_MAX, limit):
        for item in ranked:
            if item not in read_first:
                read_first.append(item)
            if len(read_first) >= min(READ_FIRST_MAX, limit):
                break

    symbol_map = repo_memory.symbols_for_files([item["path"] for item in read_first], root, max_per_file=8)
    for item in read_first:
        syms = symbol_map.get(item["path"], [])
        for sym in syms:
            hint = f"{sym.get('name')} around line {sym.get('start_line')}"
            if hint not in item["line_hints"]:
                item["line_hints"].append(hint)
        item["line_hints"] = _compact_line_hints(item.get("line_hints", []))

    deterministic = {
        "task": task,
        "repo_root": str(root),
        "mode": "agent_ready_natural_context",
        "repo_status": repo_memory.status(root),
        "interpreted_query": interpreted,
        "candidate_files": ranked,
        "read_first": read_first,
        "symbol_hits": sorted(symbol_hits[: limit * 3], key=lambda s: (str(s.get("file_path", "")), int(s.get("start_line") or 0), str(s.get("name", "")))),
        "followed_source_references": sorted(followed_references[:50], key=lambda r: (str(r.get("resolved", "")), str(r.get("from", "")), int(r.get("line") or 0))),
        "search_results_by_term": search_results,
        "agent_guidance": _agent_guidance(read_first, interpreted),
        "instructions_for_agent": [
            "Use this to narrow your read, not as final truth.",
            "Read current source before making risky edits.",
            "If editing, produce an exact patch; neo-localmcp can apply exact approved patches only.",
        ],
    }

    ollama_result: dict[str, Any] | None = None
    if use_ollama and ranked:
        prompt = f"""
You are a second-pass ranking reviewer for neo-localmcp. You improve the deterministic repo context; you do not replace it. Do not write source code.

Task: {task}
Interpreted query:
{json.dumps(interpreted, indent=2)}

Non-negotiable policy:
- Source files and tests outrank docs/status for debug, feature, and refactor tasks.
- Current source files are truth. Docs/status are orientation only.
- Never tell the agent to skip a source file that directly contains a requested symbol, method, property, API, or error.
- If a docs/status file only points at source files, put the source files in the read order and docs in "Do not read yet".
- Preserve the deterministic top candidates unless there is a clear reason to change order.
- Return concise sections exactly named: Recommended read order, Key line ranges, Do not read yet, Risk.
- Keep the whole answer under 900 words. Do not quote large code snippets.

Candidate files, scores, reasons, and line hints:
{json.dumps(ranked[:limit], indent=2)[:12000]}
""".strip()
        ollama_result = chat(prompt, model=model)

    result = {**deterministic, "ollama_requested": bool(use_ollama), "ollama_ranking": ollama_result, "ollama_timing": _format_model_timing(ollama_result)}
    if os.environ.get(CONTEXT_QUERY_RECORD_ENV, "").strip().lower() in {"1", "true", "yes", "on"}:
        repo_memory.record_task_query(task, result, root)
    return _format(result, output_format)


def summarize_file(path: str, repo_root: str = "auto", model: str | None = None) -> str:
    root = repo_root_or_cwd(repo_root)
    p = safe_path(path, root)
    ctx = repo_memory.file_context(rel(p, root), root)
    text = read_text_file(p, int(load_config().get("repo", {}).get("summary_max_chars", 80_000)))
    prompt = f"""
Summarize this file for repository working context. Do not write or suggest source code.
Return:
- purpose
- important symbols
- external dependencies
- likely related files
- risk areas
- confidence

File context:
{json.dumps(ctx, indent=2, default=str)[:20000]}

Current source file:
{text}
""".strip()
    result = chat(prompt, model=model)
    if result.get("ok") and result.get("response"):
        conn = repo_memory.connect()
        rid = repo_memory.upsert_repo(conn, root)
        relative = rel(p, root)
        conn.execute("UPDATE files SET purpose_summary=?, last_summarized_at=? WHERE repo_id=? AND path=?", (result["response"][:6000], repo_memory.now_iso(), rid, relative))
        conn.execute("INSERT INTO repo_fts(repo_id, kind, target, body) VALUES(?, 'summary', ?, ?)", (rid, relative, f"{relative}\n{result['response'][:6000]}"))
        conn.commit()
    return json_out({"file": rel(p, root), "context": ctx, "ollama_summary": result, "ollama_timing": _format_model_timing(result)})


def apply_unified_patch(patch_text: str, repo_root: str = "auto", check_only: bool = False) -> str:
    root = repo_root_or_cwd(repo_root)
    if not patch_text.strip():
        return json_out({"ok": False, "error": "patch_text is empty"})
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".patch", encoding="utf-8") as tmp:
        tmp.write(patch_text)
        patch_path = Path(tmp.name)
    try:
        check = run_command(["git", "apply", "--check", str(patch_path)], cwd=root, timeout=30)
        if check["returncode"] != 0:
            return json_out({"ok": False, "stage": "check", "stdout": check["stdout"], "stderr": check["stderr"]})
        if check_only:
            return json_out({"ok": True, "check_only": True, "message": "Patch applies cleanly. No files changed."})
        apply_result = run_command(["git", "apply", str(patch_path)], cwd=root, timeout=30)
        if apply_result["returncode"] != 0:
            return json_out({"ok": False, "stage": "apply", "stdout": apply_result["stdout"], "stderr": apply_result["stderr"]})
        changed = run_command(["git", "diff", "--name-only"], cwd=root, timeout=20)
        paths = [p.strip() for p in changed["stdout"].splitlines() if p.strip()]
        update = repo_memory.record_change("Applied exact approved unified patch", paths, root)
        return json_out({"ok": True, "changed_paths": paths, "memory_update": update})
    finally:
        try:
            patch_path.unlink(missing_ok=True)
        except Exception:
            pass


def record_change(summary: str, paths: list[str], repo_root: str = "auto") -> str:
    return json_out(repo_memory.record_change(summary, paths, repo_root))


def set_ollama(base_url: str | None = None, summary_model: str | None = None, fast_model: str | None = None, num_ctx: int | None = None) -> str:
    cfg = load_config()
    if base_url:
        cfg.setdefault("ollama", {})["base_url"] = base_url.rstrip("/")
    if summary_model:
        cfg.setdefault("ollama", {})["summary_model"] = summary_model
    if fast_model:
        cfg.setdefault("ollama", {})["fast_model"] = fast_model
    if num_ctx:
        cfg.setdefault("ollama", {})["num_ctx"] = int(num_ctx)
    save_config(cfg)
    return json_out({"ok": True, "ollama": cfg.get("ollama", {})})
