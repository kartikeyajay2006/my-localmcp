"""Repeatable benchmarking for neo-localmcp itself (#9).

CLI-only, administrative tool -- never exposed as an MCP tool, matching the
existing convention that administration is CLI-only (see cli.py). See
docs/1.2.0_PLAN.md for the full design and the decisions behind it.

Groups are registered in ``GROUPS`` below; ``full`` is always the union of
every *registered* group computed at call time, never a separately
maintained list, so a new group joins ``full`` just by being registered.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from . import tools
from .utils import git_info, repo_root_or_cwd

# Every check result has this shape, regardless of group. Fields that don't
# apply to a given check (e.g. gold_file for a sys command) are left None
# rather than omitted, so every row in the report has the same columns --
# important for the "manipulatable in a table" requirement.
CheckResult = dict[str, Any]


def _check(group: str, name: str, *, ok: bool, wall_seconds: float, mode: str | None = None,
           generation_method: str | None = None, estimated_tokens: int | None = None,
           gold_file: str | None = None, rank_of_gold_file: int | None = None,
           deterministic: bool | None = None, error: str | None = None, notes: str | None = None) -> CheckResult:
    return {
        "group": group,
        "name": name,
        "ok": ok,
        "wall_seconds": wall_seconds,
        "mode": mode,
        "generation_method": generation_method,
        "estimated_tokens": estimated_tokens,
        "gold_file": gold_file,
        "rank_of_gold_file": rank_of_gold_file,
        "deterministic": deterministic,
        "error": error,
        "notes": notes,
    }


def _run_sys_command(group: str, name: str, func: Callable[[], str]) -> CheckResult:
    """Run one existing CLI-backing function and record pass/fail + timing.

    Pass/fail here means "did the command run without erroring" -- not a
    quality judgement. A live issue (e.g. Ollama unreachable) surfaces as a
    separate flagged finding elsewhere, never as a benchmark failure (#9
    design decision).
    """
    started = time.monotonic()
    try:
        raw = func()
        data = json.loads(raw)
        ok = bool(data.get("ok", True))
        error = None if ok else str(data.get("error") or "reported ok=false")
    except Exception as exc:  # noqa: BLE001 - a check that raises is a failed check, not a crashed benchmark
        ok = False
        error = str(exc)
    return _check(group, name, ok=ok, wall_seconds=round(time.monotonic() - started, 3), error=error)


def _sys_checks(root: Path) -> list[CheckResult]:
    """Group 'sys': a liveness sweep of the CLI/admin surface.

    No LLM, no Ollama -- always safe and effectively free to run. Reuses the
    existing tools.py functions directly (in-process), not a subprocess
    shell-out to the CLI.
    """
    root_str = str(root)
    return [
        _run_sys_command("sys", "doctor", lambda: tools.doctor(root_str)),
        _run_sys_command("sys", "status", lambda: tools.status(root_str)),
        _run_sys_command("sys", "where", lambda: tools.where(root_str)),
        _run_sys_command("sys", "model_status", lambda: tools.model_status()),
    ]


GROUPS: dict[str, Callable[[Path], list[CheckResult]]] = {
    "sys": _sys_checks,
    # "mem": _mem_checks -- phase 1c/1d
    # "ollama": _ollama_checks -- phase 1e
}


def resolve_groups(requested: list[str]) -> list[str]:
    """Expand the requested group list, with 'full' meaning every registered group.

    Raises ValueError on an unknown group name rather than silently ignoring
    it -- a typo'd group must not silently benchmark nothing.
    """
    if not requested:
        raise ValueError("At least one group is required (e.g. 'sys', 'full').")
    if "full" in requested:
        return sorted(GROUPS.keys())
    unknown = [g for g in requested if g not in GROUPS]
    if unknown:
        known = ", ".join(sorted(GROUPS)) + ", full"
        raise ValueError(f"Unknown benchmark group(s): {', '.join(unknown)}. Known groups: {known}.")
    seen: list[str] = []
    for g in requested:
        if g not in seen:
            seen.append(g)
    return seen


def _summarize(checks: list[CheckResult]) -> dict[str, Any]:
    by_group: dict[str, dict[str, Any]] = {}
    for c in checks:
        bucket = by_group.setdefault(c["group"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        if c["ok"]:
            bucket["passed"] += 1
    return {"by_group": by_group, "total": len(checks), "passed": sum(1 for c in checks if c["ok"])}


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# neo-localmcp benchmark report",
        "",
        f"- Benchmarked at: {report['benchmarked_at']}",
        f"- Repo: {report['repo_root']} (commit {report.get('repo_commit') or 'unknown'})",
        f"- Groups run: {', '.join(report['groups_run'])}",
        f"- Overall: {'PASS' if report['ok'] else 'FAIL'} ({report['summary']['passed']}/{report['summary']['total']} checks)",
        "",
    ]
    for note in report.get("notes", []):
        lines.append(f"> {note}")
    lines.append("")
    lines.append("| group | name | ok | wall_s | mode | gen. method | est. tokens | gold rank | deterministic | error |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|")
    for c in report["checks"]:
        lines.append(
            f"| {c['group']} | {c['name']} | {'yes' if c['ok'] else 'no'} | {c['wall_seconds']} | "
            f"{c.get('mode') or ''} | {c.get('generation_method') or ''} | {c.get('estimated_tokens') if c.get('estimated_tokens') is not None else ''} | "
            f"{c.get('rank_of_gold_file') if c.get('rank_of_gold_file') is not None else ''} | "
            f"{'' if c.get('deterministic') is None else ('yes' if c['deterministic'] else 'no')} | {c.get('error') or ''} |"
        )
    return "\n".join(lines) + "\n"


def _render_csv(report: dict[str, Any]) -> str:
    columns = ["group", "name", "ok", "wall_seconds", "mode", "generation_method", "estimated_tokens", "gold_file", "rank_of_gold_file", "deterministic", "error", "notes"]
    lines = [",".join(columns)]
    for c in report["checks"]:
        row = []
        for col in columns:
            value = c.get(col)
            cell = "" if value is None else str(value).replace(",", ";")
            row.append(cell)
        lines.append(",".join(row))
    return "\n".join(lines) + "\n"


def _write_report(report: dict[str, Any], base_dir: Path) -> str:
    """Write json/md/csv for this run under a timestamped, never-overwritten path.

    Path convention (agreed during design): neo-localmcp_benchmarks/<timestamp>/
    benchmark_<groups-and-flags>_<timestamp>.{json,md,csv} -- under the
    current working directory, not inside the repo being benchmarked (which
    may not even belong to the person running the benchmark).
    """
    ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    groups_slug = "-".join(report["groups_run"]) or "none"
    # Two runs within the same second must never collide: never overwrite an
    # existing report, add a -2/-3/... counter instead (same precedent as
    # mcpb_build.py's _next_free_path).
    run_dir = base_dir / "neo-localmcp_benchmarks" / ts
    suffix = ""
    counter = 2
    while (run_dir.parent / f"{run_dir.name}{suffix}").exists():
        suffix = f"-{counter}"
        counter += 1
    run_dir = run_dir.parent / f"{run_dir.name}{suffix}"
    run_dir.mkdir(parents=True, exist_ok=False)
    stem = run_dir / f"benchmark_{groups_slug}_{ts}{suffix}"
    json_path = stem.with_suffix(".json")
    md_path = stem.with_suffix(".md")
    csv_path = stem.with_suffix(".csv")
    json_path.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(_render_markdown(report), encoding="utf-8")
    csv_path.write_text(_render_csv(report), encoding="utf-8")
    return str(json_path)


def run_benchmark(groups: list[str], repo_root: str = "auto", out_dir: str | None = None) -> dict[str, Any]:
    """Run the requested benchmark groups and write one timestamped report.

    Never modifies existing repository memory: no reset-repo is ever issued,
    and (once the mem group lands in a later phase) benchmark-invoked
    queries are not recorded into task_queries/retrieval_boost.
    """
    root = repo_root_or_cwd(repo_root)
    resolved_groups = resolve_groups(groups)
    started = time.monotonic()
    checks: list[CheckResult] = []
    for group in resolved_groups:
        checks.extend(GROUPS[group](root))
    wall_seconds = round(time.monotonic() - started, 3)

    git = git_info(root)
    report: dict[str, Any] = {
        "ok": all(c["ok"] for c in checks) if checks else True,
        "benchmarked_at": datetime.now(timezone.utc).isoformat(),
        "repo_root": str(root),
        "repo_commit": git.get("commit"),
        "repo_branch": git.get("branch"),
        "groups_requested": groups,
        "groups_run": resolved_groups,
        "wall_seconds": wall_seconds,
        "notes": [
            "Token figures are char/4 estimates, not real API telemetry (see #65 for real-telemetry comparison).",
            "This benchmark never modifies existing repository memory: it never runs reset-repo, and its own queries are not recorded into task_queries/retrieval_boost.",
        ],
        "checks": checks,
        "summary": _summarize(checks),
    }
    report["report_path"] = _write_report(report, Path.cwd() if out_dir is None else Path(out_dir))
    return report
