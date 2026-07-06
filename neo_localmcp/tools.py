from __future__ import annotations

import hashlib
import json
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any

from . import __version__
from . import repo_memory
from .config import CONFIG_PATH, ensure_config, load_config, save_config
from .identity import IDENTITY
from .ollama_client import chat, ensure as ensure_ollama, ping, start_service, status as ollama_state, stop_service, unload as unload_ollama, warm as warm_ollama
from .query import category_boost, classify_path, extract_file_references, normalize_query, term_key as compute_term_key
from .utils import read_text_file, rel, repo_root_or_cwd, rg_search, safe_path, sha256_file, run_command


LINE_HINT_MAX_PER_FILE = 5
READ_FIRST_MAX = 5


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
        lines.append(f"  {idx}. {item.get('path')} [{item.get('category')}, score {item.get('score')}] - {range_text}")
        for reason in (item.get("reasons") or [])[:3]:
            lines.append(f"     - {reason}")
    if data.get("candidate_files"):
        read_paths = {item.get("path") for item in data.get("read_first", [])}
        others = [item for item in data.get("candidate_files", []) if item.get("path") not in read_paths]
        if others:
            lines.append("")
            lines.append("Other candidates:")
            for item in others[:10]:
                lines.append(f"  - {item.get('path')} [{item.get('category')}, score {item.get('score')}] - {', '.join(_compact_line_hints(item.get('line_hints') or [], 2)) or 'no line hints'}")
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
        "mcp_response_version": "1.0.0",
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
        "context_excerpts": data.get("context_excerpts") or [],
        "retrieval_metrics": data.get("retrieval_metrics") or {},
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
    This is the default server response in V1. It intentionally omits diagnostic
    search payloads; the CLI JSON path remains available for full debugging.
    """
    repo = data.get("repo_status") or {}
    git = repo.get("git") or {}
    interp = data.get("interpreted_query") or {}
    lines: list[str] = []
    lines.append("neo-localmcp context_prepare")
    lines.append(f"version: {__version__}")
    lines.append(f"repo_root: {data.get('repo_root')}")
    if data.get("retrieval_id"):
        lines.append(f"retrieval_id: {data.get('retrieval_id')} (pass to file_excerpts to record whether you used the suggested section)")
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
    excerpts = data.get("context_excerpts") or []
    if excerpts:
        lines.append("")
        lines.append("CURRENT SOURCE EXCERPTS")
        for excerpt in excerpts:
            section = f" section '{excerpt.get('matched_name')}'" if excerpt.get("matched_name") else ""
            lines.append(f"--- {excerpt.get('path')}:{excerpt.get('start_line')}-{excerpt.get('end_line')}{section} sha256={str(excerpt.get('sha256') or '')[:12]} ---")
            lines.append(str(excerpt.get("text") or ""))
    metrics = data.get("retrieval_metrics") or {}
    if metrics:
        lines.append("")
        lines.append(f"retrieval: estimated_tokens={metrics.get('estimated_tokens_returned')} searches={metrics.get('repository_searches')} candidates={metrics.get('candidate_files')}")
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
    from . import lifecycle
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


def test_determinism(task: str, repo_root: str = "auto", runs: int = 5, max_files: int = 6, limit: int = 6, reset_repo_first: bool = False, reindex_first: bool = False) -> str:
    if runs < 2:
        runs = 2
    root = repo_root_or_cwd(repo_root)
    setup_actions: list[dict[str, Any]] = []
    if reset_repo_first:
        setup_actions.append({"reset_repo": repo_memory.reset_repo(root)})
    if reindex_first or reset_repo_first:
        setup_actions.append({"reindex": repo_memory.index_repo(root, max_files=None, force=True)})
    outputs: list[dict[str, Any]] = []
    hashes: list[str] = []
    for i in range(runs):
        raw = context_prepare(task, root, max_files=None, limit=min(max_files, limit), use_ollama=False, output_format="json")
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


def file_excerpts(ranges: list[dict[str, Any]], repo_root: str = "auto", max_chars: int = 20_000, retrieval_id: str | None = None) -> str:
    result = repo_memory.file_excerpts(ranges, repo_root, max_chars=max_chars)
    if retrieval_id:
        # P4/P5 (1.0.6): an explicit follow-up pull is the implicit success signal
        # for the earlier prepare_context call that returned this retrieval_id.
        # This only ever feeds the capped retrieval_boost table; it cannot change
        # what was already returned here.
        result["retrieval_feedback"] = repo_memory.record_retrieval_feedback(retrieval_id, repo_root, ranges)
    return json_out(result)


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


def _add_candidate(candidates: dict[str, dict[str, Any]], path: str, reason: str, score: int, intent: str, *, line_hint: str | None = None, allow_reason_line_hint: bool = True, strong_term: str | None = None) -> None:
    category = classify_path(path)
    if path not in candidates:
        candidates[path] = {"path": path, "category": category, "score": category_boost(category, intent), "reasons": [], "line_hints": [], "line_hint_weights": {}, "strong_terms_matched": set()}
    item = candidates[path]
    # Score each unique evidence reason once. This prevents duplicate FTS/search rows or repeated refreshes
    # from changing deterministic scores across identical runs.
    is_new_reason = reason not in item["reasons"]
    if is_new_reason:
        item["reasons"].append(reason)
        item["score"] += score
    if strong_term:
        item["strong_terms_matched"].add(strong_term)
    hint = line_hint if line_hint is not None else (_line_hint_from_reason(reason) if allow_reason_line_hint else None)
    if hint:
        if hint not in item["line_hints"]:
            item["line_hints"].append(hint)
        # Track the strongest evidence weight tied to each hinted line so a file with
        # several unrelated hit locations can later center on the most query-relevant
        # one instead of whichever hint happens to sort first by line number.
        m = re.search(r"(\d+)", hint)
        if m:
            line_no = int(m.group(1))
            item["line_hint_weights"][line_no] = max(item["line_hint_weights"].get(line_no, 0), score)


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


_MILESTONE_RE = re.compile(r"^[A-Za-z]\d+(?:\.\d+)*$")
# Cap a single section excerpt so one long section cannot starve the shared budget.
_MAX_SECTION_LINES = 80


def _heading_words(name: str) -> set[str]:
    return set(re.findall(r"[A-Za-z0-9_.]+", str(name or "").lower()))


def _heading_match_score(term: str, sym: dict[str, Any], interpreted: dict[str, Any]) -> tuple[int, str]:
    """Score a heading-symbol candidate hit.

    An exact milestone token (e.g. ``f4.7``) appearing in a heading is the
    strongest document signal available and must beat incidental code-symbol
    collisions on generic words like ``render``.
    """
    base = _term_score(term, interpreted, 16, 10)
    words = _heading_words(sym.get("name", ""))
    term_l = term.lower()
    if _MILESTONE_RE.match(term) and term_l in words:
        return base + 60, " [milestone]"
    if term_l in words:
        is_strong = term in interpreted.get("strong_terms", [])
        return base + (25 if is_strong else 12), " [heading-term]"
    return base, ""


def _best_heading_section(path: str, symbol_hits: list[dict[str, Any]], interpreted: dict[str, Any]) -> dict[str, Any] | None:
    """Pick the heading section in ``path`` that best matches the query terms.

    This is what lets a filename-anchored or free-text doc query land on the
    correct section instead of line 1. Returns the winning heading symbol, or
    ``None`` when no heading's text overlaps the query.
    """
    strong = [t.lower() for t in interpreted.get("strong_terms", [])]
    weak = [t.lower() for t in interpreted.get("weak_terms", [])]
    best: dict[str, Any] | None = None
    best_key: tuple[int, int] = (0, 0)
    for sym in symbol_hits:
        if sym.get("kind") != "heading" or sym.get("file_path") != path or not sym.get("start_line"):
            continue
        words = _heading_words(sym.get("name", ""))
        score = 0
        for t in strong:
            if t in words:
                score += 30 if _MILESTONE_RE.match(t) else 12
        for t in weak:
            if t in words:
                score += 6
        if score <= 0:
            continue
        # Highest score wins; ties break to the earliest section for stability.
        key = (score, -int(sym.get("start_line")))
        if key > best_key:
            best_key = key
            best = sym
    return best


def context_prepare(task: str, repo_root: str = "auto", max_files: int | None = None, limit: int = 6, use_ollama: bool = False, model: str | None = None, output_format: str = "json", token_budget: int = 3000) -> str:
    """Core retrieval implementation; prepare_context is the MCP/CLI adapter over this."""
    root = repo_root_or_cwd(repo_root)
    status_data = repo_memory.status(root)
    refreshed = False
    if status_data["counts"].get("files", 0) == 0 or not status_data.get("index_complete", False):
        repo_memory.index_repo(root, max_files=max_files, force=False)
        refreshed = True
    elif status_data.get("stale_files", 0) or status_data.get("missing_files", 0) or status_data.get("branch_changed") or status_data.get("indexer_rebuild_recommended"):
        repo_memory.refresh(root, max_files=max_files, force=status_data.get("indexer_rebuild_recommended", False))
        refreshed = True
    if refreshed:
        status_data = repo_memory.status(root)

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
    strong_terms_set = set(interpreted.get("strong_terms") or [])

    for term in terms[:20]:
        term_if_strong = term if term in strong_terms_set else None
        lookup_data = repo_memory.lookup(term, root, limit=limit * 3)
        for hit in lookup_data.get("hits", []):
            target = str(hit.get("target", ""))
            path = target.split(":", 1)[0]
            if path:
                score = _term_score(term, interpreted, 7, 4)
                if term.lower() in path.lower():
                    # A query token embedded in a filename is much stronger evidence
                    # than repeated prose/reference matches elsewhere in the repo.
                    score += _term_score(term, interpreted, 80, 15)
                _add_candidate(candidates, path, f"index hit for '{term}': {target}", score, intent, strong_term=term_if_strong)
        for sym in lookup_data.get("symbols", []):
            symbol_hits.append(sym)
            path = sym.get("file_path")
            if not path:
                continue
            if sym.get("kind") == "heading":
                score, label = _heading_match_score(term, sym, interpreted)
                _add_candidate(candidates, path, f"heading '{sym.get('name')}' line {sym.get('start_line')}{label}", score, intent, strong_term=term_if_strong)
            else:
                _add_candidate(candidates, path, f"symbol '{sym.get('name')}' line {sym.get('start_line')}", _term_score(term, interpreted, 16, 10), intent, strong_term=term_if_strong)
    batch_terms = terms[:20]
    if batch_terms:
        batch_pattern = "(?:" + "|".join(re.escape(term) for term in batch_terms) + ")"
        rows = rg_search(batch_pattern, root, max_results=max(60, limit * 12))
        rows = sorted(rows, key=lambda r: (str(r.get("path", "")), int(r.get("line") or 0), str(r.get("text", ""))))
        search_results["batched"] = rows
        for row in rows:
            path = row.get("path")
            if not path:
                continue
            haystack = f"{path} {row.get('text', '')}".lower()
            matched = [term for term in batch_terms if term.lower() in haystack]
            term = matched[0] if matched else batch_terms[0]
            weight = _term_score(term, interpreted, 8, 4)
            _add_candidate(candidates, path, f"search term '{term}'", weight, intent, line_hint=f"around line {row.get('line')}", strong_term=term if term in strong_terms_set else None)
            if classify_path(path) in {"docs", "status", "instructions"}:
                for ref in extract_file_references(row.get("text", "")):
                    for resolved in _resolve_reference(ref, indexed_files):
                        # Promote referenced source file, but do NOT copy the docs/status line number onto the source file.
                        _add_candidate(candidates, resolved, f"source reference from {path} line {row.get('line')}: {ref}", 12, intent, allow_reason_line_hint=False)
                        record = {"from": path, "line": row.get("line"), "reference": ref, "resolved": resolved}
                        if record not in followed_references:
                            followed_references.append(record)

    explicit_paths: set[str] = set()
    for reference in extract_file_references(task):
        explicit_paths.update(_resolve_reference(reference, indexed_files))

    for term in terms:
        for resolved in _resolve_reference(term, indexed_files):
            explicit_paths.add(resolved)
            _add_candidate(candidates, resolved, f"direct file reference '{term}'", 120, intent, allow_reason_line_hint=False, strong_term=term if term in strong_terms_set else None)

    # Distinct-strong-term-coverage bonus (#24): a file that co-occurs with several
    # *different* strong terms is much stronger evidence of relevance than a file
    # that racks up many repeated hits on a single overloaded term (e.g. "migration"
    # matching every symbol in a filesystem-layout-migration module by coincidence of
    # vocabulary). Applied once per candidate, after all term-matching passes above,
    # so it reflects breadth of query coverage rather than depth of one term's hits.
    for item in candidates.values():
        matched = item.pop("strong_terms_matched", None) or set()
        if len(matched) > 1:
            item["score"] += 20 * len(matched)

    for resolved in sorted(explicit_paths):
        _add_candidate(candidates, resolved, "explicit path in task", 120, intent, allow_reason_line_hint=False)

    # Retrieval-memory boost (1.0.6, P4/P5): a small, capped, recency-gated nudge
    # from prior sessions' implicit feedback (see repo_memory.get_boost_map). This
    # runs after all structural scoring above and is bounded well below any
    # structural signal (heading milestone match alone is +60), so memory can only
    # break near-ties, never override a real structural match.
    term_key = compute_term_key(interpreted)
    if term_key and candidates:
        boost_map = repo_memory.get_boost_map(root, term_key, list(candidates.keys()))
        if boost_map:
            for path, item in candidates.items():
                section = _best_heading_section(path, symbol_hits, interpreted)
                heading_name = section.get("name") if section else None
                boost = boost_map.get((path, ""), 0) + (boost_map.get((path, heading_name), 0) if heading_name else 0)
                if boost:
                    item["score"] += boost
                    item["reasons"].append(f"memory boost (+{boost}) from prior retrievals")

    for item in candidates.values():
        item["reasons"] = sorted(item.get("reasons", []))
        item["line_hints"] = _compact_line_hints(item.get("line_hints", []))

    ranked = sorted(candidates.values(), key=lambda item: (-int(item.get("score", 0)), str(item.get("category", "")), str(item.get("path", ""))))
    ranked = ranked[:max(limit, 1)]
    read_limit = min(max(1, int(limit)), max(1, int(max_files or limit)), 6)
    # Explicit user paths are authoritative even for feature/debug tasks. The
    # source-first policy applies after those requested files, not instead of them.
    read_first = [item for item in ranked if item.get("path") in explicit_paths][:read_limit]
    if intent in {"debug", "feature", "refactor", "test"}:
        for item in ranked:
            if len(read_first) >= read_limit:
                break
            if item not in read_first and item.get("category") in {"source", "test", "config"}:
                read_first.append(item)
    if len(read_first) < read_limit:
        for item in ranked:
            if item not in read_first:
                read_first.append(item)
            if len(read_first) >= read_limit:
                break

    symbol_map = repo_memory.symbols_for_files([item["path"] for item in read_first], root, max_per_file=8)
    for item in read_first:
        syms = symbol_map.get(item["path"], [])
        for sym in syms:
            hint = f"{sym.get('name')} around line {sym.get('start_line')}"
            if hint not in item["line_hints"]:
                item["line_hints"].append(hint)
        item["line_hints"] = _compact_line_hints(item.get("line_hints", []))

    ranges: list[dict[str, Any]] = []
    section_by_path: dict[str, dict[str, Any]] = {}
    sections_for_memory: list[dict[str, Any]] = []
    for item in read_first:
        # Prefer the matched heading's real section range so a long doc opens at
        # the relevant section, not its first line. Fall back to symbol/hint
        # centering, then the file opening.
        section = _best_heading_section(item["path"], symbol_hits, interpreted)
        if section:
            start = max(1, int(section["start_line"]))
            end = min(max(start, int(section["end_line"])), start + _MAX_SECTION_LINES - 1)
            ranges.append({"path": item["path"], "start_line": start, "end_line": end})
            section_by_path[item["path"]] = {"match_kind": "heading", "matched_name": section.get("name"), "start_line": start, "end_line": end}
            sections_for_memory.append({"path": item["path"], "start_line": start, "end_line": end, "match_kind": "heading", "matched_name": section.get("name"), "score": item.get("score")})
            continue
        center = None
        for term in interpreted.get("strong_terms", []):
            exact = next((sym for sym in symbol_hits if sym.get("file_path") == item["path"] and str(sym.get("name", "")).lower() == str(term).lower()), None)
            if exact and exact.get("start_line"):
                center = int(exact["start_line"])
                break
        if center is None:
            weights = item.get("line_hint_weights") or {}
            # Prefer the hint tied to the strongest matching evidence, not merely
            # whichever hint happens to sort first by line number -- a file can have
            # several distinct, unrelated hit locations, and the earliest one is not
            # necessarily the most query-relevant. Ties break to the earliest line
            # for determinism.
            center = min(weights, key=lambda line: (-weights[line], line)) if weights else 1
        range_start, range_end = max(1, center - 20), center + 19
        ranges.append({"path": item["path"], "start_line": range_start, "end_line": range_end})
        other_hint_lines = sorted({line for line in item.get("line_hint_weights", {}) if not (range_start <= line <= range_end)})
        sections_for_memory.append({
            "path": item["path"], "start_line": range_start, "end_line": range_end,
            "match_kind": "fallback", "matched_name": None, "score": item.get("score"),
            "hint_lines": other_hint_lines,
        })
    for item in candidates.values():
        item.pop("line_hint_weights", None)
    excerpt_data = repo_memory.file_excerpts(ranges, root, max_chars=max(1000, min(int(token_budget), 8000) * 4))
    for excerpt in excerpt_data.get("excerpts", []):
        section = section_by_path.get(excerpt.get("path"))
        if section:
            excerpt["match_kind"] = section["match_kind"]
            excerpt["matched_name"] = section["matched_name"]
    retrieval_id = uuid.uuid4().hex[:16]
    deterministic = {
        "task": task,
        "repo_root": str(root),
        "mode": "agent_ready_natural_context",
        "retrieval_id": retrieval_id,
        "repo_status": status_data,
        "interpreted_query": interpreted,
        "candidate_files": ranked,
        "read_first": read_first,
        "context_excerpts": excerpt_data.get("excerpts", []),
        "retrieval_metrics": {
            "token_budget": token_budget,
            "estimated_tokens_returned": (int(excerpt_data.get("chars_returned", 0)) + 3) // 4,
            "source_chars_returned": excerpt_data.get("chars_returned", 0),
            "repository_searches": 1 if batch_terms else 0,
            "candidate_files": len(ranked),
        },
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
        ollama_result = chat(prompt, model=model, purpose="ranking")

    ranking_full_status = (ollama_result or {}).get("ollama_status")
    ranking_result_for_nesting = {**ollama_result, "ollama_status": _slim_status_for_nesting(ranking_full_status)} if ollama_result else ollama_result
    result = {**deterministic, "ollama_requested": bool(use_ollama), "ollama_ranking": ranking_result_for_nesting, "ollama_timing": _format_model_timing(ollama_result), "ollama_status": ranking_full_status}
    repo_memory.record_task_query(task, root, retrieval_id=retrieval_id, term_key=term_key, sections=sections_for_memory, tool_version=__version__)
    return _format(result, output_format)


def prepare_context(task: str, repo_root: str = "auto", token_budget: int = 3000, max_files: int = 6, use_ollama: bool = False, model: str | None = None, output_format: str = "mcp_text") -> str:
    """MCP/CLI-facing adapter -- see context_prepare for the core implementation."""
    return context_prepare(task, repo_root, max_files=None, limit=max_files, use_ollama=use_ollama, model=model, output_format=output_format, token_budget=token_budget)


def _cap_keyword_terms(raw: str, max_terms: int = 8) -> str:
    """Cap by comma-separated term count, not character count.

    A generation-length cap (see ollama_client.chat's num_predict) is the primary
    defense against a runaway response, but this is the belt-and-suspenders layer:
    even a response that stays under the token cap could still cram far more than
    the requested "at most 8" terms into a shorter space. Enforce the actual shape
    regardless of what the model produced.
    """
    terms = [t.strip() for t in raw.split(",") if t.strip()]
    return ", ".join(terms[:max_terms])


def _split_summary_keywords(text: str) -> tuple[str, str]:
    """Best-effort split of a 'summary: ...\\nkeywords: ...' style Ollama response."""
    summary_match = re.search(r"summary\s*:\s*(.+?)(?:\n\s*keywords\s*:|\Z)", text, re.IGNORECASE | re.DOTALL)
    keywords_match = re.search(r"keywords\s*:\s*(.+)", text, re.IGNORECASE | re.DOTALL)
    summary = (summary_match.group(1).strip() if summary_match else text.strip())[:2000]
    keywords_raw = (keywords_match.group(1).strip() if keywords_match else "")[:4000]
    keywords = _cap_keyword_terms(keywords_raw)
    return summary, keywords


def _slim_status_for_nesting(status: dict[str, Any] | None) -> dict[str, Any] | None:
    """Drop the verbose installed_models list from a second, nested copy of an Ollama
    status dict. The top-level ollama_status key in the same response keeps the full
    list; embedding it again inside ollama_summary/ollama_ranking is pure duplication."""
    if not status:
        return status
    return {k: v for k, v in status.items() if k != "installed_models"}


def _summarize_section(path: str, heading: str, root: Path, model: str | None) -> str:
    sym = repo_memory.find_heading_symbol(path, heading, root)
    if not sym:
        return json_out({"ok": False, "error": f"heading not found: {heading}", "path": path})
    current_hash = sha256_file(safe_path(path, root))
    cached = repo_memory.get_section_summary(path, heading, root)
    if cached and cached.get("source_hash") == current_hash and (not model or cached.get("model") == model):
        return json_out({
            "file": cached.get("file_path"), "heading": heading, "cached": True,
            "start_line": cached.get("start_line"), "end_line": cached.get("end_line"),
            "summary": cached.get("summary"), "keywords": cached.get("keywords"),
            "model": cached.get("model"), "prompt_version": cached.get("prompt_version"),
        })
    excerpt_data = repo_memory.file_excerpts([{"path": path, "start_line": sym["start_line"], "end_line": sym["end_line"]}], root, max_chars=12_000)
    section_text = ((excerpt_data.get("excerpts") or [{}])[0]).get("text", "")
    prompt = f"""
Summarize this single document section for repository working context. Do not write or suggest source code.
Return exactly two labeled parts:
summary: one or two factual sentences describing what this section covers
keywords: a short comma-separated list of section-specific terms, at most 8

Section heading: {sym.get('signature') or heading}
Section text:
{section_text}
""".strip()
    num_predict = int(load_config().get("ollama", {}).get("section_summary_num_predict", 400))
    result = chat(prompt, model=model, purpose="summary", num_predict=num_predict)
    eval_count = (result.get("raw") or {}).get("eval_count")
    # Ollama stops generation at exactly num_predict tokens when the cap is what ended
    # the response rather than the model choosing to stop -- a reliable runaway signal.
    truncated = bool(result.get("ok") and eval_count is not None and int(eval_count) >= num_predict)
    stored = None
    if result.get("ok") and result.get("response") and not truncated:
        summary_text, keywords_text = _split_summary_keywords(str(result["response"]))
        stored = repo_memory.store_section_summary(path, heading, int(sym["start_line"]), int(sym["end_line"]), summary_text, keywords_text, str(result.get("model") or model or ""), "section-summary-v1", root)
    full_status = result.get("ollama_status")
    result_for_nesting = {**result, "ollama_status": _slim_status_for_nesting(full_status)}
    return json_out({
        "file": stored.get("path") if stored else path, "heading": heading, "cached": False, "truncated": truncated,
        "start_line": sym["start_line"], "end_line": sym["end_line"],
        "ollama_summary": result_for_nesting, "ollama_timing": _format_model_timing(result), "ollama_status": full_status,
        "stored": stored,
    })


def summarize_file(path: str, repo_root: str = "auto", model: str | None = None, heading: str | None = None) -> str:
    root = repo_root_or_cwd(repo_root)
    if heading:
        # P6 (1.0.6): section-scoped enrichment. This never determines a heading's
        # line boundaries -- those stay authoritative from the deterministic
        # extractor -- it only adds cached, keyword-searchable summary text.
        return _summarize_section(path, heading, root, model)
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
    result = chat(prompt, model=model, purpose="summary")
    if result.get("ok") and result.get("response"):
        repo_memory.store_summary(rel(p, root), result["response"], str(result.get("model") or model or ""), "file-summary-v1", root)
    full_status = result.get("ollama_status")
    result_for_nesting = {**result, "ollama_status": _slim_status_for_nesting(full_status)}
    return json_out({"file": rel(p, root), "context": ctx, "ollama_summary": result_for_nesting, "ollama_timing": _format_model_timing(result), "ollama_status": full_status})


def apply_unified_patch(patch_text: str, repo_root: str = "auto", check_only: bool = False) -> str:
    root = repo_root_or_cwd(repo_root)
    if not patch_text.strip():
        return json_out({"ok": False, "error": "patch_text is empty"})
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".patch", encoding="utf-8", newline="") as tmp:
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
    return json_out({"ok": True, "ollama": cfg.get("ollama", {}), "status": ollama_state()})


def ollama_status(model: str | None = None, purpose: str = "ranking") -> str:
    return json_out(ollama_state(model, purpose))


def ollama_ensure(model: str | None = None, purpose: str = "ranking") -> str:
    return json_out(ensure_ollama(model, purpose))


def ollama_control(action: str, model: str | None = None, purpose: str = "ranking") -> str:
    actions = {
        "status": lambda: ollama_state(model, purpose),
        "ensure": lambda: ensure_ollama(model, purpose),
        "start": start_service,
        "warm": lambda: warm_ollama(model, purpose),
        "unload": lambda: unload_ollama(model, purpose),
        "stop": stop_service,
        "test": lambda: chat("Reply with exactly: ok", model=model, purpose=purpose),
    }
    if action not in actions:
        return json_out({"ok": False, "error": f"unknown Ollama action: {action}"})
    return json_out(actions[action]())
