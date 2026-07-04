"""Pack a Claude Desktop ``.mcpb`` bundle from a source checkout.

A ``.mcpb`` is a plain zip archive that Claude Desktop reads via its root
``manifest.json`` -- there is no signature or hash to reproduce -- so this builds
the bundle with the stdlib :mod:`zipfile`, keeping the wizard dependency-free (no
Node/``npx``). The staged layout mirrors ``scripts/build-mcpb.sh``:

    manifest.json      <- packages/claude-desktop/mcpb/manifest.json
    server.py          <- packages/claude-desktop/mcpb/server.py
    README.md          <- repo root
    pyproject.toml     <- repo root
    neo_localmcp/**    <- repo root, honoring packages/claude-desktop/mcpb/.mcpbignore

This is a developer/release convenience: it only runs from a source checkout (where
the ``mcpb/`` staging inputs exist) and never overwrites an existing bundle.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

# The four exclusion rules the project's .mcpbignore actually uses. Kept as a fixed
# set rather than a general .gitignore matcher -- if .mcpbignore grows richer rules,
# extend this deliberately.
_EXCLUDED_DIR_NAMES = {".venv", "__pycache__", "tests"}
_EXCLUDED_SUFFIXES = (".pyc",)
# OS junk that must never be shipped (the canonical `mcpb pack` drops these too).
_EXCLUDED_NAMES = {".DS_Store"}

# Files copied verbatim from the mcpb/ staging dir into the bundle root.
_STAGING_FILES = ("manifest.json", "server.py")
# Files copied from the repo root into the bundle root.
_ROOT_FILES = ("README.md", "pyproject.toml")


def _is_excluded(relative: Path) -> bool:
    if any(part in _EXCLUDED_DIR_NAMES for part in relative.parts):
        return True
    if relative.name in _EXCLUDED_NAMES:
        return True
    return relative.name.endswith(_EXCLUDED_SUFFIXES)


def _next_free_path(package_dir: Path, version: str) -> Path:
    """Return the versioned bundle path, adding a ``-N`` counter if it is taken.

    ``neo-localmcp-v1.1.0.mcpb`` -> ``neo-localmcp-v1.1.0-2.mcpb`` -> ``-3`` ...
    Never overwrites an existing file.
    """
    base = package_dir / f"neo-localmcp-v{version}.mcpb"
    if not base.exists():
        return base
    counter = 2
    while True:
        candidate = package_dir / f"neo-localmcp-v{version}-{counter}.mcpb"
        if not candidate.exists():
            return candidate
        counter += 1


def build_mcpb(source_root: Path | str, version: str) -> Path | None:
    """Pack ``packages/claude-desktop/neo-localmcp-v{version}.mcpb`` from a checkout.

    Returns the written path, or ``None`` if the ``mcpb/`` staging inputs are absent
    (i.e. not running from a source checkout -- dev-only, caller should skip).
    """
    root = Path(source_root)
    package_dir = root / "packages" / "claude-desktop"
    staging = package_dir / "mcpb"
    if not (staging / "manifest.json").exists():
        return None

    target = _next_free_path(package_dir, version)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in _STAGING_FILES:
            source = staging / name
            if source.is_file():
                archive.write(source, name)
        for name in _ROOT_FILES:
            source = root / name
            if source.is_file():
                archive.write(source, name)
        package = root / "neo_localmcp"
        for source in sorted(package.rglob("*")):
            if not source.is_file():
                continue
            relative = source.relative_to(root)
            if _is_excluded(relative):
                continue
            archive.write(source, relative.as_posix())
    return target
