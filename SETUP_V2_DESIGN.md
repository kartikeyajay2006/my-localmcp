# setup_v2 Cross-Platform Lifecycle Design

Status: approved design record.

## Goals

- Replace the platform-specific installer implementations with a Python-first,
  OS-agnostic lifecycle.
- Keep OS-specific code limited to unavoidable path, process, and executable
  differences.
- Keep durable MCP memory and configuration separate from the disposable Python
  runtime.
- Make install, reinstall, uninstall, clean install, process shutdown, and their
  user-facing terminology unambiguous.
- Verify what actually changed on the system and whether the installed MCP
  endpoint works.

## Portability principle

Avoid OS-specific files and implementations by default. Add one only when the
platform-specific behavior materially improves reliability, stability, or
functionality. Every exception remains behind a shared Python interface and
must not redefine lifecycle policy.

## Entrypoint and command ownership

During development the lifecycle entrypoint is `setup_v2.py`. After validation,
it will replace the current installer and be renamed to `setup.py`.

`setup.py` is the only supported interface for installation lifecycle actions:

```text
python setup.py install [flags]
python setup.py reinstall [flags]
python setup.py uninstall [flags]
python setup.py --help
```

Every command and subcommand supports `-h` and `--help` consistently.

The installed executable is always named `neo-localmcp`. There is no `neo`
alias. Installed `neo-localmcp` commands operate the MCP (`doctor`, `config`,
`stop`, context commands, and similar functions); they do not install,
reinstall, or uninstall it.

The installer requires an existing Python 3.12 or newer interpreter. It detects
an unsupported or missing interpreter and prints OS-appropriate guidance, but it
does not install system Python.

The installer installs from the local checkout containing `setup.py`. Downloaded
or remotely selected releases are outside the current scope.

`setup.py` is a thin dispatcher. The testable implementation lives in focused
modules under `neo_localmcp/installer/` rather than in one large script. The
current PowerShell and shell installers are behavioral references, not the new
architecture.

## Managed filesystem

The canonical managed root is `~/.neo-localmcp/`:

```text
~/.neo-localmcp/
|-- venv/       disposable managed runtime
|-- memory/     durable retrieval and feedback memory
|-- sqlite/     durable databases
|-- config/     durable configuration and install metadata
|-- clients/    durable client-registration records
|-- logs/       durable lifecycle and runtime logs
`-- cache/      rebuildable cache
```

`config/install.json` records installation and lifecycle state. Legacy flat
files, including `config.yaml` and `repo-context.sqlite`, move to their canonical
durable locations through explicit, testable migrations.

Before acting, the installer classifies the current state as absent, data-only,
healthy, broken-runtime, legacy-layout, or partial-operation.

Destructive operations validate the resolved managed root. They refuse empty
paths, filesystem roots, home directories, and unexpected paths.

## Lifecycle semantics

### Install

```text
python setup.py install
```

Creates or updates the managed runtime and reuses existing durable data. When
preserved data exists without a runtime, it prints:

```text
Existing neo-localmcp memory detected. Reusing preserved memory/data.
```

### Reinstall

```text
python setup.py reinstall
```

Stops the running MCP, deletes and recreates only `venv/`, reconnects clients,
and preserves memory, SQLite data, configuration, client records, logs, and
cache.

### Uninstall

```text
python setup.py uninstall
```

Stops the running MCP, unloads Neo-used Ollama models, removes active client
registrations, and removes only `venv/`. It does not recreate the venv. Durable
data and client-registration records remain available for a later install.

### Full wipe

```text
python setup.py uninstall --delete-memory --yes
```

Deletes the validated `~/.neo-localmcp/` root, including runtime, memory,
databases, configuration, client records, logs, and cache. A full wipe is never
the default and requires interactive confirmation or explicit non-interactive
flags.

### Clean install

```text
python setup.py install --clean
python setup.py install --clean --yes
```

A clean install is a full wipe followed by a completely fresh install. It
removes active client registrations and all managed runtime and durable data.
Interactive use requires clear confirmation; non-interactive use requires
`--yes`. `--clean` never means only a fresh venv.

## Process and Ollama shutdown

Before replacing or removing the runtime, the lifecycle performs these steps:

1. Mark the operation as in progress.
2. Ask registered neo-localmcp servers to stop gracefully.
3. Unload only the Ollama models configured or recorded as used by Neo.
4. Stop Neo-owned helpers such as `uv.exe`, Python children, launchers, and
   associated Windows console processes.
5. Wait for a bounded timeout.
6. Force-kill only verified Neo-owned survivors.
7. Confirm that no process still holds the managed runtime.
8. Continue the requested lifecycle operation.

Process ownership is established through Neo's process registry, executable
paths beneath the managed venv, and verified process ancestry. The lifecycle
must never kill processes globally based only on names such as `python`, `uv`,
`ollama`, or `conhost`.

Ollama is an external, potentially shared dependency. Neo unloads models it used
to free resources but does not kill a shared Ollama daemon. A model-unload
failure produces a visible warning unless it directly prevents an explicitly
requested full wipe.

## Runtime replacement safety

Install and reinstall build a candidate venv separately and validate it before
replacing the current venv. This keeps dependency or packaging failures from
destroying a working runtime. Client registration and installed-endpoint
verification happen after replacement.

## Planned module boundaries

```text
neo_localmcp/installer/
|-- cli.py
|-- paths.py
|-- state.py
|-- migration.py
|-- processes.py
|-- ollama.py
|-- runtime.py
|-- clients.py
|-- metadata.py
|-- verification.py
`-- output.py
```

Each module owns one lifecycle concern and exposes platform-neutral behavior.
OS-specific implementations remain behind those interfaces.

## Verification contract

Install and reinstall are successful only after verifying all of the following:

- The managed interpreter exists and is Python 3.12 or newer.
- The installed package version matches the local checkout.
- `neo-localmcp` resolves to the managed venv.
- The CLI starts successfully.
- The MCP server imports and completes a bounded startup or handshake probe.
- Client registrations point to the correct managed executable.
- Durable paths resolve to their canonical directories.
- Required `doctor` checks pass.

The final output reports what happened on the system: the requested operation
and result; whether the runtime was created, replaced, removed, or preserved;
whether durable data was reused, created, or deleted; model-unload results;
client-registration changes; verified paths and versions; warnings; and any
required recovery action.

## Failure and interruption handling

Before destructive steps, installation metadata records an in-progress
operation. Success is recorded only after endpoint verification.

Failures preserve or restore the previous validated runtime where possible. An
interrupted or partially completed operation remains detectable on the next run
and must never be silently reported as success.

## Test contract

Tests cover lifecycle semantics, path validation, legacy migration, client
cleanup and restoration, process ownership, interruption recovery, output
terminology, metadata transitions, broken-runtime detection, and
Windows/macOS/Linux path behavior. Platform-specific process behavior is tested
on its corresponding CI operating system.

## OS-specific boundaries

Shared Python code owns all lifecycle decisions. Platform adapters are limited
to unavoidable differences:

- Managed executable locations: `venv/Scripts/` on Windows and `venv/bin/` on
  macOS/Linux.
- Process discovery, ancestry, graceful termination, and forced termination.
- Windows console-process relationships.
- Client configuration locations and path formatting.
- Executable suffixes, permissions, and filesystem replacement behavior.

No PowerShell or shell file owns lifecycle policy.

## User-facing terminology

Help text, prompts, logs, tests, and documentation use these meanings exactly:

- `install`: create or update the runtime and reuse memory.
- `reinstall`: replace the runtime and preserve memory.
- `uninstall`: remove the runtime and preserve memory.
- `install --clean`: delete everything, then install fresh.
- `uninstall --delete-memory`: delete everything and do not reinstall.

Every destructive prompt identifies the exact managed root and data categories
that will be deleted before requesting confirmation.

## Development rollout

1. Build and test `setup_v2.py` beside the current installers.
2. Reach Windows feature parity and implement macOS/Linux behavior through the
   shared lifecycle.
3. Verify migration from every recognized legacy layout.
4. Rename `setup_v2.py` to `setup.py`.
5. Retire the old PowerShell and shell lifecycle implementations only after
   parity is proven.
