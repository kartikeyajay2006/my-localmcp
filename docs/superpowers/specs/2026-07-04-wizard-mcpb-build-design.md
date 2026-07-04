# Wizard-built `.mcpb` into the repo — design

**Date:** 2026-07-04
**Status:** approved (brainstorming), pending implementation plan

## Origin

The setup wizard does not produce the Claude Desktop `.mcpb` bundle, and its
completion output never says where such a bundle would live. Investigation
(`superpowers:systematic-debugging`) traced this to a lost step: the legacy
`_LegacyInstallers/install.ps1` (lines 208–212) copied the versioned bundle from
`packages/claude-desktop/neo-localmcp-v<version>.mcpb` to a fixed-name local copy
at `~/.neo-localmcp/neo-localmcp.mcpb`, but the stdlib Python installer/wizard that
replaced the PowerShell installers never ported any equivalent.

The owner's decision reframed the fix: **do not** reintroduce the
`~/.neo-localmcp/` copy (deemed useless). Instead, the wizard should **build** the
`.mcpb` into the repo under `packages/claude-desktop/`, versioned, **never
overwriting** an existing bundle (a reinstall keeps the old file and writes a new
one).

## Decisions (owner-confirmed)

1. **Dev-only, from a source checkout.** Build only when the wizard runs from the
   repo (the `packages/claude-desktop/mcpb/` staging inputs exist). Otherwise skip
   silently — this is a developer/release convenience, not an end-user install step.
2. **Pure Python `zipfile`.** A `.mcpb` is a plain zip archive that Claude Desktop
   reads via its root `manifest.json` (no signature or hash to reproduce), so a
   stdlib zip built to the same layout loads identically. This keeps the wizard
   dependency-free (no Node/`npx`, whose resolution is already documented as flaky
   on some hosts in `PROJECT_NOTES.md`).
3. **No-overwrite numeric counter.** Base name `neo-localmcp-v{version}.mcpb`; if it
   exists, try `neo-localmcp-v{version}-2.mcpb`, `-3`, … until a free name is found.
4. **Out of scope:** the `~/.neo-localmcp/neo-localmcp.mcpb` copy (left untouched),
   and the non-wizard `python setup.py install` CLI path (the request is scoped to
   the wizard).

## Bundle contents (must match `scripts/build-mcpb.sh`)

Verified against the committed `packages/claude-desktop/neo-localmcp-v1.1.0.mcpb`
(48 files, plain deflate zip). Top-level entries plus the package tree:

- `manifest.json` (from `packages/claude-desktop/mcpb/manifest.json`)
- `server.py` (from `packages/claude-desktop/mcpb/server.py`)
- `README.md` (repo root)
- `pyproject.toml` (repo root)
- `neo_localmcp/**` — excluding `tests/`, `__pycache__/`, `*.pyc`, `.venv/`
  (the rules in `packages/claude-desktop/mcpb/.mcpbignore`)

`.mcpbignore` itself is an input, not shipped inside the bundle.

## Components

### Component 1 — `neo_localmcp/mcpb_build.py` (new module)

```python
def build_mcpb(source_root: Path, version: str) -> Path | None:
    """Pack packages/claude-desktop/neo-localmcp-v{version}.mcpb from a source
    checkout.

    Returns the written path, or None if the mcpb/ staging inputs are absent
    (not a source checkout — dev-only, skip silently).
    """
```

Behavior:

1. Resolve `pkg_dir = source_root / "packages" / "claude-desktop"` and
   `staging = pkg_dir / "mcpb"`. If `staging / "manifest.json"` is absent, return
   `None` (dev-only gate).
2. Compute the target path: `pkg_dir / f"neo-localmcp-v{version}.mcpb"`; if it
   exists, append `-2`, `-3`, … before `.mcpb` until the name is free.
3. Build the zip (stdlib `zipfile`, `ZIP_DEFLATED`) with the contents listed above,
   honoring the `.mcpbignore` exclusion rules (parsed as simple directory/glob
   prefixes: `tests/`, `__pycache__/`, `*.pyc`, `.venv/`).
4. Return the target path.

The exclusion handling is intentionally minimal — it matches the four fixed rules
the project's `.mcpbignore` actually uses, not a general `.gitignore`-style matcher.

The staging `manifest.json` is copied as-is; its `version` is assumed to equal
`__version__` per the repo's lockstep-version convention. (No manifest patching —
kept faithful to the existing build scripts. A mismatch is a pre-existing lockstep
violation caught elsewhere, not this module's concern.)

### Component 2 — wizard hook (`neo_localmcp/wizard/real_backend.py`)

In `run_operation`, after a successful install/reinstall (not uninstall):

- Call `build_mcpb(self._source_root, self._source_version)`.
- If it returns a path: `emit(StepEvent("action", f"Built Claude Desktop bundle: {path}"))`
  and include the path in the resulting `OperationOutcome.detail_lines`, so the
  completion screen states where the file was written.
- If it returns `None`, do nothing (not a source checkout).
- Wrap in a `try/except` that degrades to a warning event — a bundle-build failure
  must never fail an otherwise-successful install (mirrors the file's existing
  "never crash the UI on a lifecycle error" convention).

Core `operations.py` lifecycle is deliberately left untouched; the build is a
wizard-layer, source-checkout concern.

### Component 3 — restore deleted staging inputs

`packages/claude-desktop/mcpb/{manifest.json,server.py,.mcpbignore}` are deleted in
the current working tree but are the build's required inputs; restore them from
`main`. Also restore the committed versioned bundles
(`neo-localmcp-v1.0.10.mcpb`, `neo-localmcp-v1.1.0.mcpb`) and
`claude_desktop_config.example.json`, since the "keep every version" policy means
prior bundles should remain.

## Testing

Unit tests for `build_mcpb` (against a temp `source_root` fixture with a minimal
`packages/claude-desktop/mcpb/` + `neo_localmcp/` + `README.md` + `pyproject.toml`):

- Produces `neo-localmcp-v{version}.mcpb`.
- A second call produces `neo-localmcp-v{version}-2.mcpb` and leaves the first
  intact (no overwrite).
- Bundle contains `manifest.json`, `server.py`, `README.md`, `pyproject.toml`, and
  `neo_localmcp/` files; excludes `tests/`, `__pycache__/`, `*.pyc`.
- Returns `None` when the `mcpb/` staging dir is absent.

Plus a wizard-level test that a successful install/reinstall surfaces the built
bundle path in the outcome (and uninstall does not build).

## Verification

- `python -m pytest -q` green.
- `python -m compileall -q neo_localmcp` clean.
- Manually diff the Python-built bundle's file list against the committed
  `neo-localmcp-v1.1.0.mcpb` to confirm content parity.
- Update `PROJECT_STATUS.md` / `PROJECT_NOTES.md` per repo convention.
