"""Migration-safety regression tests for repo_memory's schema evolution.

Background (GitHub issue #7): `repo_memory.init_db` evolves the SQLite schema
purely with `CREATE TABLE IF NOT EXISTS` + hand-written `ALTER TABLE ADD COLUMN`
for specific known columns. There is no `PRAGMA user_version`, no migration
runner, and no SQL-shape version marker distinct from `INDEXER_VERSION` (which
governs re-deriving *index data from source*, not the raw table shape). Every
migration so far has been additive (new nullable columns / new tables), so it
has been safe -- but that safety was empirically held, not tested.

These tests pin the guarantee down: seed a raw SQLite DB shaped like an *older*
install (missing the columns/tables current code adds), run current
`init_db`/indexing against it, and assert (a) no exception, (b) pre-existing
rows/data survive, and (c) the current-schema columns/tables exist afterward.

`_seed_pre_migration_db` deliberately hand-writes an early `init_db` shape rather
than importing the current one, so it stays frozen at the old schema even as the
real schema keeps evolving -- that is the whole point of a migration test. Extend
it (or add a sibling seeder) as the schema actually changes going forward.
"""

from __future__ import annotations

import sqlite3

import pytest

from neo_localmcp.retrieval import repo_memory

pytestmark = pytest.mark.retrieval


# The columns/tables current `init_db` adds on top of the pre-migration shape.
# These mirror repo_memory.init_db's ALTER TABLE / later CREATE TABLE blocks; if
# init_db grows a new additive migration, add its column/table here so this test
# keeps proving the upgrade preserves data.
_EXPECTED_FILES_ADDED_COLUMNS = {"summary_source_hash", "summary_model", "summary_prompt_version"}
_EXPECTED_TASK_QUERIES_ADDED_COLUMNS = {"retrieval_id", "term_key", "embed_model", "query_vector"}
_EXPECTED_ADDED_TABLES = {"retrieval_boost", "section_summaries", "file_embeddings"}


def _seed_pre_migration_db(db_file) -> None:
    """Create a SQLite DB shaped like an early neo-localmcp install and seed rows.

    Intentionally NOT the current schema: `files` lacks the three `summary_*`
    columns, `task_queries` lacks `retrieval_id`/`term_key`, and the later
    `retrieval_boost` / `section_summaries` tables are absent entirely -- exactly
    the shape that current `init_db` upgrades via ALTER/CREATE-IF-NOT-EXISTS.
    """
    db_file.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_file)
    try:
        conn.executescript(
            """
            CREATE TABLE metadata (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE repo_metadata (
                repo_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                PRIMARY KEY(repo_id, key)
            );
            CREATE TABLE repos (
                id TEXT PRIMARY KEY,
                root_path TEXT NOT NULL,
                remote TEXT,
                branch TEXT,
                commit_hash TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            );
            CREATE TABLE files (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                path TEXT NOT NULL,
                language TEXT,
                size_bytes INTEGER,
                sha256 TEXT NOT NULL,
                modified_at REAL,
                line_count INTEGER,
                purpose_summary TEXT,
                last_indexed_at TEXT NOT NULL,
                last_summarized_at TEXT,
                UNIQUE(repo_id, path)
            );
            CREATE TABLE symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                file_path TEXT NOT NULL,
                kind TEXT NOT NULL,
                name TEXT NOT NULL,
                signature TEXT,
                start_line INTEGER,
                end_line INTEGER,
                source TEXT NOT NULL DEFAULT 'deterministic_scan',
                created_at TEXT NOT NULL,
                UNIQUE(repo_id, file_path, kind, name, start_line)
            );
            CREATE TABLE task_queries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                query TEXT NOT NULL,
                result_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE TABLE change_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                repo_id TEXT NOT NULL,
                summary TEXT NOT NULL,
                paths_json TEXT NOT NULL,
                created_at TEXT NOT NULL
            );
            CREATE VIRTUAL TABLE repo_fts USING fts5(repo_id, kind, target, body);
            """
        )
        ts = "2026-01-01T00:00:00+00:00"
        conn.execute(
            "INSERT INTO repos(id, root_path, remote, branch, commit_hash, created_at, updated_at) VALUES(?,?,?,?,?,?,?)",
            ("legacy-repo", "/legacy/root", "git@example.com:legacy.git", "main", "abc123", ts, ts),
        )
        conn.execute(
            "INSERT INTO metadata(key, value, updated_at) VALUES(?,?,?)",
            ("indexer_version", "1.0.0", ts),
        )
        conn.execute(
            "INSERT INTO repo_metadata(repo_id, key, value, updated_at) VALUES(?,?,?,?)",
            ("legacy-repo", "indexer_version", "1.0.0", ts),
        )
        conn.execute(
            """
            INSERT INTO files(repo_id, path, language, size_bytes, sha256, modified_at, line_count, purpose_summary, last_indexed_at, last_summarized_at)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            """,
            ("legacy-repo", "src/old.py", "python", 123, "deadbeef", 1000.0, 10, "an old summary", ts, None),
        )
        conn.execute(
            "INSERT INTO symbols(repo_id, file_path, kind, name, signature, start_line, end_line, created_at) VALUES(?,?,?,?,?,?,?,?)",
            ("legacy-repo", "src/old.py", "function", "old_func", "def old_func():", 1, 5, ts),
        )
        conn.execute(
            "INSERT INTO task_queries(repo_id, query, result_json, created_at) VALUES(?,?,?,?)",
            ("legacy-repo", "find the old thing", "{}", ts),
        )
        conn.execute(
            "INSERT INTO change_events(repo_id, summary, paths_json, created_at) VALUES(?,?,?,?)",
            ("legacy-repo", "changed old.py", '["src/old.py"]', ts),
        )
        conn.commit()
    finally:
        conn.close()


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(row[1]) for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {str(row[0]) for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()}


def test_init_db_on_a_pre_migration_db_does_not_raise(tmp_path, isolated_config):
    """Upgrading an older-shaped DB in place must complete without an exception."""
    db_file = repo_memory.db_path()
    _seed_pre_migration_db(db_file)

    conn = sqlite3.connect(db_file)
    try:
        # Sanity: the seeded DB genuinely predates the current schema, so this is a
        # real upgrade path, not a no-op against an already-current DB.
        assert _EXPECTED_FILES_ADDED_COLUMNS.isdisjoint(_column_names(conn, "files"))
        assert _EXPECTED_TASK_QUERIES_ADDED_COLUMNS.isdisjoint(_column_names(conn, "task_queries"))
        assert _EXPECTED_ADDED_TABLES.isdisjoint(_table_names(conn))

        repo_memory.init_db(conn)  # Must not raise.
    finally:
        conn.close()


def test_init_db_adds_current_columns_and_tables_to_old_db(tmp_path, isolated_config):
    """After upgrade, the additive columns/tables current code relies on exist."""
    db_file = repo_memory.db_path()
    _seed_pre_migration_db(db_file)

    conn = sqlite3.connect(db_file)
    try:
        repo_memory.init_db(conn)
        assert _EXPECTED_FILES_ADDED_COLUMNS.issubset(_column_names(conn, "files"))
        assert _EXPECTED_TASK_QUERIES_ADDED_COLUMNS.issubset(_column_names(conn, "task_queries"))
        assert _EXPECTED_ADDED_TABLES.issubset(_table_names(conn))
    finally:
        conn.close()


def test_init_db_preserves_pre_existing_rows(tmp_path, isolated_config):
    """The core memory-loss guarantee: no seeded row is dropped or altered by upgrade."""
    db_file = repo_memory.db_path()
    _seed_pre_migration_db(db_file)

    conn = sqlite3.connect(db_file)
    try:
        conn.row_factory = sqlite3.Row
        repo_memory.init_db(conn)

        repo = conn.execute("SELECT * FROM repos WHERE id=?", ("legacy-repo",)).fetchone()
        assert repo is not None
        assert repo["root_path"] == "/legacy/root"
        assert repo["remote"] == "git@example.com:legacy.git"

        file_row = conn.execute("SELECT * FROM files WHERE repo_id=? AND path=?", ("legacy-repo", "src/old.py")).fetchone()
        assert file_row is not None
        assert file_row["sha256"] == "deadbeef"
        assert file_row["purpose_summary"] == "an old summary"
        # The freshly-added columns exist and default to NULL for pre-existing rows.
        assert file_row["summary_source_hash"] is None
        assert file_row["summary_model"] is None
        assert file_row["summary_prompt_version"] is None

        symbol = conn.execute("SELECT * FROM symbols WHERE repo_id=? AND name=?", ("legacy-repo", "old_func")).fetchone()
        assert symbol is not None
        assert symbol["signature"] == "def old_func():"

        task = conn.execute("SELECT * FROM task_queries WHERE repo_id=?", ("legacy-repo",)).fetchone()
        assert task is not None
        assert task["query"] == "find the old thing"
        assert task["retrieval_id"] is None  # newly-added, NULL for the legacy row
        assert task["term_key"] is None

        change = conn.execute("SELECT * FROM change_events WHERE repo_id=?", ("legacy-repo",)).fetchone()
        assert change is not None
        assert change["summary"] == "changed old.py"

        meta = conn.execute("SELECT value FROM metadata WHERE key=?", ("indexer_version",)).fetchone()
        assert meta is not None and meta["value"] == "1.0.0"
        repo_meta = conn.execute(
            "SELECT value FROM repo_metadata WHERE repo_id=? AND key=?", ("legacy-repo", "indexer_version")
        ).fetchone()
        assert repo_meta is not None and repo_meta["value"] == "1.0.0"
    finally:
        conn.close()


def test_init_db_on_pre_migration_db_is_idempotent(tmp_path, isolated_config):
    """Running the upgrade twice must not raise or duplicate the added columns."""
    db_file = repo_memory.db_path()
    _seed_pre_migration_db(db_file)

    conn = sqlite3.connect(db_file)
    try:
        repo_memory.init_db(conn)
        repo_memory.init_db(conn)  # Second pass: no-op, must not raise.
        files_columns = [str(row[1]) for row in conn.execute("PRAGMA table_info(files)").fetchall()]
        # Idempotent: each added column appears exactly once, no duplicate ALTERs.
        for column in _EXPECTED_FILES_ADDED_COLUMNS:
            assert files_columns.count(column) == 1
    finally:
        conn.close()


def test_indexing_runs_end_to_end_against_a_migrated_db(tmp_path, isolated_config):
    """Beyond schema shape: real indexing/retrieval must work on the upgraded DB.

    Proves the migrated DB is not merely structurally correct but fully usable --
    the pre-existing legacy repo's memory coexists with a freshly indexed repo,
    and features that depend on the newly-added tables/columns (retrieval memory,
    section summaries) operate without error.
    """
    db_file = repo_memory.db_path()
    _seed_pre_migration_db(db_file)

    # First contact through the real code path runs init_db (via connect()).
    conn = repo_memory.connect()
    conn.close()

    repo = tmp_path / "repo"
    (repo / "pkg").mkdir(parents=True)
    (repo / "pkg" / "new_module.py").write_text(
        "def brand_new_function():\n    return 'hello from the migrated db'\n",
        encoding="utf-8",
    )

    result = repo_memory.index_repo(str(repo), force=True)
    assert result["ok"] is True
    assert result["errors"] == 0
    assert result["indexed_or_updated"] >= 1

    # The legacy repo's seeded rows still coexist untouched in the shared DB.
    conn = repo_memory.connect()
    try:
        legacy = conn.execute("SELECT COUNT(*) FROM files WHERE repo_id=?", ("legacy-repo",)).fetchone()
        assert legacy[0] == 1
    finally:
        conn.close()

    # Retrieval works on the newly-indexed repo through the migrated schema.
    hits = repo_memory.lookup("brand_new_function", str(repo))
    assert any("brand_new_function" in s["name"] for s in hits["symbols"])

    # A feature backed by a newly-added table (retrieval_boost) operates cleanly.
    boost = repo_memory.get_boost_map(str(repo), "brand_new_function", ["pkg/new_module.py"])
    assert boost == {}  # no evidence yet, but the query path must not error
