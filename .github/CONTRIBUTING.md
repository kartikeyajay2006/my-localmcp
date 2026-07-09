# Contributing

## Issue / PR title format

`type(area): description` — e.g. `fix(installer): rebuild stale .mcpb bundle`,
`feat(wizard): back navigation, richer summary, model sizes, examples`.

Same convention for commit messages, PR titles, and issue titles — one mental
model, not three. Based on [Conventional Commits](https://www.conventionalcommits.org/)
and its common Angular-convention type extension, with two intentional
project-specific deviations noted below.

Every issue/PR gets one `type:` label and, unless it's `meta`, one or more
`area:` labels.

## Types

| Type | Meaning | Note |
|---|---|---|
| `meta` | Project-as-repo: README/`PROJECT_STATUS.md`/`PROJECT_NOTES.md`, `LICENSE`, `.github/*` — plus open decisions/direction questions not yet resolved into a change | Custom type, not part of Conventional Commits or its Angular extension; kept deliberately since nothing standard covers "is this about the codebase at all" |
| `docs` | Documentation *of the implementation*: `docs/ARCHITECTURE.md`, `docs/*_PLAN.md`, design specs | Distinct from `meta` by subject matter (implementation vs. project), not by size |
| `chore` | Angular-strict: non-src/non-test files only — tooling, config, dependencies. **Never touches src.** | Deliberately kept at the standard, narrower definition rather than broadened to include code cleanup |
| `refactor` | Code restructuring, any size, no behavior change | Absorbs what might otherwise be miscategorized as a "small chore" |
| `feat` | New capability | |
| `fix` | Bug fix | |
| `test` | Test-only additions/changes | |
| `perf` | Performance-focused change | |
| `security` | Security-relevant fix/hardening | |

`meta`-typed items get no `area` label — area labels denote codebase regions,
and `meta` is explicitly about the project, not the codebase.

## Areas

```
wizard                (setup_wizard.py, neo_localmcp/wizard/)
├─ installer          (setup.py, neo_localmcp/installer/, + packaging/.mcpb)
└─ client-integration (neo_localmcp/ai_client_config.py)

mcp-tools             (tools.py, runtime_cli.py, server.py, slash-command templates)
├─ mcp-mgmt           (doctor, status, index/refresh/reindex, servers/stop, config)
└─ mcp-toolkit        (context, file_excerpts, lookup, summarize, apply-patch, record-change)

ollama                (ollama_client.py)
retrieval             (repo_memory.py, query.py, ranking logic, schema/migration)
benchmark             (not yet built — reserved for automated token-reduction measurement)
```

GitHub labels don't nest, so a change to a child area gets **both** labels
(e.g. `area:wizard` + `area:installer`); a parent-only change (e.g. pure
wizard-UI work with no installer/client-integration involvement) gets just
the parent label. A change can carry more than one area label if it
genuinely spans areas (e.g. a change touching both the wizard's Ollama
screen and `ollama_client.py` gets `area:wizard` + `area:ollama`).

## Verification before opening a PR

See `.github/pull_request_template.md` for the actual checklist. Short version:
touched `neo_localmcp/` or `tests/` → run `pytest -q -m "not slow"` locally;
docs/meta-only changes need neither. CI (not this checklist) is what's actually
required before merging — branch protection enforces it. The `.mcpb` bundle is
rebuilt automatically by `setup.py install`/`reinstall` from a source checkout
(`neo_localmcp/installer/mcpb.py`), not a manual step. On a version bump,
`git rm` the previous version's `packages/claude-desktop/neo-localmcp-v*.mcpb`
before committing the new one — only the current version's bundle should stay
tracked.
