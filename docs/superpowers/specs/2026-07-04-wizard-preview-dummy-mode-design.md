# Wizard: mid-session Preview Dummy toggle, persisted preview state, colorization

Date: 2026-07-04
Status: approved, ready for planning

## Origin

`setup_wizard.py` already has a side-effect-free `FakeBackend` behind `--fake`,
used to walk the whole flow safely. Two gaps prompted this change:

1. There's no way to enter dummy/preview mode *after* the wizard has already
   started (only via the `--fake` launch flag) and no persistent visual
   confirmation once you're in it — a developer could lose track of whether
   they're looking at a real or simulated run.
2. `FakeBackend`'s simulated state (installed? which clients? which Ollama
   models?) is purely in-memory per process (seeded once from
   `NEO_LOCALMCP_WIZARD_FAKE_STATE`), so re-running `--fake` always starts
   from the same canned state and never reflects what a *previous* simulated
   install/uninstall would have left behind.

## Scope

Three independent, additive changes to `neo_localmcp/wizard/`:

- **A. Mid-session toggle.** At the main menu only, typing `d`/`dummy`
  instead of a number switches the running wizard from the real backend to
  `FakeBackend` for the rest of the process. One-way (no toggling back off).
- **B. Persisted preview state file.** `FakeBackend` reads/writes a
  human-readable JSON file describing its simulated install state, so
  running the preview through an install, then later an uninstall, actually
  shows the right "before" state each time — without ever touching any real
  managed root, venv, or client config.
- **D. Colorization.** Structural chrome (headers, section rules, the
  `[Preview Dummy]` tag) and semantic/status text (success, error,
  destructive-confirm) get ANSI color, applied wizard-wide (not just the
  dummy path), with automatic fallback to plain text when unsupported.

A and B are scoped to the fake/dummy path only; D touches both real and fake
runs since it's purely presentational. The real backend and real lifecycle
code are untouched by all three.

## A. Mid-session `D` toggle

- `_ask_int` gains an optional `allow_dummy_toggle: bool = False` parameter.
  When set, before the digit/back checks it also accepts `raw.strip().lower()
  in {"d", "dummy"}` and raises a new `_ToggleDummy` exception (alongside the
  existing `_GoBack`).
- Only `_main_menu()` passes `allow_dummy_toggle=True` — no other prompt
  recognizes the toggle, so it can't accidentally fire mid-phase.
- `_main_menu()` catches `_ToggleDummy`: it constructs a fresh
  `FakeBackend()`, assigns it to `self.backend`, sets `self.fake = True`,
  reloads `self.detected`/`self.prefs` from the new backend, and redraws the
  menu. No operation phases are in flight at the main menu, so there's no
  state to reset.
- The menu's numeric hint text gets `(or d for preview dummy mode)` appended
  so the toggle is discoverable without prior knowledge.
- This is one-way for the process lifetime: once `self.fake` is true there is
  no path back to the real backend short of restarting the wizard.

## B. Unified `[Preview Dummy]` label

- `_header()`'s title suffix changes from `"(SIMULATION - nothing changes)"`
  to `"[Preview Dummy]"`, shown whenever `self.fake` is true — identical
  whether set via `--fake` at launch or the mid-session `D` toggle. This is
  the persistent visual confirmation that no option chosen from this point
  can be saved or acted on for real.

## C. Persisted preview state file

**Location:** `.wizard_preview/state.json` at the repo root. Added to
`.gitignore`. Plain `json.dump(..., indent=2)` — no new dependency, and still
readable directly in an editor.

**Shape:**

```json
{
  "installed": false,
  "installed_version": null,
  "registered_clients": [],
  "fast_model": "qwen3:8b",
  "summary_model": "qwen3-coder:30b",
  "base_url": "http://127.0.0.1:11434",
  "prefs": {}
}
```

**Load/seed (`FakeBackend.__init__`):**

- If `.wizard_preview/state.json` exists and parses as valid JSON matching
  this shape, load it as the starting simulated state. This is what makes an
  install "stick" so a later `--fake` uninstall run sees the right before-state.
- Otherwise (missing, unreadable, or blank), seed from
  `NEO_LOCALMCP_WIZARD_FAKE_STATE` exactly as today (`absent` default,
  `healthy` for a pre-installed scenario), and immediately write that seed out
  as the initial file so it exists for next time.
- A corrupt/partially-written file is treated the same as missing (falls back
  to env-seeded defaults) rather than raising — this is a throwaway dev
  preview aid, not a durable store.

**Write-back:** every non-dry-run mutating call rewrites the whole file from
the backend's post-mutation in-memory state:

- `_simulate_install_like` (install/reinstall path) — writes `installed`,
  `installed_version`, `registered_clients`, and (if `configure_ollama`) the
  model fields.
- `apply_ollama_config` — writes the model + base_url fields.
- `apply_client_changes` — writes `registered_clients`.
- `_simulate_uninstall`:
  - **Runtime-only** (`state.full_wipe` false): clears `installed` (→
    `false`) and `installed_version` (→ `null`) and `registered_clients` (→
    `[]`), matching "clients disconnected, runtime gone" — but preserves
    `fast_model`/`summary_model`/`base_url`/`prefs` as-is, matching the real
    uninstall's guarantee that durable data (which includes Ollama config)
    survives a runtime-only removal.
  - **Full wipe** (`state.full_wipe` true): resets the entire file back to
    the blank/`absent` shape (all fields as if freshly seeded with
    `NEO_LOCALMCP_WIZARD_FAKE_STATE=absent`), matching "everything under the
    managed root is gone."
- Dry runs (`state.dry_run`) never touch the file — nothing "changed."

**`load_prefs`/`save_prefs`:** currently backed by an in-memory `self._prefs`
dict seeded at construction; these now read from/write into the same loaded
state and get included in the write-back so `last_clients`/model prefs
persist across preview runs too.

## D. Colorization

**Goal:** apply color to both structural chrome and semantic/status output
across the whole wizard, for scannability — not just the dummy-mode path.

**Implementation:** stdlib-only, raw ANSI escape codes — no new dependency,
keeping the existing "no TUI toolkit" convention intact. A small
`_ansi.py` (or a section in `console.py`) defines:

- On import/startup, enable ANSI processing on Windows consoles that don't
  have it by default (a one-line `os.system("")` call is sufficient to
  trigger this on modern Windows Terminal/cmd.exe; no `ctypes`/`ansicon`
  dependency needed).
- A `_color(text, code)` helper and named wrappers (`_bold`, `_dim`, `_cyan`,
  `_yellow`, `_green`, `_red`) that wrap `text` in the corresponding ANSI
  escape and reset sequence.
- A module-level `_COLOR_ENABLED` flag computed once at startup:
  `False` if `NO_COLOR` env var is set (any value, per the no-color.org
  convention) OR `sys.stdout.isatty()` is `False` (covers piped test input
  and redirected output); `True` otherwise. When disabled, every color
  wrapper is a no-op passthrough, so piped/test output is unaffected byte
  for byte except for the actual text.

**Palette (roles, not literal hex — standard 16-color ANSI):**

| Role | Color | Used for |
|---|---|---|
| Header / section title | cyan, bold | Title bar (`" neo-localmcp setup wizard"`), `"="*_WIDTH` rules, question line in `_header` |
| `[Preview Dummy]` tag | yellow | The title-bar suffix from section B, both `--fake` and mid-session toggle |
| Descriptions / hints | dim | `_explain(...)` blurbs, per-option `desc` lines in `_print_options`, path/detail lines under client options, `[Default: ...]` hints |
| Success | green | Operation outcome title/detail lines when `outcome.ok` is true, `"(connected)"`/registered markers |
| Error / destructive | red, bold | Operation outcome when `outcome.ok` is false, the full-wipe confirm phase's warning text and the typed-`DELETE`-phrase prompt |
| Option titles / body text | default (no color) | Numbered option titles, running-summary values, general body copy — left plain so colored elements stand out against it |

**Where colors are applied (call sites, not a rewrite):** `_header`,
`_print_options`, `_explain`, `_confirm`/`_phase_uninstall_wipe_confirm`
(destructive-path text), and the outcome-printing block in `_run` are updated
to wrap their existing strings in the relevant color helper — no change to
control flow, phase structure, or what text is shown, only how it's styled.

## Non-goals / deferred

- No un-toggle back to the real backend once `D` is used mid-session (decided
  explicitly — one-way for the session).
- No toggle acceptance outside the main menu.
- No change to the real backend, real lifecycle, or any real managed-root
  path.
- No YAML or other new dependency — plain JSON is sufficient for a
  developer-inspectable scratch file.
- No new dependency for colorization either — raw ANSI only, matching the
  existing stdlib-only convention.
- No configurable palette/theme — one fixed palette for now; an explicit
  `--no-color` flag is deferred since `NO_COLOR`/`isatty` auto-detection
  covers the known cases (piped test input, redirected output, opt-out env
  var).

## Testing

- Existing `--fake` walkthrough tests (if any) continue to pass with the
  renamed title suffix.
- New coverage: `.wizard_preview/state.json` round-trip across install →
  uninstall (runtime-only) → uninstall (full wipe), verifying the file
  contents at each step match the rules above.
- New coverage: `D` at the main menu switches `self.backend` to a
  `FakeBackend` instance and the header immediately shows `[Preview Dummy]`.
- New coverage: with `NO_COLOR` set (or stdout piped, as tests already do),
  no ANSI escape sequences appear anywhere in captured output; with color
  forced on, key strings (header title, `[Preview Dummy]` tag, an error
  outcome) contain the expected escape codes.
