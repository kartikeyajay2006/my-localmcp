from __future__ import annotations

import json
import re
import tempfile
from pathlib import Path

from .. import repo_memory
from ..config import load_config
from ..ollama_client import chat
from ..utils import read_text_file, rel, repo_root_or_cwd, run_command, safe_path, sha256_file
from ._shared import _format_model_timing, _slim_status_for_nesting, json_out


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
