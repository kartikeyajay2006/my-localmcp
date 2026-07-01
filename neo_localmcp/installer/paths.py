"""Canonical cross-platform paths and destructive-operation safety guards."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Mapping

PlatformName = Literal["windows", "posix"]


class UnsafeManagedRoot(ValueError):
    """Raised when a destructive operation targets an unsafe directory."""


@dataclass(frozen=True)
class ManagedPaths:
    root: Path
    platform: PlatformName
    home: Path
    allow_test_root: bool = False

    def __post_init__(self) -> None:
        root = Path(self.root).expanduser()
        home = Path(self.home).expanduser()
        if self.platform not in {"windows", "posix"}:
            raise ValueError(f"Unsupported platform: {self.platform}")
        object.__setattr__(self, "root", root)
        object.__setattr__(self, "home", home)

    @classmethod
    def from_environment(
        cls,
        *,
        platform: PlatformName | None = None,
        home: Path | None = None,
        environ: Mapping[str, str] | None = None,
        allow_test_root: bool = False,
    ) -> "ManagedPaths":
        environment = os.environ if environ is None else environ
        resolved_home = Path.home() if home is None else Path(home)
        configured = environment.get("NEO_LOCALMCP_HOME")
        root = Path(configured).expanduser() if configured else resolved_home / ".neo-localmcp"
        platform_name: PlatformName = platform or ("windows" if os.name == "nt" else "posix")
        return cls(
            root=root,
            platform=platform_name,
            home=resolved_home,
            allow_test_root=allow_test_root,
        )

    @property
    def venv(self) -> Path:
        return self.root / "venv"

    @property
    def memory(self) -> Path:
        return self.root / "memory"

    @property
    def sqlite(self) -> Path:
        return self.root / "sqlite"

    @property
    def config(self) -> Path:
        return self.root / "config"

    @property
    def clients(self) -> Path:
        return self.root / "clients"

    @property
    def logs(self) -> Path:
        return self.root / "logs"

    @property
    def cache(self) -> Path:
        return self.root / "cache"

    @property
    def install_metadata(self) -> Path:
        return self.config / "install.json"

    @property
    def process_registry(self) -> Path:
        return self.cache / "processes"

    @property
    def candidate_venv(self) -> Path:
        return self.cache / "runtime-staging" / "venv"

    @property
    def executable_dir(self) -> Path:
        return self.venv / ("Scripts" if self.platform == "windows" else "bin")

    @property
    def executable_suffix(self) -> str:
        return ".exe" if self.platform == "windows" else ""

    @property
    def python_executable(self) -> Path:
        return self.executable_dir / f"python{self.executable_suffix}"

    @property
    def cli_executable(self) -> Path:
        return self.executable_dir / f"neo-localmcp{self.executable_suffix}"

    @property
    def server_executable(self) -> Path:
        return self.executable_dir / f"neo-localmcp-server{self.executable_suffix}"

    @property
    def durable_directories(self) -> tuple[Path, ...]:
        return (self.memory, self.sqlite, self.config, self.clients, self.logs)

    def ensure_directories(self) -> None:
        self.root.mkdir(parents=True, exist_ok=True)
        for directory in (*self.durable_directories, self.cache):
            directory.mkdir(parents=True, exist_ok=True)

    def validate_destructive_root(self) -> Path:
        try:
            resolved = self.root.resolve(strict=False)
            resolved_home = self.home.resolve(strict=False)
        except (OSError, RuntimeError) as exc:
            raise UnsafeManagedRoot(f"Managed root cannot be resolved safely: {exc}") from exc

        if resolved == Path(resolved.anchor):
            raise UnsafeManagedRoot(f"Refusing filesystem root: {resolved}")
        if resolved == resolved_home:
            raise UnsafeManagedRoot(f"Refusing home directory: {resolved}")
        if not self.allow_test_root and resolved.name != ".neo-localmcp":
            raise UnsafeManagedRoot(
                f"Refusing unexpected managed root (expected .neo-localmcp): {resolved}"
            )
        return resolved
