from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from neo_localmcp.installer import migration
from neo_localmcp.installer.migration import apply_migration, plan_migration
from neo_localmcp.installer.paths import ManagedPaths


def _paths(tmp_path: Path) -> ManagedPaths:
    return ManagedPaths(
        root=tmp_path / ".neo-localmcp",
        platform="posix",
        home=tmp_path,
    )


def _seed_legacy_layout(paths: ManagedPaths) -> None:
    paths.root.mkdir(parents=True)
    legacy_db = paths.root / "repo-context.sqlite"
    connection = sqlite3.connect(legacy_db)
    connection.execute("CREATE TABLE retrieval_boost (task TEXT PRIMARY KEY, shown INTEGER)")
    connection.execute("INSERT INTO retrieval_boost VALUES ('repeat task', 7)")
    connection.commit()
    connection.close()
    (paths.root / "repo-context.sqlite-wal").write_bytes(b"wal-bytes")
    (paths.root / "repo-context.sqlite-shm").write_bytes(b"shm-bytes")
    (paths.root / "config.yaml").write_text(
        json.dumps({"memory": {"db_path": str(legacy_db)}}),
        encoding="utf-8",
    )
    (paths.root / "servers").mkdir()
    (paths.root / "servers" / "123.json").write_text("{}", encoding="utf-8")
    (paths.root / "ollama-supervisor.json").write_text("{}", encoding="utf-8")
    (paths.root / ".venv-nlm-v1.0.9").mkdir()
    (paths.root / "venvs").mkdir()
    (paths.root / "bin").mkdir()
    (paths.root / "current-venv.txt").write_text("old", encoding="utf-8")
    (paths.root / "neo-localmcp.mcpb").write_bytes(b"bundle")
    (paths.root / "user-notes.txt").write_text("keep me", encoding="utf-8")


def test_plan_classifies_known_paths_without_mutating(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _seed_legacy_layout(paths)
    before = sorted(path.relative_to(paths.root) for path in paths.root.rglob("*"))

    plan = plan_migration(paths)

    after = sorted(path.relative_to(paths.root) for path in paths.root.rglob("*"))
    assert before == after
    assert {(action.kind, action.source.name) for action in plan.actions} >= {
        ("move", "config.yaml"),
        ("move", "repo-context.sqlite"),
        ("move", "servers"),
        ("move", "ollama-supervisor.json"),
        ("move", "neo-localmcp.mcpb"),
        ("discard", ".venv-nlm-v1.0.9"),
        ("discard", "venvs"),
        ("discard", "bin"),
        ("discard", "current-venv.txt"),
    }
    assert plan.unknown_paths == (paths.root / "user-notes.txt",)
    assert plan.conflicts == ()


def test_canonical_venv_is_recognized_and_never_discarded(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.venv.mkdir(parents=True)

    plan = plan_migration(paths)

    assert paths.venv in plan.recognized_paths
    assert all(action.source != paths.venv for action in plan.actions)


def test_collision_stops_before_any_mutation(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    paths.root.mkdir(parents=True)
    legacy = paths.root / "config.yaml"
    legacy.write_text("legacy", encoding="utf-8")
    paths.config.mkdir(parents=True)
    paths.config.joinpath("config.yaml").write_text("canonical", encoding="utf-8")

    plan = plan_migration(paths)
    result = apply_migration(plan, processes_stopped=True)

    assert len(plan.conflicts) == 1
    assert result.applied is False
    assert result.error == "migration_conflicts"
    assert legacy.read_text(encoding="utf-8") == "legacy"
    assert paths.config.joinpath("config.yaml").read_text(encoding="utf-8") == "canonical"


def test_sqlite_and_runtime_migration_requires_stopped_processes(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _seed_legacy_layout(paths)

    result = apply_migration(plan_migration(paths), processes_stopped=False)

    assert result.applied is False
    assert result.error == "processes_must_be_stopped"
    assert (paths.root / "repo-context.sqlite").exists()
    assert (paths.root / ".venv-nlm-v1.0.9").exists()


def test_apply_preserves_database_rows_and_companion_bytes(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _seed_legacy_layout(paths)
    legacy_db = paths.root / "repo-context.sqlite"
    original_db = legacy_db.read_bytes()
    original_wal = (paths.root / "repo-context.sqlite-wal").read_bytes()
    original_shm = (paths.root / "repo-context.sqlite-shm").read_bytes()

    result = apply_migration(plan_migration(paths), processes_stopped=True)

    assert result.applied is True
    assert result.rolled_back is False
    assert paths.sqlite.joinpath("repo-context.sqlite").read_bytes() == original_db
    assert paths.sqlite.joinpath("repo-context.sqlite-wal").read_bytes() == original_wal
    assert paths.sqlite.joinpath("repo-context.sqlite-shm").read_bytes() == original_shm
    connection = sqlite3.connect(paths.sqlite / "repo-context.sqlite")
    assert connection.execute("SELECT task, shown FROM retrieval_boost").fetchone() == (
        "repeat task",
        7,
    )
    connection.close()


def test_apply_moves_state_normalizes_config_and_preserves_unknown_files(
    tmp_path: Path,
) -> None:
    paths = _paths(tmp_path)
    _seed_legacy_layout(paths)

    result = apply_migration(plan_migration(paths), processes_stopped=True)

    config_payload = json.loads(
        paths.config.joinpath("config.yaml").read_text(encoding="utf-8")
    )
    assert result.applied is True
    assert config_payload["memory"]["db_path"] == str(
        paths.sqlite / "repo-context.sqlite"
    )
    assert (paths.process_registry / "servers" / "123.json").exists()
    assert (paths.cache / "ollama" / "supervisor.json").exists()
    assert (paths.cache / "packages" / "neo-localmcp.mcpb").read_bytes() == b"bundle"
    assert (paths.root / "user-notes.txt").read_text(encoding="utf-8") == "keep me"
    assert not (paths.root / ".venv-nlm-v1.0.9").exists()
    assert not (paths.root / "venvs").exists()
    assert not (paths.root / "bin").exists()
    assert not (paths.root / "current-venv.txt").exists()


def test_move_failure_rolls_back_every_completed_move(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    paths = _paths(tmp_path)
    paths.root.mkdir(parents=True)
    legacy_config = paths.root / "config.yaml"
    legacy_db = paths.root / "repo-context.sqlite"
    legacy_config.write_text("{}", encoding="utf-8")
    legacy_db.write_bytes(b"database")
    real_move = migration._move_path
    calls = 0

    def fail_second_move(source: Path, destination: Path) -> None:
        nonlocal calls
        calls += 1
        if calls == 2:
            raise OSError("injected move failure")
        real_move(source, destination)

    monkeypatch.setattr(migration, "_move_path", fail_second_move)

    result = apply_migration(plan_migration(paths), processes_stopped=True)

    assert result.applied is False
    assert result.rolled_back is True
    assert "injected move failure" in str(result.error)
    assert legacy_config.exists()
    assert legacy_db.exists()
    assert not paths.config.joinpath("config.yaml").exists()
    assert not paths.sqlite.joinpath("repo-context.sqlite").exists()


def test_applied_migration_is_idempotent(tmp_path: Path) -> None:
    paths = _paths(tmp_path)
    _seed_legacy_layout(paths)
    first = apply_migration(plan_migration(paths), processes_stopped=True)

    second_plan = plan_migration(paths)
    second = apply_migration(second_plan, processes_stopped=True)

    assert first.applied is True
    assert second_plan.actions == ()
    assert second_plan.conflicts == ()
    assert second.applied is True
    assert second.moved == ()
    assert second.discarded == ()
