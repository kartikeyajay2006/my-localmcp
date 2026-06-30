from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import db_path, load_config

INDEXER_VERSION = "1.0.0"
from .utils import extract_symbols, git_info, language_for_path, read_text_file, rel, repo_id, repo_root_or_cwd, safe_path, scan_repo_files, sha256_file, simple_terms


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def connect() -> sqlite3.Connection:
    path = db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    init_db(conn)
    return conn


def init_db(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS repo_metadata (
            repo_id TEXT NOT NULL,
            key TEXT NOT NULL,
            value TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            PRIMARY KEY(repo_id, key)
        );
        CREATE TABLE IF NOT EXISTS repos (
            id TEXT PRIMARY KEY,
            root_path TEXT NOT NULL,
            remote TEXT,
            branch TEXT,
            commit_hash TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS files (
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
        CREATE TABLE IF NOT EXISTS symbols (
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
        CREATE TABLE IF NOT EXISTS task_queries (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id TEXT NOT NULL,
            query TEXT NOT NULL,
            result_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS change_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            repo_id TEXT NOT NULL,
            summary TEXT NOT NULL,
            paths_json TEXT NOT NULL,
            created_at TEXT NOT NULL
        );
        CREATE VIRTUAL TABLE IF NOT EXISTS repo_fts USING fts5(repo_id, kind, target, body);
        """
    )
    existing_columns = {str(row[1]) for row in conn.execute("PRAGMA table_info(files)").fetchall()}
    for column, definition in {
        "summary_source_hash": "TEXT",
        "summary_model": "TEXT",
        "summary_prompt_version": "TEXT",
    }.items():
        if column not in existing_columns:
            conn.execute(f"ALTER TABLE files ADD COLUMN {column} {definition}")
    conn.commit()



def get_repo_meta(conn: sqlite3.Connection, rid: str, key: str) -> str | None:
    row = conn.execute("SELECT value FROM repo_metadata WHERE repo_id=? AND key=?", (rid, key)).fetchone()
    return str(row["value"]) if row else None


def set_repo_meta(conn: sqlite3.Connection, rid: str, key: str, value: str) -> None:
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO repo_metadata(repo_id, key, value, updated_at)
        VALUES(?, ?, ?, ?)
        ON CONFLICT(repo_id, key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
        """,
        (rid, key, value, ts),
    )
    conn.commit()


def repo_indexer_needs_rebuild(conn: sqlite3.Connection, rid: str) -> bool:
    return get_repo_meta(conn, rid, "indexer_version") != INDEXER_VERSION


def upsert_repo(conn: sqlite3.Connection, root: Path) -> str:
    info = git_info(root)
    rid = repo_id(root)
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO repos(id, root_path, remote, branch, commit_hash, created_at, updated_at)
        VALUES(?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(id) DO UPDATE SET
            root_path=excluded.root_path,
            remote=excluded.remote,
            branch=excluded.branch,
            commit_hash=excluded.commit_hash,
            updated_at=excluded.updated_at
        """,
        (rid, str(root), info.get("remote"), info.get("branch"), info.get("commit"), ts, ts),
    )
    conn.commit()
    return rid


def index_file(conn: sqlite3.Connection, root: Path, path: Path, rid: str | None = None, force: bool = False) -> dict[str, Any]:
    rid = rid or upsert_repo(conn, root)
    relative = rel(path, root)
    stat = path.stat()
    existing = conn.execute("SELECT sha256, size_bytes, modified_at FROM files WHERE repo_id=? AND path=?", (rid, relative)).fetchone()
    if existing and not force and int(existing["size_bytes"] or -1) == stat.st_size and float(existing["modified_at"] or -1) == stat.st_mtime:
        return {"path": relative, "changed": False, "indexed": False}
    current_hash = sha256_file(path)
    if existing and existing["sha256"] == current_hash and not force:
        conn.execute(
            "UPDATE files SET size_bytes=?, modified_at=? WHERE repo_id=? AND path=?",
            (stat.st_size, stat.st_mtime, rid, relative),
        )
        conn.commit()
        return {"path": relative, "changed": False, "indexed": False}

    text = read_text_file(path, int(load_config().get("repo", {}).get("summary_max_chars", 80_000)))
    lang = language_for_path(path)
    line_count = len(text.splitlines())
    ts = now_iso()
    conn.execute(
        """
        INSERT INTO files(repo_id, path, language, size_bytes, sha256, modified_at, line_count, last_indexed_at)
        VALUES(?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(repo_id, path) DO UPDATE SET
            language=excluded.language,
            size_bytes=excluded.size_bytes,
            sha256=excluded.sha256,
            modified_at=excluded.modified_at,
            line_count=excluded.line_count,
            last_indexed_at=excluded.last_indexed_at,
            purpose_summary=NULL,
            last_summarized_at=NULL,
            summary_source_hash=NULL,
            summary_model=NULL,
            summary_prompt_version=NULL
        """,
        (rid, relative, lang, stat.st_size, current_hash, stat.st_mtime, line_count, ts),
    )
    conn.execute("DELETE FROM symbols WHERE repo_id=? AND file_path=?", (rid, relative))
    conn.execute("DELETE FROM repo_fts WHERE repo_id=? AND (target=? OR target LIKE ?)", (rid, relative, relative + ":%"))
    symbols = extract_symbols(text, lang)
    for sym in symbols:
        conn.execute(
            """
            INSERT OR IGNORE INTO symbols(repo_id, file_path, kind, name, signature, start_line, end_line, created_at)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (rid, relative, sym["kind"], sym["name"], sym.get("signature"), sym.get("start_line"), sym.get("end_line"), ts),
        )
    conn.execute("INSERT INTO repo_fts(repo_id, kind, target, body) VALUES(?, 'file', ?, ?)", (rid, relative, f"{relative} {lang}"))
    for sym in symbols[:120]:
        conn.execute("INSERT INTO repo_fts(repo_id, kind, target, body) VALUES(?, 'symbol', ?, ?)", (rid, f"{relative}:{sym['name']}", f"{sym['kind']} {sym['name']} {sym.get('signature','')} {relative}"))
    conn.commit()
    return {"path": relative, "changed": True, "indexed": True, "symbols": len(symbols)}


def _delete_indexed_path(conn: sqlite3.Connection, rid: str, relative: str) -> None:
    conn.execute("DELETE FROM symbols WHERE repo_id=? AND file_path=?", (rid, relative))
    conn.execute("DELETE FROM repo_fts WHERE repo_id=? AND (target=? OR target LIKE ?)", (rid, relative, relative + ":%"))
    conn.execute("DELETE FROM files WHERE repo_id=? AND path=?", (rid, relative))


def index_repo(repo_root: str | Path | None = None, max_files: int | None = None, force: bool = False) -> dict[str, Any]:
    root = repo_root_or_cwd(repo_root)
    conn = connect()
    rid = upsert_repo(conn, root)
    previous_indexer_version = get_repo_meta(conn, rid, "indexer_version")
    indexer_version_changed = previous_indexer_version != INDEXER_VERSION
    effective_force = force or indexer_version_changed
    files, eligible_files, index_complete = scan_repo_files(root, max_files=max_files)
    indexed = skipped = errors = 0
    changed_paths: list[str] = []
    for path in files:
        try:
            result = index_file(conn, root, path, rid=rid, force=effective_force)
            if result.get("indexed"):
                indexed += 1
                changed_paths.append(result["path"])
            else:
                skipped += 1
        except Exception:
            errors += 1
    selected_paths = {rel(path, root) for path in files}
    removed_paths: list[str] = []
    if errors == 0 and index_complete:
        stored_paths = {str(row["path"]) for row in conn.execute("SELECT path FROM files WHERE repo_id=?", (rid,)).fetchall()}
        removed_paths = sorted(stored_paths - selected_paths)
        for relative in removed_paths:
            _delete_indexed_path(conn, rid, relative)
        conn.commit()
    if errors == 0:
        set_repo_meta(conn, rid, "indexer_version", INDEXER_VERSION)
        set_repo_meta(conn, rid, "eligible_files", str(eligible_files))
        set_repo_meta(conn, rid, "index_complete", "true" if index_complete else "false")
        set_repo_meta(conn, rid, "indexed_branch", str(git_info(root).get("branch") or ""))
    return {
        "ok": errors == 0,
        "repo_root": str(root),
        "repo_id": rid,
        "files_seen": len(files),
        "indexed_files": len(selected_paths),
        "eligible_files": eligible_files,
        "index_complete": index_complete,
        "indexed_or_updated": indexed,
        "unchanged": skipped,
        "removed": len(removed_paths),
        "removed_paths": removed_paths[:100],
        "errors": errors,
        "changed_paths": changed_paths[:100],
        "db_path": str(db_path()),
        "indexer_version": INDEXER_VERSION,
        "previous_indexer_version": previous_indexer_version,
        "indexer_version_changed": indexer_version_changed,
        "forced": effective_force,
    }


def status(repo_root: str | Path | None = None) -> dict[str, Any]:
    root = repo_root_or_cwd(repo_root)
    conn = connect()
    rid = upsert_repo(conn, root)
    counts: dict[str, int] = {}
    for table in ["files", "symbols", "task_queries", "change_events"]:
        row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE repo_id=?", (rid,)).fetchone()
        counts[table] = int(row["n"] if row else 0)
    stale = 0
    missing = 0
    for row in conn.execute("SELECT path, size_bytes, modified_at FROM files WHERE repo_id=?", (rid,)).fetchall():
        p = root / row["path"]
        if not p.exists():
            missing += 1
        else:
            try:
                stat = p.stat()
                if stat.st_size != int(row["size_bytes"] or -1) or stat.st_mtime != float(row["modified_at"] or -1):
                    stale += 1
            except OSError:
                stale += 1
    previous_indexer_version = get_repo_meta(conn, rid, "indexer_version")
    git = git_info(root)
    indexed_branch = get_repo_meta(conn, rid, "indexed_branch")
    indexed_files = counts.get("files", 0)
    eligible = int(get_repo_meta(conn, rid, "eligible_files") or indexed_files)
    complete = get_repo_meta(conn, rid, "index_complete") == "true"
    return {
        "repo_root": str(root), "repo_id": rid, "db_path": str(db_path()), "counts": counts,
        "indexed_files": indexed_files, "eligible_files": eligible, "index_complete": complete,
        "stale_files": stale, "missing_files": missing, "git": git,
        "branch_changed": indexed_branch is not None and indexed_branch != str(git.get("branch") or ""),
        "indexer_version": INDEXER_VERSION, "stored_indexer_version": previous_indexer_version,
        "indexer_rebuild_recommended": previous_indexer_version != INDEXER_VERSION,
    }


def refresh(repo_root: str | Path | None = None, force: bool = False, max_files: int | None = None) -> dict[str, Any]:
    # Fresh-install baseline: deterministic refresh is just hash-aware re-index.
    return index_repo(repo_root, max_files=max_files, force=force)


def lookup(query: str, repo_root: str | Path | None = None, limit: int = 20) -> dict[str, Any]:
    root = repo_root_or_cwd(repo_root)
    conn = connect()
    rid = upsert_repo(conn, root)
    q = query.strip().replace('"', " ")
    rows: list[dict[str, Any]] = []
    if q:
        try:
            for row in conn.execute(
                """
                SELECT kind, target, body, rank
                FROM repo_fts
                WHERE repo_id=?
                  AND kind IN ('file', 'symbol', 'summary')
                  AND repo_fts MATCH ?
                ORDER BY rank, kind, target, body
                LIMIT ?
                """,
                (rid, q, limit),
            ).fetchall():
                rows.append(dict(row))
        except sqlite3.OperationalError:
            like = f"%{q}%"
            for row in conn.execute("SELECT 'file' AS kind, path AS target, path AS body FROM files WHERE repo_id=? AND path LIKE ? ORDER BY path LIMIT ?", (rid, like, limit)).fetchall():
                rows.append(dict(row))
    like = f"%{query}%"
    symbols = [dict(row) for row in conn.execute("SELECT file_path, kind, name, signature, start_line, end_line FROM symbols WHERE repo_id=? AND (name LIKE ? OR signature LIKE ?) ORDER BY file_path, start_line, name LIMIT ?", (rid, like, like, limit)).fetchall()]
    return {"repo_root": str(root), "repo_id": rid, "query": query, "hits": rows, "symbols": symbols}


def file_context(path: str, repo_root: str | Path | None = None, around_line: int | None = None, context_lines: int = 40, symbol_limit: int = 25) -> dict[str, Any]:
    root = repo_root_or_cwd(repo_root)
    p = safe_path(path, root)
    conn = connect()
    rid = upsert_repo(conn, root)
    relative = rel(p, root)
    if p.exists():
        row = conn.execute("SELECT sha256 FROM files WHERE repo_id=? AND path=?", (rid, relative)).fetchone()
        current = sha256_file(p)
        if not row or row["sha256"] != current:
            index_file(conn, root, p, rid=rid, force=True)
    file_row = conn.execute("SELECT * FROM files WHERE repo_id=? AND path=?", (rid, relative)).fetchone()
    symbols = [dict(row) for row in conn.execute("SELECT kind, name, signature, start_line, end_line FROM symbols WHERE repo_id=? AND file_path=? ORDER BY start_line, name LIMIT ?", (rid, relative, max(0, symbol_limit))).fetchall()]
    excerpt = None
    if around_line and p.exists():
        lines = read_text_file(p, 500_000).splitlines()
        requested = max(1, int(context_lines))
        before = (requested - 1) // 2
        start = max(1, int(around_line) - before)
        end = min(len(lines), start + requested - 1)
        start = max(1, end - requested + 1)
        excerpt = {"start_line": start, "end_line": end, "text": "\n".join(f"{i}: {lines[i-1]}" for i in range(start, end + 1))}
    fresh = False
    if file_row and p.exists():
        stat = p.stat()
        fresh = stat.st_size == int(file_row["size_bytes"] or -1) and stat.st_mtime == float(file_row["modified_at"] or -1)
    return {"repo_root": str(root), "path": relative, "exists": p.exists(), "file": dict(file_row) if file_row else None, "fresh": fresh, "symbols": symbols, "excerpt": excerpt}


def file_excerpts(ranges: list[dict[str, Any]], repo_root: str | Path | None = None, max_chars: int = 20_000) -> dict[str, Any]:
    root = repo_root_or_cwd(repo_root)
    remaining = max(256, int(max_chars))
    excerpts: list[dict[str, Any]] = []
    for item in ranges[:20]:
        path = str(item.get("path") or "")
        if not path or remaining <= 0:
            break
        p = safe_path(path, root)
        if not p.exists():
            excerpts.append({"path": path, "exists": False})
            continue
        lines = read_text_file(p, 1_000_000).splitlines()
        start = max(1, int(item.get("start_line") or 1))
        end = min(len(lines), int(item.get("end_line") or start + 39))
        if end < start:
            start, end = end, start
        text = "\n".join(f"{i}: {lines[i-1]}" for i in range(start, end + 1))
        if len(text) > remaining:
            text = text[:remaining].rsplit("\n", 1)[0] + "\n...[budget exhausted]"
        excerpts.append({"path": rel(p, root), "exists": True, "start_line": start, "end_line": end, "sha256": sha256_file(p), "text": text})
        remaining -= len(text)
    return {"repo_root": str(root), "max_chars": max_chars, "chars_returned": max_chars - remaining, "excerpts": excerpts}


def store_summary(path: str, summary: str, model: str, prompt_version: str, repo_root: str | Path | None = None) -> dict[str, Any]:
    root = repo_root_or_cwd(repo_root)
    p = safe_path(path, root)
    conn = connect()
    rid = upsert_repo(conn, root)
    relative = rel(p, root)
    source_hash = sha256_file(p)
    conn.execute("DELETE FROM repo_fts WHERE repo_id=? AND kind='summary' AND target=?", (rid, relative))
    conn.execute(
        "UPDATE files SET purpose_summary=?, last_summarized_at=?, summary_source_hash=?, summary_model=?, summary_prompt_version=? WHERE repo_id=? AND path=?",
        (summary[:6000], now_iso(), source_hash, model, prompt_version, rid, relative),
    )
    conn.execute("INSERT INTO repo_fts(repo_id, kind, target, body) VALUES(?, 'summary', ?, ?)", (rid, relative, f"{relative}\n{summary[:6000]}"))
    conn.commit()
    return {"path": relative, "source_hash": source_hash, "model": model, "prompt_version": prompt_version}


def record_task_query(query: str, result: dict[str, Any], repo_root: str | Path | None = None) -> None:
    root = repo_root_or_cwd(repo_root)
    conn = connect()
    rid = upsert_repo(conn, root)
    conn.execute("INSERT INTO task_queries(repo_id, query, result_json, created_at) VALUES(?, ?, ?, ?)", (rid, query, json.dumps(result), now_iso()))
    conn.commit()


def record_change(summary: str, paths: list[str], repo_root: str | Path | None = None) -> dict[str, Any]:
    root = repo_root_or_cwd(repo_root)
    conn = connect()
    rid = upsert_repo(conn, root)
    normalized = []
    for path in paths:
        try:
            p = safe_path(path, root)
            normalized.append(rel(p, root))
            if p.exists():
                index_file(conn, root, p, rid=rid, force=True)
            else:
                _delete_indexed_path(conn, rid, rel(p, root))
        except Exception:
            normalized.append(path)
    conn.execute("INSERT INTO change_events(repo_id, summary, paths_json, created_at) VALUES(?, ?, ?, ?)", (rid, summary, json.dumps(normalized), now_iso()))
    conn.commit()
    return {"ok": True, "repo_root": str(root), "paths_reindexed": normalized, "summary": summary}


def list_indexed_files(repo_root: str | Path | None = None) -> list[str]:
    root = repo_root_or_cwd(repo_root)
    conn = connect()
    rid = upsert_repo(conn, root)
    return [str(row["path"]) for row in conn.execute("SELECT path FROM files WHERE repo_id=? ORDER BY path", (rid,)).fetchall()]


def symbols_for_files(paths: list[str], repo_root: str | Path | None = None, max_per_file: int = 12) -> dict[str, list[dict[str, Any]]]:
    root = repo_root_or_cwd(repo_root)
    conn = connect()
    rid = upsert_repo(conn, root)
    out: dict[str, list[dict[str, Any]]] = {}
    for path in paths:
        out[path] = [dict(row) for row in conn.execute(
            "SELECT kind, name, signature, start_line, end_line FROM symbols WHERE repo_id=? AND file_path=? ORDER BY start_line, name LIMIT ?",
            (rid, path, max_per_file),
        ).fetchall()]
    return out


def reset_repo(repo_root: str | Path | None = None) -> dict[str, Any]:
    """Delete only the current repo's indexed context from the shared DB."""
    root = repo_root_or_cwd(repo_root)
    conn = connect()
    rid = repo_id(root)
    counts_before: dict[str, int] = {}
    for table in ["files", "symbols", "task_queries", "change_events", "repo_metadata", "repos"]:
        if table == "repos":
            row = conn.execute("SELECT COUNT(*) AS n FROM repos WHERE id=?", (rid,)).fetchone()
        else:
            row = conn.execute(f"SELECT COUNT(*) AS n FROM {table} WHERE repo_id=?", (rid,)).fetchone()
        counts_before[table] = int(row["n"] if row else 0)
    conn.execute("DELETE FROM files WHERE repo_id=?", (rid,))
    conn.execute("DELETE FROM symbols WHERE repo_id=?", (rid,))
    conn.execute("DELETE FROM task_queries WHERE repo_id=?", (rid,))
    conn.execute("DELETE FROM change_events WHERE repo_id=?", (rid,))
    conn.execute("DELETE FROM repo_metadata WHERE repo_id=?", (rid,))
    conn.execute("DELETE FROM repo_fts WHERE repo_id=?", (rid,))
    conn.execute("DELETE FROM repos WHERE id=?", (rid,))
    conn.commit()
    return {"ok": True, "repo_root": str(root), "repo_id": rid, "deleted_counts": counts_before, "db_path": str(db_path())}


def reset_all() -> dict[str, Any]:
    """Delete the whole repo context database; keep config and installed client setup."""
    path = db_path()
    deleted: list[str] = []
    errors: list[str] = []
    for candidate in [path, Path(str(path) + "-wal"), Path(str(path) + "-shm")]:
        try:
            if candidate.exists():
                candidate.unlink()
                deleted.append(str(candidate))
        except OSError as exc:
            errors.append(f"{candidate}: {exc}")
    return {"ok": not errors, "db_path": str(path), "deleted": deleted, "errors": errors, "note": "config.yaml and installed MCP client configs were not removed"}
