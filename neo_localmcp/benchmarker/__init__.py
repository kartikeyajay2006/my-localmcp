"""Repeatable benchmarking for neo-localmcp itself (#9).

CLI-only, administrative tool -- never exposed as an MCP tool, matching the
existing convention that administration is CLI-only (see cli.py). See
docs/1.1.1_PLAN.md for the full design and the decisions behind it.

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

from .. import repo_memory
from ..mcp_commands import memory, ollama, system
from ..utils import git_info, repo_root_or_cwd

# How many real indexed symbols to turn into mechanical synthetic queries.
# Deterministic sample (repo_memory.sample_symbols orders by file/line), not
# randomized, so repeated benchmark runs are comparable to each other.
DEFAULT_SYNTHETIC_SAMPLE_SIZE = 10

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
        # Filled in by _score_token_reduction (only for mem checks with a
        # known gold_file) -- left None here so every row has the same
        # columns regardless of group.
        "baseline_tokens": None,
        "reduction_ratio": None,
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


def _sys_checks(root: Path, options: dict[str, Any]) -> list[CheckResult]:
    """Group 'sys': a liveness sweep of the CLI/admin surface.

    No LLM, no Ollama -- always safe and effectively free to run. Reuses the
    existing mcp_commands/system.py functions directly (in-process), not a subprocess
    shell-out to the CLI.
    """
    root_str = str(root)
    return [
        _run_sys_command("sys", "doctor", lambda: system.doctor(root_str)),
        _run_sys_command("sys", "status", lambda: system.status(root_str)),
        _run_sys_command("sys", "where", lambda: system.where(root_str)),
        _run_sys_command("sys", "model_status", lambda: system.model_status()),
    ]


def _run_query_check(root: Path, mode: str, generation_method: str, task: str, gold_file: str | None, check_name: str) -> CheckResult:
    """Run one query through the determinism gate, then score it.

    record=False on every call here, always -- benchmark queries are never
    recorded as real usage (#9 design decision, not a config option).
    """
    started = time.monotonic()
    det = json.loads(memory.test_determinism(task, str(root), runs=5, record=False))
    deterministic = bool(det.get("ok")) and len(det.get("unique_hashes") or []) == 1
    if not deterministic:
        return _check(
            "mem", check_name, ok=False, wall_seconds=round(time.monotonic() - started, 3),
            mode=mode, generation_method=generation_method, gold_file=gold_file, deterministic=False,
            error="query is non-deterministic across 5 runs; excluded from accuracy scoring",
        )
    data = json.loads(memory.prepare_context(task, str(root), output_format="json", record=False))
    read_first = data.get("read_first") or []
    rank = None
    if gold_file is not None:
        rank = next((i + 1 for i, item in enumerate(read_first) if item.get("path") == gold_file), None)
    metrics = data.get("retrieval_metrics") or {}
    return _check(
        "mem", check_name, ok=True, wall_seconds=round(time.monotonic() - started, 3),
        mode=mode, generation_method=generation_method, gold_file=gold_file,
        rank_of_gold_file=rank, estimated_tokens=metrics.get("estimated_tokens_returned"), deterministic=True,
    )


def _mem_synthetic_checks(root: Path) -> list[CheckResult]:
    """Mechanical synthetic queries: template one query per real indexed
    symbol, gold file = that symbol's defining file. Zero LLM cost -- always
    labeled generation_method='mechanical' in the report, not silently
    implied to be LLM-generated.
    """
    results = []
    for sym in repo_memory.sample_symbols(root, limit=DEFAULT_SYNTHETIC_SAMPLE_SIZE):
        name = sym["name"]
        task = f"debug {name}: {name}"
        results.append(_run_query_check(root, "synthetic", "mechanical", task, sym["file_path"], f"synthetic: {name}"))
    return results


def _default_queries_path() -> Path:
    return Path(__file__).resolve().parent / "queries" / "default.jsonl"


def _load_natural_queries(path: Path | None) -> list[dict[str, Any]]:
    """Each row: {"task": "...", "gold_file": "..." | null}. gold_file may be
    null -- scored on speed/tokens only, no accuracy claim, since there's no
    agreed way yet to auto-generate ground truth for prose queries (#9 open
    question, not solved by this phase).
    """
    source = path or _default_queries_path()
    if not source.exists():
        return []
    queries = []
    for line in source.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            queries.append(json.loads(line))
    return queries


def _mem_natural_checks(root: Path, queries_path: Path | None) -> list[CheckResult]:
    """Hand-curated, natural-language-phrased queries -- the shape that
    catches bugs synthetic symbol-templated queries can't (e.g. #22/#23/#24
    were all found on prose queries, not synthetic ones)."""
    results = []
    for row in _load_natural_queries(queries_path):
        task = row["task"]
        gold_file = row.get("gold_file")
        label = task if len(task) <= 60 else task[:57] + "..."
        results.append(_run_query_check(root, "natural", "hand-curated", task, gold_file, f"natural: {label}"))
    return results


# README's stated acceptance target this group's proxy approximates -- only
# the "discovery/read tokens" half (>=50%). The "total task tokens" half
# (>=30%) needs real agent-in-the-loop measurement; that's #65, not this proxy.
DISCOVERY_READ_TARGET_RATIO = 0.50


def _score_token_reduction(root: Path, checks: list[CheckResult]) -> None:
    """Mutate each scored mem check in place with baseline_tokens (a whole
    gold-file read, char/4 estimate) and reduction_ratio (1 -
    estimated_tokens/baseline_tokens). A proxy for "MCP bundle vs. reading
    the whole file yourself" -- not real agent behavior (#65 covers that).
    """
    for check in checks:
        if check["group"] != "mem" or not check["ok"] or check["gold_file"] is None or check["estimated_tokens"] is None:
            continue
        try:
            char_count = len((root / check["gold_file"]).read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
        baseline_tokens = max(1, (char_count + 3) // 4)
        check["baseline_tokens"] = baseline_tokens
        check["reduction_ratio"] = round(1 - (check["estimated_tokens"] / baseline_tokens), 4)


def _mem_checks(root: Path, options: dict[str, Any]) -> list[CheckResult]:
    """Group 'mem': the retrieval/token-reduction benchmark itself.

    Precondition is `refresh` -- additive, never `reset-repo` -- so existing
    accumulated memory (however much or little exists) is never touched.
    """
    repo_memory.refresh(root)
    queries_path = options.get("queries_path")
    checks = _mem_synthetic_checks(root) + _mem_natural_checks(root, queries_path)
    _score_token_reduction(root, checks)
    return checks


# How many repeated ensure() calls the 'ollama' group's reliability check
# makes. Deliberately uses ensure() (status/start/warm), not chat() -- a
# real generation call per attempt would make this group slow to run
# routinely; ensure() alone already answers "is it alive and bootable."
OLLAMA_RELIABILITY_ATTEMPTS = 3


def _ollama_checks(root: Path, options: dict[str, Any]) -> list[CheckResult]:
    """Group 'ollama': reachability + configured-model presence + reliability.

    Never requires the model to already be warm/loaded -- a cold model is
    not a problem, that's exactly what the existing ensure()/chat() auto-load
    behavior (verified already in place, #36/#9 design discussion) is for.
    Reliability is scored as "did it respond usably," never "did it match
    last time" -- Ollama's own output is not expected to be deterministic.
    """
    results: list[CheckResult] = []

    started = time.monotonic()
    status_data = json.loads(ollama.ollama_status())
    state = status_data.get("state")
    reachable = state not in {"unreachable", "timed_out"}
    results.append(_check(
        "ollama", "reachable", ok=reachable, wall_seconds=round(time.monotonic() - started, 3),
        error=None if reachable else (status_data.get("error") or f"state={state}"),
    ))

    if state == "disabled":
        results.append(_check("ollama", "model_present", ok=True, wall_seconds=0.0, notes="Ollama disabled by config; not applicable."))
        results.append(_check("ollama", "reliability", ok=True, wall_seconds=0.0, notes="Ollama disabled by config; not applicable."))
        return results

    model_present = bool(status_data.get("installed"))
    results.append(_check(
        "ollama", "model_present", ok=model_present, wall_seconds=0.0,
        error=None if model_present else f"configured model {status_data.get('model')!r} is not installed -- run `ollama pull` to resolve",
    ))

    if not reachable:
        results.append(_check("ollama", "reliability", ok=False, wall_seconds=0.0, error="skipped: Ollama unreachable"))
        return results

    successes = 0
    attempt_seconds: list[float] = []
    for _ in range(OLLAMA_RELIABILITY_ATTEMPTS):
        attempt_started = time.monotonic()
        result = json.loads(ollama.ollama_ensure())
        attempt_seconds.append(round(time.monotonic() - attempt_started, 3))
        if result.get("ok"):
            successes += 1
    reliable = successes == OLLAMA_RELIABILITY_ATTEMPTS
    results.append(_check(
        "ollama", "reliability", ok=reliable, wall_seconds=round(sum(attempt_seconds), 3),
        notes=f"{successes}/{OLLAMA_RELIABILITY_ATTEMPTS} ensure() calls responded usably; per-attempt seconds: {attempt_seconds}",
        error=None if reliable else f"only {successes}/{OLLAMA_RELIABILITY_ATTEMPTS} attempts succeeded",
    ))
    return results


GROUPS: dict[str, Callable[[Path, dict[str, Any]], list[CheckResult]]] = {
    "sys": _sys_checks,
    "mem": _mem_checks,
    "ollama": _ollama_checks,
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


def _median(values: list[float]) -> float:
    ordered = sorted(values)
    mid = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[mid]
    return (ordered[mid - 1] + ordered[mid]) / 2


def _summarize(checks: list[CheckResult]) -> dict[str, Any]:
    by_group: dict[str, dict[str, Any]] = {}
    for c in checks:
        bucket = by_group.setdefault(c["group"], {"total": 0, "passed": 0})
        bucket["total"] += 1
        if c["ok"]:
            bucket["passed"] += 1

    ratios = [c["reduction_ratio"] for c in checks if c.get("reduction_ratio") is not None]
    token_reduction = None
    if ratios:
        median_ratio = round(_median(ratios), 4)
        token_reduction = {
            "queries_scored": len(ratios),
            "median_reduction_ratio": median_ratio,
            "target_ratio": DISCOVERY_READ_TARGET_RATIO,
            "meets_discovery_read_target": median_ratio >= DISCOVERY_READ_TARGET_RATIO,
            "note": (
                "Approximates only the README's 'discovery/read tokens' target (>=50%): MCP bundle "
                "tokens vs. a whole gold-file read, both char/4 estimates. Does NOT measure the 'total "
                "task tokens' target (>=30%) -- that needs real agent behavior; see issue #65."
            ),
        }

    return {
        "by_group": by_group,
        "total": len(checks),
        "passed": sum(1 for c in checks if c["ok"]),
        "token_reduction": token_reduction,
    }


def _render_markdown(report: dict[str, Any]) -> str:
    lines = [
        "# neo-localmcp benchmark report",
        "",
        f"- Benchmarked at: {report['benchmarked_at']}",
        f"- Repo: {report['repo_root']} (commit {report.get('repo_commit') or 'unknown'})",
        f"- Groups run: {', '.join(report['groups_run'])}",
        f"- Overall: {'PASS' if report['ok'] else 'FAIL'} ({report['summary']['passed']}/{report['summary']['total']} checks)",
    ]
    token_reduction = report["summary"].get("token_reduction")
    if token_reduction:
        verdict = "MEETS" if token_reduction["meets_discovery_read_target"] else "BELOW"
        lines.append(
            f"- Discovery/read token reduction (proxy, {token_reduction['queries_scored']} queries): "
            f"median {token_reduction['median_reduction_ratio']:.0%} vs. >= {token_reduction['target_ratio']:.0%} target -- {verdict}"
        )
    lines.append("")
    for note in report.get("notes", []):
        lines.append(f"> {note}")
    if token_reduction:
        lines.append(f"> {token_reduction['note']}")
    lines.append("")
    lines.append("| group | name | ok | wall_s | mode | gen. method | est. tokens | baseline tokens | reduction | gold rank | deterministic | error |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|")
    for c in report["checks"]:
        reduction = f"{c['reduction_ratio']:.0%}" if c.get("reduction_ratio") is not None else ""
        lines.append(
            f"| {c['group']} | {c['name']} | {'yes' if c['ok'] else 'no'} | {c['wall_seconds']} | "
            f"{c.get('mode') or ''} | {c.get('generation_method') or ''} | {c.get('estimated_tokens') if c.get('estimated_tokens') is not None else ''} | "
            f"{c.get('baseline_tokens') if c.get('baseline_tokens') is not None else ''} | {reduction} | "
            f"{c.get('rank_of_gold_file') if c.get('rank_of_gold_file') is not None else ''} | "
            f"{'' if c.get('deterministic') is None else ('yes' if c['deterministic'] else 'no')} | {c.get('error') or ''} |"
        )
    return "\n".join(lines) + "\n"


def _render_csv(report: dict[str, Any]) -> str:
    columns = ["group", "name", "ok", "wall_seconds", "mode", "generation_method", "estimated_tokens", "gold_file", "rank_of_gold_file", "baseline_tokens", "reduction_ratio", "deterministic", "error", "notes"]
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
    # installer/mcpb.py's _next_free_path).
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


def run_benchmark(groups: list[str], repo_root: str = "auto", out_dir: str | None = None, queries_path: str | None = None) -> dict[str, Any]:
    """Run the requested benchmark groups and write one timestamped report.

    Never modifies existing repository memory: no reset-repo is ever issued,
    and benchmark-invoked queries are never recorded into
    task_queries/retrieval_boost -- not a config option, always off.
    """
    root = repo_root_or_cwd(repo_root)
    resolved_groups = resolve_groups(groups)
    options: dict[str, Any] = {"queries_path": Path(queries_path) if queries_path else None}
    started = time.monotonic()
    checks: list[CheckResult] = []
    for group in resolved_groups:
        checks.extend(GROUPS[group](root, options))
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
