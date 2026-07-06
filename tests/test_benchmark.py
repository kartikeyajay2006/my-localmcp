from __future__ import annotations

import inspect
import json

import pytest

from neo_localmcp import benchmark


def _seed_repo(repo):
    repo.mkdir()
    (repo / "service.py").write_text("def load_model():\n    return 'ready'\n", encoding="utf-8")
    return repo


def test_resolve_groups_full_is_union_of_registered_groups():
    assert benchmark.resolve_groups(["full"]) == sorted(benchmark.GROUPS.keys())


def test_resolve_groups_rejects_unknown_group():
    with pytest.raises(ValueError, match="Unknown benchmark group"):
        benchmark.resolve_groups(["not-a-real-group"])


def test_resolve_groups_requires_at_least_one():
    with pytest.raises(ValueError):
        benchmark.resolve_groups([])


def test_resolve_groups_dedupes_preserving_order():
    assert benchmark.resolve_groups(["sys", "sys"]) == ["sys"]


def test_sys_group_runs_and_reports_pass(tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    report = benchmark.run_benchmark(["sys"], repo_root=str(repo), out_dir=str(tmp_path / "out"))
    assert report["ok"] is True
    assert report["groups_run"] == ["sys"]
    assert report["checks"]
    assert all(c["group"] == "sys" for c in report["checks"])
    assert {c["name"] for c in report["checks"]} == {"doctor", "status", "where", "model_status"}


def test_full_expands_to_every_registered_group(tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    report = benchmark.run_benchmark(["full"], repo_root=str(repo), out_dir=str(tmp_path / "out"))
    assert report["groups_requested"] == ["full"]
    assert report["groups_run"] == sorted(benchmark.GROUPS.keys())


def test_report_is_written_to_a_never_overwritten_timestamped_path(tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    out_dir = tmp_path / "out"
    first = benchmark.run_benchmark(["sys"], repo_root=str(repo), out_dir=str(out_dir))
    second = benchmark.run_benchmark(["sys"], repo_root=str(repo), out_dir=str(out_dir))
    assert first["report_path"] != second["report_path"]
    for path_str in (first["report_path"], second["report_path"]):
        loaded = json.loads(open(path_str, encoding="utf-8").read())
        assert loaded["groups_run"] == ["sys"]
    # sibling .md and .csv were also written next to the .json
    from pathlib import Path
    json_path = Path(first["report_path"])
    assert json_path.with_suffix(".md").exists()
    assert json_path.with_suffix(".csv").exists()


def test_report_includes_transparency_notes(tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    report = benchmark.run_benchmark(["sys"], repo_root=str(repo), out_dir=str(tmp_path / "out"))
    notes_text = " ".join(report["notes"])
    assert "estimate" in notes_text.lower()
    assert "reset-repo" in notes_text.lower() or "reset_repo" in notes_text.lower()


def test_report_names_the_benchmarked_repo_and_group_selection(tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    report = benchmark.run_benchmark(["sys"], repo_root=str(repo), out_dir=str(tmp_path / "out"))
    assert report["repo_root"] == str(repo.resolve())
    assert report["groups_requested"] == ["sys"]
    assert report["groups_run"] == ["sys"]


def test_benchmark_module_never_calls_reset_repo():
    """Structural guard for the 'never destructive' design decision (#9):
    a future group implementation must not reach for reset_repo/reset_all,
    even accidentally, since the benchmark must be safe to run against a
    repo with real accumulated memory."""
    source = inspect.getsource(benchmark)
    assert "reset_repo" not in source
    assert "reset_all" not in source
