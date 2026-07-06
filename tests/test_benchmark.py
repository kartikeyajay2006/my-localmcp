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


def _fake_ollama_status(state, **overrides):
    data = {"state": state, "model": "fake-model", "installed": True, "base_url": "http://127.0.0.1:11434"}
    data.update(overrides)
    return json.dumps(data)


def test_ollama_group_healthy(monkeypatch, tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    monkeypatch.setattr(benchmark.tools, "ollama_status", lambda: _fake_ollama_status("ready"))
    monkeypatch.setattr(benchmark.tools, "ollama_ensure", lambda: json.dumps({"ok": True, "state": "ready"}))
    report = benchmark.run_benchmark(["ollama"], repo_root=str(repo), out_dir=str(tmp_path / "out"))
    assert report["ok"] is True
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["reachable"]["ok"] is True
    assert by_name["model_present"]["ok"] is True
    assert by_name["reliability"]["ok"] is True
    assert "3/3" in by_name["reliability"]["notes"]


def test_ollama_group_flags_unreachable_without_crashing(monkeypatch, tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    monkeypatch.setattr(benchmark.tools, "ollama_status", lambda: _fake_ollama_status("unreachable", installed=False, error="connection refused"))
    monkeypatch.setattr(benchmark.tools, "ollama_ensure", lambda: (_ for _ in ()).throw(AssertionError("must not attempt ensure() when unreachable")))
    report = benchmark.run_benchmark(["ollama"], repo_root=str(repo), out_dir=str(tmp_path / "out"))
    assert report["ok"] is False
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["reachable"]["ok"] is False
    assert by_name["reliability"]["ok"] is False


def test_ollama_group_flags_missing_model_even_when_reachable(monkeypatch, tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    monkeypatch.setattr(benchmark.tools, "ollama_status", lambda: _fake_ollama_status("model_cold", installed=False))
    monkeypatch.setattr(benchmark.tools, "ollama_ensure", lambda: json.dumps({"ok": True, "state": "ready"}))
    report = benchmark.run_benchmark(["ollama"], repo_root=str(repo), out_dir=str(tmp_path / "out"))
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["model_present"]["ok"] is False
    assert "not installed" in by_name["model_present"]["error"]


def test_ollama_group_disabled_by_config_is_not_a_failure(monkeypatch, tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    monkeypatch.setattr(benchmark.tools, "ollama_status", lambda: _fake_ollama_status("disabled"))
    monkeypatch.setattr(benchmark.tools, "ollama_ensure", lambda: (_ for _ in ()).throw(AssertionError("must not attempt ensure() when disabled")))
    report = benchmark.run_benchmark(["ollama"], repo_root=str(repo), out_dir=str(tmp_path / "out"))
    assert report["ok"] is True
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["model_present"]["ok"] is True
    assert by_name["reliability"]["ok"] is True


def test_ollama_group_reliability_reflects_partial_failures(monkeypatch, tmp_path, isolated_config):
    repo = _seed_repo(tmp_path / "repo")
    attempts = iter([True, False, True])
    monkeypatch.setattr(benchmark.tools, "ollama_status", lambda: _fake_ollama_status("ready"))
    monkeypatch.setattr(benchmark.tools, "ollama_ensure", lambda: json.dumps({"ok": next(attempts)}))
    report = benchmark.run_benchmark(["ollama"], repo_root=str(repo), out_dir=str(tmp_path / "out"))
    by_name = {c["name"]: c for c in report["checks"]}
    assert by_name["reliability"]["ok"] is False
    assert "2/3" in by_name["reliability"]["notes"]


def test_mem_group_scores_token_reduction_against_discovery_read_target(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("def load_model():\n    return 'ready'\n" + "# filler line\n" * 500, encoding="utf-8")
    report = benchmark.run_benchmark(["mem"], repo_root=str(repo), out_dir=str(tmp_path / "out"), queries_path=str(tmp_path / "empty.jsonl"))
    scored = [c for c in report["checks"] if c["group"] == "mem" and c.get("gold_file") == "service.py"]
    assert scored, "expected at least one synthetic query targeting service.py"
    for c in scored:
        assert c["baseline_tokens"] is not None
        assert c["reduction_ratio"] is not None
        # A big filler file read whole vs. a bounded MCP excerpt should reduce tokens a lot.
        assert c["reduction_ratio"] > 0.5

    token_reduction = report["summary"]["token_reduction"]
    assert token_reduction is not None
    assert token_reduction["queries_scored"] == len(scored)
    assert token_reduction["target_ratio"] == benchmark.DISCOVERY_READ_TARGET_RATIO
    assert token_reduction["meets_discovery_read_target"] is True


def test_mem_group_reduction_ratio_is_none_without_a_gold_file(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "service.py").write_text("def load_model():\n    return 'ready'\n", encoding="utf-8")
    queries = tmp_path / "queries.jsonl"
    queries.write_text('{"task": "how does this repo work overall", "gold_file": null}\n', encoding="utf-8")
    report = benchmark.run_benchmark(["mem"], repo_root=str(repo), out_dir=str(tmp_path / "out"), queries_path=str(queries))
    natural = next(c for c in report["checks"] if c["mode"] == "natural")
    assert natural["baseline_tokens"] is None
    assert natural["reduction_ratio"] is None
