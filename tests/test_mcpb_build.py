from __future__ import annotations

import json
import zipfile
from pathlib import Path

from neo_localmcp.installer import mcpb


def _make_source_root(tmp_path: Path, *, version: str = "9.9.9") -> Path:
    """Build a minimal source checkout the way build_mcpb expects to find one."""
    root = tmp_path / "checkout"
    staging = root / "packages" / "claude-desktop" / "mcpb"
    staging.mkdir(parents=True)
    (staging / "manifest.json").write_text(
        json.dumps({"manifest_version": "0.4", "name": "neo-localmcp", "version": version}),
        encoding="utf-8",
    )
    (staging / "server.py").write_text("from neo_localmcp.server import main\n", encoding="utf-8")
    (staging / ".mcpbignore").write_text(".venv/\n__pycache__/\n*.pyc\ntests/\n", encoding="utf-8")

    pkg = root / "neo_localmcp"
    pkg.mkdir()
    (pkg / "__init__.py").write_text(f'__version__ = "{version}"\n', encoding="utf-8")
    (pkg / "server.py").write_text("def main():\n    pass\n", encoding="utf-8")
    (pkg / "installer").mkdir()
    (pkg / "installer" / "__init__.py").write_text("", encoding="utf-8")
    # Files that .mcpbignore must exclude:
    (pkg / "__pycache__").mkdir()
    (pkg / "__pycache__" / "server.cpython-312.pyc").write_bytes(b"\x00stale")
    (pkg / "stale.pyc").write_bytes(b"\x00stale")
    (pkg / "tests").mkdir()
    (pkg / "tests" / "test_inner.py").write_text("assert True\n", encoding="utf-8")
    (pkg / ".DS_Store").write_bytes(b"\x00junk")

    (root / "README.md").write_text("# neo-localmcp\n", encoding="utf-8")
    (root / "pyproject.toml").write_text('[project]\nname = "neo-localmcp"\n', encoding="utf-8")
    return root


def _target_dir(root: Path) -> Path:
    return root / "packages" / "claude-desktop"


def test_build_writes_versioned_bundle(tmp_path):
    root = _make_source_root(tmp_path, version="9.9.9")
    written = mcpb.build_mcpb(root, "9.9.9")
    assert written == _target_dir(root) / "neo-localmcp-v9.9.9.mcpb"
    assert written.exists()


def test_bundle_contents_match_layout(tmp_path):
    root = _make_source_root(tmp_path, version="9.9.9")
    written = mcpb.build_mcpb(root, "9.9.9")
    with zipfile.ZipFile(written) as archive:
        names = {n.replace("\\", "/") for n in archive.namelist()}
        manifest = json.loads(archive.read("manifest.json"))

    # Top-level staging + repo-root inputs are present.
    assert {"manifest.json", "server.py", "README.md", "pyproject.toml"} <= names
    # Package tree is included.
    assert "neo_localmcp/__init__.py" in names
    assert "neo_localmcp/installer/__init__.py" in names
    # Manifest is copied faithfully.
    assert manifest["version"] == "9.9.9"
    # .mcpbignore rules are honored.
    assert not any("__pycache__" in n for n in names)
    assert not any(n.endswith(".pyc") for n in names)
    assert not any("/tests/" in n or n.startswith("tests/") for n in names)
    # OS junk is never shipped.
    assert not any(n.endswith(".DS_Store") for n in names)
    # The ignore file itself is an input, not shipped.
    assert ".mcpbignore" not in names


def test_second_build_does_not_overwrite(tmp_path):
    root = _make_source_root(tmp_path, version="9.9.9")
    first = mcpb.build_mcpb(root, "9.9.9")
    second = mcpb.build_mcpb(root, "9.9.9")
    third = mcpb.build_mcpb(root, "9.9.9")
    assert first == _target_dir(root) / "neo-localmcp-v9.9.9.mcpb"
    assert second == _target_dir(root) / "neo-localmcp-v9.9.9-2.mcpb"
    assert third == _target_dir(root) / "neo-localmcp-v9.9.9-3.mcpb"
    # All three coexist; nothing was overwritten.
    assert first.exists() and second.exists() and third.exists()


def test_returns_none_without_staging(tmp_path):
    root = tmp_path / "not-a-checkout"
    (root / "neo_localmcp").mkdir(parents=True)
    (root / "neo_localmcp" / "__init__.py").write_text("", encoding="utf-8")
    assert mcpb.build_mcpb(root, "9.9.9") is None


# -- wizard hook ---------------------------------------------------------- #

from neo_localmcp.installer import Operation, OperationStatus  # noqa: E402
from neo_localmcp.installer.wizard import live_backend as rb  # noqa: E402
from neo_localmcp.installer.wizard.backend import WizardState  # noqa: E402


class _Result:
    def __init__(self, op: str) -> None:
        self.status = OperationStatus.SUCCEEDED
        self.operation = Operation(op)
        self.actions = ["promoted-runtime"]
        self.warnings: list[str] = []


def _run(backend: rb.LiveBackend, state: WizardState):
    events: list = []
    outcome = backend.run_operation(state, events.append)
    return outcome, events


def test_wizard_install_surfaces_built_bundle(monkeypatch):
    backend = rb.LiveBackend()
    monkeypatch.setattr(rb, "install", lambda ctx, clean: _Result("install"))
    calls = []
    monkeypatch.setattr(rb, "build_mcpb", lambda root, version: calls.append((root, version)) or Path("/repo/neo-localmcp-v9.9.9.mcpb"))

    outcome, events = _run(backend, WizardState(operation="install"))

    assert calls, "build_mcpb was not called on a successful install"
    assert any("neo-localmcp-v9.9.9.mcpb" in line for line in outcome.detail_lines)
    assert any("Built Claude Desktop bundle" in e.message for e in events)


def test_wizard_uninstall_does_not_build(monkeypatch):
    backend = rb.LiveBackend()
    monkeypatch.setattr(rb, "uninstall", lambda ctx, delete_memory, assume_yes: _Result("uninstall"))
    called = []
    monkeypatch.setattr(rb, "build_mcpb", lambda root, version: called.append(1))

    _run(backend, WizardState(operation="uninstall"))

    assert not called, "uninstall must not build a Claude Desktop bundle"


def test_wizard_survives_build_failure(monkeypatch):
    backend = rb.LiveBackend()
    monkeypatch.setattr(rb, "install", lambda ctx, clean: _Result("install"))

    def boom(root, version):
        raise RuntimeError("disk full")

    monkeypatch.setattr(rb, "build_mcpb", boom)

    outcome, events = _run(backend, WizardState(operation="install"))

    assert outcome.ok, "a bundle-build failure must not fail the install"
    assert any("Could not build" in e.message for e in events)
