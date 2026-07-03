from __future__ import annotations

from pathlib import Path

import pytest

from neo_localmcp.installer.paths import ManagedPaths, UnsafeManagedRoot


def test_posix_layout_uses_one_venv_and_durable_subdirectories(tmp_path: Path) -> None:
    root = tmp_path / ".neo-localmcp"
    paths = ManagedPaths(root=root, platform="posix", home=tmp_path)

    assert paths.venv == root / "venv"
    assert paths.memory == root / "memory"
    assert paths.sqlite == root / "sqlite"
    assert paths.config == root / "config"
    assert paths.clients == root / "clients"
    assert paths.logs == root / "logs"
    assert paths.cache == root / "cache"
    assert paths.install_metadata == root / "config" / "install.json"
    assert paths.process_registry == root / "cache" / "processes"
    assert paths.candidate_venv == root / "cache" / "runtime-staging" / "venv"
    assert paths.cli_executable == root / "venv" / "bin" / "neo-localmcp"
    assert paths.server_executable == root / "venv" / "bin" / "neo-localmcp-server"


def test_windows_layout_uses_scripts_executables(tmp_path: Path) -> None:
    root = tmp_path / ".neo-localmcp"
    paths = ManagedPaths(root=root, platform="windows", home=tmp_path)

    assert paths.cli_executable == root / "venv" / "Scripts" / "neo-localmcp.exe"
    assert paths.server_executable == root / "venv" / "Scripts" / "neo-localmcp-server.exe"
    assert paths.python_executable == root / "venv" / "Scripts" / "python.exe"


def test_from_environment_prefers_explicit_managed_home(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = tmp_path / ".neo-localmcp"
    monkeypatch.setenv("NEO_LOCALMCP_HOME", str(root))

    paths = ManagedPaths.from_environment(platform="posix", home=tmp_path)

    assert paths.root == root


def test_ensure_directories_never_creates_venv(tmp_path: Path) -> None:
    paths = ManagedPaths(
        root=tmp_path / ".neo-localmcp", platform="posix", home=tmp_path
    )

    paths.ensure_directories()

    assert paths.root.is_dir()
    assert all(path.is_dir() for path in paths.durable_directories)
    assert paths.cache.is_dir()
    assert not paths.venv.exists()


@pytest.mark.parametrize("unsafe", [Path("/"), Path("."), Path("not-neo")])
def test_destructive_validation_refuses_unsafe_roots(
    unsafe: Path, tmp_path: Path
) -> None:
    paths = ManagedPaths(root=unsafe, platform="posix", home=tmp_path)

    with pytest.raises(UnsafeManagedRoot):
        paths.validate_destructive_root()


def test_destructive_validation_refuses_home_directory(tmp_path: Path) -> None:
    paths = ManagedPaths(root=tmp_path, platform="posix", home=tmp_path)

    with pytest.raises(UnsafeManagedRoot, match="home directory"):
        paths.validate_destructive_root()


def test_destructive_validation_refuses_symlink_resolving_to_home(tmp_path: Path) -> None:
    link = tmp_path.parent / ".neo-localmcp"
    try:
        link.symlink_to(tmp_path, target_is_directory=True)
    except FileExistsError:
        pytest.skip("shared temporary parent already contains the safety-test link")

    paths = ManagedPaths(root=link, platform="posix", home=tmp_path)
    try:
        with pytest.raises(UnsafeManagedRoot, match="home directory"):
            paths.validate_destructive_root()
    finally:
        link.unlink(missing_ok=True)


def test_destructive_validation_accepts_expected_root(tmp_path: Path) -> None:
    root = tmp_path / ".neo-localmcp"
    paths = ManagedPaths(root=root, platform="posix", home=tmp_path)

    assert paths.validate_destructive_root() == root.resolve()


def test_test_override_still_refuses_filesystem_root(tmp_path: Path) -> None:
    paths = ManagedPaths(
        root=Path("/"),
        platform="posix",
        home=tmp_path,
        allow_test_root=True,
    )

    with pytest.raises(UnsafeManagedRoot):
        paths.validate_destructive_root()
