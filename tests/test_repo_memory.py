from __future__ import annotations

import pytest

from neo_localmcp import repo_memory

pytestmark = pytest.mark.retrieval


def test_complete_index_prunes_deleted_files(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    first = repo / "first.py"
    second = repo / "second.py"
    first.write_text("def first():\n    return 1\n", encoding="utf-8")
    second.write_text("def second():\n    return 2\n", encoding="utf-8")

    initial = repo_memory.index_repo(repo)
    assert initial["index_complete"] is True
    assert initial["eligible_files"] == 2

    second.unlink()
    refreshed = repo_memory.refresh(repo)
    assert refreshed["removed"] == 1
    assert repo_memory.list_indexed_files(repo) == ["first.py"]


def test_capped_index_reports_incomplete_without_pruning(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    for name in ("a.py", "b.py", "c.py"):
        (repo / name).write_text(f"VALUE = '{name}'\n", encoding="utf-8")

    full = repo_memory.index_repo(repo)
    assert full["indexed_files"] == 3
    capped = repo_memory.index_repo(repo, max_files=1)
    assert capped["index_complete"] is False
    assert capped["eligible_files"] == 3
    assert len(repo_memory.list_indexed_files(repo)) == 3


def test_summary_is_replaced_and_invalidated_on_source_change(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    source = repo / "module.py"
    source.write_text("def value():\n    return 1\n", encoding="utf-8")
    repo_memory.index_repo(repo)

    repo_memory.store_summary("module.py", "first summary", "model-a", "prompt-v1", repo)
    repo_memory.store_summary("module.py", "second summary", "model-a", "prompt-v1", repo)
    conn = repo_memory.connect()
    rid = repo_memory.repo_id(repo)
    count = conn.execute("SELECT COUNT(*) FROM repo_fts WHERE repo_id=? AND kind='summary' AND target='module.py'", (rid,)).fetchone()[0]
    assert count == 1

    source.write_text("def value():\n    return 2\n", encoding="utf-8")
    repo_memory.refresh(repo)
    row = conn.execute("SELECT purpose_summary, summary_source_hash FROM files WHERE repo_id=? AND path='module.py'", (rid,)).fetchone()
    assert row["purpose_summary"] is None
    assert row["summary_source_hash"] is None


def test_file_context_line_count_is_total_not_radius(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    path = repo / "lines.py"
    path.write_text("\n".join(f"line_{number}" for number in range(1, 101)), encoding="utf-8")
    repo_memory.index_repo(repo)
    result = repo_memory.file_context("lines.py", repo, around_line=50, context_lines=12)
    excerpt = result["excerpt"]
    assert excerpt["end_line"] - excerpt["start_line"] + 1 == 12


def test_repo_identity_separates_clones(tmp_path, isolated_config):
    one = tmp_path / "one"
    two = tmp_path / "two"
    one.mkdir()
    two.mkdir()
    assert repo_memory.repo_id(one) != repo_memory.repo_id(two)


def test_lookup_does_not_probe_git_metadata(tmp_path, isolated_config, monkeypatch):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module.py").write_text("def MainViewModel():\n    pass\n", encoding="utf-8")
    repo_memory.index_repo(repo)
    monkeypatch.setattr(repo_memory, "git_info", lambda *_: (_ for _ in ()).throw(AssertionError("lookup must stay read-only")))
    result = repo_memory.lookup("MainViewModel", repo)
    assert result["hits"] or result["symbols"]


def test_file_excerpts_clamps_stale_line_hint_to_eof(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "short.md").write_text("one\ntwo\n", encoding="utf-8")
    result = repo_memory.file_excerpts([{"path": "short.md", "start_line": 100, "end_line": 140}], repo)
    excerpt = result["excerpts"][0]
    assert excerpt["start_line"] == 2
    assert excerpt["end_line"] == 2
    assert excerpt["text"] == "2: two"


def test_lookup_finds_sql_table_name_that_is_not_a_def_or_class(tmp_path, isolated_config):
    """Regression for #22: a SQL table name (or any string-literal identifier)
    embedded in a CREATE TABLE statement is never a `def`/`class` symbol, so
    extract_symbols never sees it. lookup must still find it via full file
    content, the same way a plain grep would, instead of only ever matching
    the narrow class/function symbol patterns."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "storage.py").write_text(
        "def init_db(conn):\n"
        "    conn.execute(\n"
        "        \"\"\"\n"
        "        CREATE TABLE IF NOT EXISTS section_summaries (\n"
        "            id INTEGER PRIMARY KEY\n"
        "        )\n"
        "        \"\"\"\n"
        "    )\n",
        encoding="utf-8",
    )
    repo_memory.index_repo(repo)
    result = repo_memory.lookup("section_summaries", repo)
    assert result["hits"], "expected a full-text hit for a table name that never appears as a def/class symbol"


def test_sample_symbols_is_deterministic_and_kind_restricted(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text(
        "class Widget:\n    pass\n\n"
        "def build_widget():\n    pass\n",
        encoding="utf-8",
    )
    (repo / "b.md").write_text("# A heading\n\nprose\n", encoding="utf-8")
    repo_memory.index_repo(repo)

    first = repo_memory.sample_symbols(repo, limit=10)
    second = repo_memory.sample_symbols(repo, limit=10)
    assert first == second  # deterministic across repeated calls, not randomized

    kinds_seen = {s["kind"] for s in first}
    assert kinds_seen <= {"function", "class", "method", "type"}
    assert "heading" not in kinds_seen
    names = {s["name"] for s in first}
    assert {"Widget", "build_widget"} <= names


def test_sample_symbols_respects_limit(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("\n".join(f"def fn_{i}():\n    pass\n" for i in range(10)), encoding="utf-8")
    repo_memory.index_repo(repo)
    assert len(repo_memory.sample_symbols(repo, limit=3)) == 3


def test_sample_symbols_spreads_across_files_not_just_the_first_alphabetically(tmp_path, isolated_config):
    """Regression for #9: a file that sorts first alphabetically (or is just
    large) must not dominate the entire sample -- caught for real during
    benchmark smoke-testing, where a legacy-directory file that happened to
    sort first supplied all 10 synthetic queries and nothing else was ever
    sampled."""
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "_aaa_big_file.py").write_text("\n".join(f"def big_fn_{i}():\n    pass\n" for i in range(20)), encoding="utf-8")
    (repo / "z_small_file.py").write_text("def small_fn():\n    pass\n", encoding="utf-8")
    repo_memory.index_repo(repo)
    sample = repo_memory.sample_symbols(repo, limit=5)
    files_seen = {s["file_path"] for s in sample}
    assert files_seen == {"_aaa_big_file.py", "z_small_file.py"}, "the small/alphabetically-later file must still be represented"

    # Deterministic across repeated calls, still.
    assert repo_memory.sample_symbols(repo, limit=5) == sample
