"""Cross-platform PATH guidance and opt-in update tests."""

from __future__ import annotations

from neo_localmcp.installer.paths import ManagedPaths


def _paths(tmp_path, *, platform: str = "posix") -> ManagedPaths:
    return ManagedPaths(
        root=tmp_path / ".neo-localmcp",
        platform=platform,  # type: ignore[arg-type]
        home=tmp_path,
        allow_test_root=True,
    )


def test_path_hint_uses_managed_posix_bin_directory(tmp_path) -> None:
    from neo_localmcp.installer.path import path_hint

    paths = _paths(tmp_path)

    assert path_hint(paths) == f'export PATH="{paths.executable_dir}:$PATH"'


def test_path_hint_uses_managed_windows_scripts_directory(tmp_path) -> None:
    from neo_localmcp.installer.path import path_hint

    paths = _paths(tmp_path, platform="windows")

    assert path_hint(paths) == f'setx PATH "%PATH%;{paths.executable_dir}"'


def test_append_shell_path_writes_once_only_when_confirmed(tmp_path) -> None:
    from neo_localmcp.installer.path import append_shell_path, path_hint

    paths = _paths(tmp_path)
    rc_file = paths.home / ".zshrc"

    result = append_shell_path(paths, environ={"SHELL": "/bin/zsh"})

    assert result.changed is True
    assert result.target == rc_file
    assert rc_file.read_text(encoding="utf-8") == (
        "\n# neo-localmcp PATH\n"
        f"{path_hint(paths)}\n"
    )

    repeated = append_shell_path(paths, environ={"SHELL": "/bin/zsh"})

    assert repeated.changed is False
    assert rc_file.read_text(encoding="utf-8").count("# neo-localmcp PATH") == 1


def test_add_to_path_updates_windows_user_path_once(tmp_path) -> None:
    from neo_localmcp.installer.path import add_to_path

    class _Key:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    class _Registry:
        HKEY_CURRENT_USER = object()
        KEY_READ = 1
        KEY_SET_VALUE = 2
        REG_EXPAND_SZ = 3

        def __init__(self) -> None:
            self.value = r"C:\\existing"
            self.writes: list[str] = []

        def OpenKey(self, *_args):
            return _Key()

        def QueryValueEx(self, _key, _name):
            return self.value, self.REG_EXPAND_SZ

        def SetValueEx(self, _key, _name, _reserved, _type, value):
            self.value = value
            self.writes.append(value)

    paths = _paths(tmp_path, platform="windows")
    registry = _Registry()

    result = add_to_path(paths, winreg_module=registry)

    assert result.changed is True
    assert registry.writes == [f"{r'C:\\existing'};{paths.executable_dir}"]
    assert add_to_path(paths, winreg_module=registry).changed is False
    assert len(registry.writes) == 1
