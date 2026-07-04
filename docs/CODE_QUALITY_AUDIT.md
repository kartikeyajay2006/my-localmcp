# neo-localmcp Code Quality Audit — Issue #10

A dedicated code-**quality** pass (not correctness — that was audited separately
and passed). Scrutinizes KISS / SOLID / DRY, readability, comment quality against
CLAUDE.md's own standard ("default to no comments, only for non-obvious WHY"),
module-boundary cleanliness, and docs-vs-code drift. Findings are concrete, with
`file:line` references, severity, the problem, and a suggested direction. Depth
over breadth on core source, in the owner-chosen priority order.

**Method:** every finding rests on a direct deterministic read of the source
(`Read`), not on any model-generated summary. neo-localmcp's own CLI was used as
the primary navigator (see `docs/dogfooding-notes-issue-10.md`) and Ollama
fast/slow output was treated as a junior-dev signal to chase and then verify
(see per-module "Ollama leads" notes and `docs/mcp-perf-log-issue-10.md`).

**Severity key:** `high` = actively harms maintainability/correctness-adjacent
clarity or is a real doc-drift a reader would be misled by; `med` = worth fixing,
adds friction or risk; `low` = polish, note-and-move-on.

**Scope note:** analysis only. No source, test, or config file was modified in
producing this report. Line numbers reference the working tree at the branch base.

---

## Module verdicts at a glance

| # | Module | Verdict |
|---|--------|---------|
| 1 | `tools.py` | Comments exemplary; real problem is size — 1054 lines, `context_prepare` is a ~233-line 8-job function. 6 findings (1 high). |
| 2 | `repo_memory.py` | Cleanest large module; well-factored, benchmark-quality docstrings. 5 minor findings (1 med), nothing high. |
| 3 | `query.py` | Compact and readable; main issue is two hand-synced word-set copies (DRY). 5 findings (1 med), nothing high. |
| 4 | `cli.py` | Textbook thin argparse dispatcher; only cosmetic findings. 4 findings, all low. |
| 5 | `server.py` | Clean FastMCP registry; repeated error-wrapper (DRY) + undocumented subprocess asymmetry. 4 findings (2 med). |
| — | `config.py` | Solid tuning comments, but `.claude` missing from `exclude_dirs` (high, causes worktree pollution), constants/functions DRY split, JSON-in-.yaml drift. 4 findings (1 high, 2 med). |
| 6 | `ollama_client.py` | Sound state-machine + fallback contract; pervasive result-dict DRY and a few mega-lines. 4 findings (2 med). |
| 7 | `lifecycle.py` | **Clean, nothing actionable** — best-documented module in the repo; a model to emulate. |
| 8 | `client_setup.py` | Good I/O comments; `remove_codex` reads config 3x, `setup_claude_code` migration dense, scope-detect duplicated. 5 findings (2 med). |
| 9 | `wizard/*` | Real Protocol seam, well-documented; private-symbol reaches across boundary + minor typing. 4 findings (1 med). |

**Bottom line:** comment quality is a project strength (matches CLAUDE.md's WHY-only
standard, near-zero noise); the debt is structural — oversized functions and
systematic DRY in the rendering / result-dict layers. Highest-value fix is the
one-line `.claude`-exclusion (C.1), a live retrieval-quality bug. See the
"Summary and suggested priorities" section at the end for the ranked action list.

---

## 1. `neo_localmcp/tools.py` (1054 lines)

**Verdict:** The MCP tool surface is functional and its comments are, on the
whole, unusually good — most explain a genuine non-obvious WHY (determinism,
budget-starvation, client-hang avoidance), which is exactly CLAUDE.md's standard.
The real quality problem is **size and single-function complexity**: `tools.py`
is a 1054-line grab-bag mixing thin CLI-facing wrappers, three near-parallel
response *renderers*, the entire ranking/centering engine, and Ollama
orchestration in one flat module. `context_prepare` alone is ~230 lines and is
the single densest, hardest-to-follow function in the codebase. Findings below
are about structure and a few genuinely-hard spots lacking a WHY, not about the
comments (which are mostly a model to keep).

### Findings

**1.1 — `context_prepare` is a ~233-line function doing 8 distinct jobs (SRP / KISS) — `tools.py:639-871` — severity: high**
The function inlines: (a) freshness/refresh decisions, (b) query normalization,
(c) three separate candidate-scoring passes (index/symbol hits, batched rg
search, explicit-path resolution), (d) doc-reference following, (e) the
retrieval-memory boost, (f) ranking + `read_first` selection, (g) excerpt-range
centering with three fallback tiers, and (h) the optional Ollama prompt+call and
result assembly. Each is independently testable and nameable
(`_score_index_and_symbol_hits`, `_score_batched_search`, `_select_read_first`,
`_build_excerpt_ranges`, `_run_ollama_ranking`). A future reader cannot hold the
whole function in their head, and the local variables (`candidates`,
`symbol_hits`, `sections_for_memory`, `section_by_path`, `explicit_paths`,
`followed_references`) are threaded through all eight jobs. *Direction:* extract
the scoring passes and the excerpt-range builder into named helpers that take and
return the `candidates` dict / ranges list; the top-level function then reads as
a pipeline. This is the highest-leverage readability change in the module.

**1.2 — Three overlapping response renderers duplicate structure (DRY) — `tools.py:86-139` (`_render_context_text`), `tools.py:143-198` (`_mcp_compact_context`), `tools.py:237-317` (`_mcp_tiny_context_text`) — severity: med**
All three walk the same `read_first` / `candidate_files` / `agent_guidance` /
`ollama_*` shape and re-implement the same field extraction (`item.get("path")`,
`item.get("category")`, `item.get("score")`, `_compact_line_hints(...)`,
`reasons[:3]`). `compact_item` (line 153) and the loops at 107-120 and 277-283
are the same projection three times over. The `git`/`interp` unpacking prologue
is copy-pasted at 100-104, 150-151, and 244-246. *Direction:* a single
`_project_read_first_item()` (there is already a near-identical `project_item` at
line 413 inside `_stable_context_projection`, making it **four** copies) plus a
shared header-fields helper would collapse most of this. `_format` (319) already
dispatches on format; the per-format bodies are where the duplication lives.

**1.3 — `_sanitize_ollama_advisory` mixes three concerns with unexplained magic constants — `tools.py:201-234` — severity: med**
The function does control-char stripping, section-keyword gating, and length
trimming in one pass. The trim logic at 229-232 has an unexplained `400`
threshold (`if last_newline > 400`) — a reader cannot tell why 400 and not, say,
`max_chars // 2`. The keyword list at 220 (`"recommended read order"`, etc.) is
coupled by exact string to the prompt authored 400 lines away at
`context_prepare:859` ("Return concise sections exactly named: Recommended read
order, ..."). If the prompt wording changes, this silently stops matching and
falls back to a raw truncation. *Direction:* name the `400` (`_MIN_KEEP_CHARS`)
with a one-line WHY, and hoist the section-name list to a module constant shared
with the prompt so the coupling is visible in one place.

**1.4 — `ollama_control` action-dispatch dict rebuilt on every call — `tools.py:1042-1054` — severity: low**
The `actions` dict of seven lambdas is reconstructed on each invocation. Harmless
performance-wise (this is a one-shot CLI/MCP call), but it's the kind of table
that reads more clearly as a module-level mapping. Minor. *Direction:* optional;
leave if preferred — flagged only for completeness.

**1.5 — `prepare_context` vs `context_prepare`: a naming near-collision that only comments would disambiguate, and there are none — `tools.py:639` and `tools.py:874` — severity: med**
`context_prepare` is the full implementation; `prepare_context` (874) is a thin
adapter that flips `max_files`/`limit` and defaults `output_format="mcp_text"`.
The two names are anagrams of each other and nothing at either definition says
"this is the MCP-facing adapter; that is the CLI implementation." A reader
grepping for one will land on the other. The module map in CLAUDE.md lists
`prepare_context/context_prepare` together but doesn't explain the split either.
*Direction:* a one-line docstring on each ("MCP entrypoint — see
`context_prepare`" / "core implementation; `prepare_context` is the MCP adapter")
is exactly the non-obvious-WHY comment CLAUDE.md's standard calls for.

**1.6 — `doctor` hard-codes a `commands` list that must be kept in sync with `cli.py` by hand — `tools.py:387` — severity: med**
The `"commands": [...]` array (24 literal strings) duplicates the subcommand set
that `cli.py`'s argparse already declares. Nothing enforces they stay in sync;
`remove-client` appears in the argparse help but not obviously mirrored here, and
the reverse drift (a command added to `cli.py` but not to this list) is silent.
This is a DRY/doc-drift hazard inside the code itself. *Direction:* derive the
list from the argparse subparser names, or drop it (the CLI `--help` is already
the authoritative inventory).

**1.7 — Good comments worth preserving (positive finding)**
Several comments here are exemplary against CLAUDE.md's standard and should be
kept as the template for the rest of the codebase: `tools.py:534-536` (why score
each reason once — determinism), `tools.py:544-546` (why track hint weights),
`tools.py:721-725` (why the memory boost is bounded below structural signal),
`tools.py:938-940` (why `eval_count >= num_predict` means truncation),
`tools.py:993` context (the `newline=""` fix, a real Windows CRLF bug per
PROJECT_NOTES). None of these restate the code; each explains a decision a reader
would otherwise have to reverse-engineer. No noise/redundant comments were found
in this module — a clean result on the comment-quality axis.

**Ollama leads for tools.py (fast-rank `qwen3:8b`, verified against source):**
The fast reranker independently flagged that `context_prepare` "might lack single
responsibility (combining context creation with ranking logic)" and that a block
around lines 847–875 "may violate KISS by handling multiple responsibilities."
Verified against source: 847–875 is the Ollama prompt-assembly + result-nesting
tail of `context_prepare`, i.e. one of the eight jobs identified in finding **1.1**
— so the model's high-level smell was **correct and corroborates 1.1** (recorded
as a datapoint, not the basis for the finding, which stands on my own read). It
also produced junior-dev-grade noise: it invented "audit logging" that does not
exist in `tools.py`, and cited `server.py:105` vaguely. Net: right on the smell,
wrong on specifics — chase-worthy, not authoritative. (Also notable: with
`--ollama-rank` on, the reranker named the canonical `neo_localmcp/tools.py` #1,
effectively *correcting* the deterministic worktree-copy pollution from Q5 — a
point in the tool's favor, logged in the perf log.)

---

## 2. `neo_localmcp/repo_memory.py` (774 lines)

**Verdict:** The cleanest large module in the codebase and a good counter-example
to tools.py. Despite being 774 lines it reads well: one function per concern,
short bodies, SQL kept close to its caller, and genuinely excellent WHY-docstrings
on the subtle parts (`record_task_query`, `record_retrieval_feedback`,
`get_boost_map`, `store_section_summary`, `lookup`). Comment quality here is a
model for the repo. Findings are minor structural/DRY items; nothing high.

### Findings

**2.1 — Every public function re-runs the same `root → connect → rid` prologue (DRY) — `repo_memory.py:258-261, 313-316, 360-366, 393-397, 452-456, 626-630, 639-643, 697-700, 717-720, 724-727, 737-741` — severity: low**
Roughly a dozen functions open with the same 3–4 lines
(`root = repo_root_or_cwd(...); conn = connect(); rid = upsert_repo(conn, root)`
or the read-only `rid = repo_id(root)` variant). This is tolerable — it's honest
and greppable — but a small `_repo_conn(repo_root, *, writable=False)` helper
returning `(root, conn, rid)` would remove ~30 repeated lines and make the one
meaningful distinction (which paths call `upsert_repo` vs. the cheaper `repo_id`)
explicit rather than a subtlety a reader has to notice per-function. *Direction:*
optional consolidation; the WHY note in `lookup` (363-366) about avoiding
`upsert_repo` on the hot path is exactly the kind of decision a shared helper
should encode by name.

**2.2 — `import` placed mid-file after a top-level statement — `repo_memory.py:11-12` — severity: low**
`INDEXER_VERSION = "1.1.0"` is assigned on line 11, then `from .utils import ...`
on line 12 — a second import statement sitting *below* a module constant, after
the line-9 `from .config import`. Cosmetic but it trips the eye and every linter's
import-grouping rule (imports should precede module-level code). *Direction:*
move the `.utils` import up with the other imports; keep `INDEXER_VERSION` in the
constants block with `RETRIEVAL_BOOST_*`.

**2.3 — `reset_repo` enumerates the same table list twice, by hand — `repo_memory.py:743, 749-757` — severity: med**
The count-before loop (743) lists 8 tables and the delete block (749-757) lists 9
`DELETE` statements (the extra being `repo_fts`, which is silently absent from the
counts). Adding a table to the schema requires editing three places in this file
(the `CREATE TABLE` in `init_db`, this count list, and this delete list) with
nothing enforcing they agree — a real maintenance trap for a "wipe this repo"
operation where a missed table means stale data survives a reset. *Direction:*
drive both from one `_REPO_SCOPED_TABLES` tuple (with `repo_fts` and the `repos`
special-case noted), so a new table is deleted-on-reset by construction.

**2.4 — `connect()` calls `init_db()` on every single connection — `repo_memory.py:29-35, 38-148` — severity: low**
Every `connect()` runs the full `executescript` (all `CREATE TABLE IF NOT EXISTS`)
plus two `PRAGMA table_info` migration scans. It's idempotent and correct, and on
SQLite it's cheap, but it means every `lookup`/`status`/`file_excerpts` call pays
the schema-init + migration-probe cost. Given `lookup` is explicitly documented
(363-366) as a latency-sensitive hot path, this is mildly at odds with that intent.
*Direction:* not urgent; if profiling ever shows it, gate `init_db` behind a
process-level "already initialised this db file" flag. Flagged for awareness, not
as an action item — correctness is fine.

**2.5 — Positive: the memory-subsystem docstrings are the comment-quality benchmark for the repo**
`get_boost_map` (585-595), `record_retrieval_feedback` (542-550),
`record_task_query` (514-522), and `store_section_summary` (663-668) each explain
the non-obvious WHY (why capped, why recency-gated, why silence isn't penalised,
why summaries never set line boundaries) without restating the SQL. This is
exactly CLAUDE.md's standard. No noise comments found in this module.

---

## Cross-cutting: `neo_localmcp/config.py` (183 lines)

Audited alongside repo_memory because it's the schema/tuning source of truth.

**C.1 — `.claude/` (and `.claude/worktrees/`) is NOT in `exclude_dirs`; this is the root cause of the Q5 worktree-copy ranking pollution — `config.py:100-105` — severity: high**
The default `exclude_dirs` list (100-105) covers `.git`, `.venv*`, `.neo-localmcp`,
etc. but not `.claude`. In this repo — an AI-led project that uses
`.claude/worktrees/agent-*` for parallel agent sessions — every sibling worktree is
a full second copy of the repo, so the indexer ingests N duplicate `tools.py`,
`repo_memory.py`, etc. Live impact was severe: for a `tools.py` query the real
working-tree file ranked **#6 (score 152)** behind five worktree duplicates
(scores 505–568) — see `docs/dogfooding-notes-issue-10.md` Q5. This is the exact
class of bug PROJECT_NOTES 2026-07-03 (2) fixed for `.venv-phase14`, recurring
through a different unexcluded directory. *Direction:* add `.claude` to the default
`exclude_dirs`. (`fnmatch` on the dir name already supports it; because matching is
name-only per the 94-99 comment, `.claude` excludes the top-level dir and its
worktrees subtree.) This is a real retrieval-quality bug worth a filed issue.

**C.2 — Module-level path constants duplicate the path functions (DRY) — `config.py:11-17` vs `23-53` — severity: med**
`APP_DIR`/`CONFIG_DIR`/`SQLITE_DIR`/`DEFAULT_DB_PATH`/`CACHE_DIR`/
`PROCESS_REGISTRY_DIR` (11-17) are computed once at import, and then `config_dir()`,
`sqlite_dir()`, `default_db_path()`, `cache_dir()`, `process_registry_dir()`
(23-53) recompute the same values as functions. Two of the functions
(`config_path`, and the `_effective_default_config` db-path swap at 133-138) exist
specifically to work around the fact that the *constants* are frozen at import time
while `APP_DIR` can be overridden by `NEO_LOCALMCP_HOME` later (mainly in tests).
The result is a confusing split where a reader can't tell whether to trust the
constant or the function, and `CONFIG_PATH` (a constant) and `config_path()` (a
function) can legitimately disagree. *Direction:* pick one. Either make the
constants the single source and document "import-frozen; override HOME before
import," or make everything a function and drop the constants. The current mix is
the module's main readability cost.

**C.3 — Doc-vs-code drift: "config.yaml" is actually written and read as JSON — `config.py:13, 155, 161, 175`; CLAUDE.md module-map line for `config.py` — severity: med**
The file, the constant `CONFIG_PATH` (`.../config.yaml`), and CLAUDE.md all call
it `config.yaml`, but `ensure_config` writes it with `json.dumps` (155) and
`load_config` reads it with `json.loads` (161). The on-disk artifact is JSON with a
`.yaml` extension. A future maintainer (or a user opening the file) is actively
misled — YAML comments or non-JSON YAML syntax in that file would silently fail to
parse. *Direction:* either genuinely support YAML (add a parser and keep the name)
or rename to `config.json` and update the constant + CLAUDE.md + any installer
references. At minimum, document the JSON-in-a-.yaml-file reality where the name is
defined. This is a genuine docs-vs-code drift of the kind issue #10 asks about.

**C.4 — Positive: the tuning comments (79-83, 90-99, 110-125) are excellent** — each
explains why a default is what it is and cites the PROJECT_NOTES entry that set it.
Model comment quality.

**Ollama leads for repo_memory/config:** the fast-rank pass for a "repository
indexing / retrieval-boost" query did not surface anything my own reads had not
already covered; it re-ranked the memory functions sensibly and added no false
leads worth recording. Datapoint: on this well-factored module the model was
neither especially helpful nor harmful — it neither found nor invented a finding.

---

## 3. `neo_localmcp/query.py` (197 lines)

**Verdict:** Compact, readable, single-purpose. The parsing logic is honest and
the two subtle comments (69, 91-92) explain real WHY decisions. The main quality
issues are DRY (two hand-maintained word-set copies that must agree) and a couple
of redundant/inline constructs. The already-filed retrieval bugs (#23 identifier
weighting, #24 overloaded "migration") originate here but are *behavioral* — out
of scope for this quality pass and not re-filed.

### Findings

**3.1 — `FILLER_WORDS` and `INTENT_KEYWORDS` duplicate the same word sets, maintained by hand in two places (DRY) — `query.py:7-20` and `22-28` — severity: med**
Every word in `INTENT_KEYWORDS` (the 5 intent sets, ~50 words) is ALSO listed in
`FILLER_WORDS` — deliberately, per the "Intent words should set intent, not become
grep terms" comment (13). But the two lists are maintained separately: adding a new
intent keyword (say `"teardown"` to `refactor`) requires also remembering to add it
to `FILLER_WORDS`, or it silently becomes a weak grep term. Nothing enforces the
invariant "every intent keyword is a filler word." *Direction:* derive
`FILLER_WORDS` as `_PURE_FILLER | set().union(*(w for _, w in INTENT_KEYWORDS))`
so the invariant holds by construction and the two lists can't drift.

**3.2 — `infer_intent` computes `best`/`score` then discards `best` for the top four intents — `query.py:61-78` — severity: low**
Lines 66 computes `best, score = max(...)`, but 70-77 then hard-code the
debug>feature>refactor>test precedence, so `best` is only ever returned (78) for
the `explain` case. A reader has to trace all the way down to realise the `max()`
result is mostly vestigial. *Direction:* the precedence is a deliberate documented
choice (69) — make it fully explicit: check `score <= 0 → "context"`, then walk an
ordered `("debug","feature","refactor","test","explain")` tuple returning the first
with a nonzero score. That removes the misleading `max()`/`best` dance entirely.

**3.3 — `category_boost` inlines four near-identical dict literals — `query.py:173-180` — severity: low**
Four separate `{...}.get(category, 0)` maps, one per intent branch, that share most
keys and differ only in the numbers. It's readable as-is (the numbers ARE the
policy and seeing them side by side has value), but a reader can't easily diff
"what changes between debug and test policy" without eyeballing four dicts.
*Direction:* optional — a single `_CATEGORY_BOOST[intent][category]` nested table
(or keeping the dicts but naming them `_DEV_BOOST`, `_TEST_BOOST`, etc.) would make
the policy diffable. Low priority; the inline form is not wrong.

**3.4 — `_split_focus` only splits on the FIRST colon, silently — `query.py:35-39` — severity: low**
`text.split(":", 1)` means a task like `"debug: fix the C:/path/thing"` puts
everything after the first colon into focus, and a second colon is treated as prose.
Harmless in practice (tasks rarely have two colons) and arguably correct, but it's
an undocumented assumption. *Direction:* a one-line comment noting "focus is
everything after the first colon; later colons are prose" would make the intent
explicit. Trivially low.

**3.5 — Positive: no noise comments; the two comments present (69, 91-92) are both real WHY.** Clean on the comment-quality axis.

**Ollama leads for query.py:** not separately run (module is small and was fully
read); the earlier fast-rank runs never surfaced query.py as a complexity concern,
consistent with my read that it is not one.

---

## 4. `neo_localmcp/cli.py` (319 lines)

**Verdict:** Clean, textbook. A thin argparse dispatcher: each `cmd_*` is a
one-line delegation to `tools.*`, and `build_parser` is a flat, readable
subcommand registry. No behavior lives here that belongs elsewhere — exactly the
right separation (administration is CLI-only, per CLAUDE.md, and it holds). Almost
nothing actionable; the findings are cosmetic.

### Findings

**4.1 — `print_json_text` is a no-op wrapper around `print` — `cli.py:14-15` — severity: low**
`def print_json_text(text): print(text)` adds an indirection with no value; every
`cmd_*` calls it, but half the file calls bare `print(json.dumps(...))` directly
(e.g. 51, 66, 73, 79) — so there are *two* printing conventions in one file for no
reason. *Direction:* drop `print_json_text` and use `print` everywhere, or route
the bare-`json.dumps` sites through it — pick one. Trivial, but it's a real
inconsistency a reader notices immediately.

**4.2 — `apply-patch` file open is unmanaged (no `with`) — `cli.py:175` — severity: low**
`open(args.patch_file, ...).read()` leaks the file handle (relies on CPython refcount
GC to close it). Harmless for a one-shot CLI, but it's the one spot in the file that
doesn't use a context manager and would trip a linter (`SIM115`). *Direction:* wrap
in `with open(...) as f:` or use `Path(args.patch_file).read_text(encoding="utf-8")`.

**4.3 — `--client` choices list is duplicated verbatim across three subparsers — `cli.py:210, 215, 239` — severity: low**
The `choices=["all", "claude-code", "claude-desktop", "codex", "codex-cli",
"codex-desktop"]` list appears three times (setup, remove, deprecated remove-client).
Plus the help strings differ subtly ("Defaults to Claude Code, Claude Desktop, Codex
CLI, and Codex Desktop" vs "Defaults to Claude Code, Codex, and Claude Desktop") —
a small doc inconsistency. *Direction:* hoist the choices to a module constant
`_CLIENT_CHOICES` and reuse; align the default-description text.

**4.4 — Positive: the deprecation comment (89) and `stop`/`reset` help strings are clear and honest.** No noise comments. Good module.

---

## 5. `neo_localmcp/server.py` (222 lines)

**Verdict:** Clean and appropriately thin — a FastMCP tool registry over `tools.*`
plus the one genuinely subtle piece, `_resolve_repo_root`, which is well-commented
where it matters. Two real structural notes: a repeated error-wrapper (DRY) and an
in-process-vs-subprocess asymmetry that deserves a WHY comment at the tool level.

### Findings

**5.1 — The `try/except Exception → json error` wrapper is copy-pasted in 9 tool functions (DRY) — `server.py:97-101, 118-122, 128-131, 137-140, 146-149, 155-158, 164-167, 173-176, 182-185` — severity: med**
Every async `@mcp.tool()` repeats the identical shape: resolve root, call
`tools.*`, `except Exception as exc: return json.dumps({"ok": False, "error":
str(exc)}, ...)`. Nine copies. This is the most repetitive block in the module and
the error JSON drifts slightly (some add `"action": "provide repo_root"`, most
don't). *Direction:* a small decorator `@_tool_guard` (or a helper
`_safe(coro)`) that wraps the body and standardises the error envelope would remove
~27 lines and guarantee a consistent error shape across every tool. This is the
one worthwhile refactor in the file.

**5.2 — `prepare_context` runs in a subprocess while every other tool runs in-process — an undocumented asymmetry — `server.py:60-91` vs `110-197` — severity: med**
`prepare_context` shells out to `python -m neo_localmcp.context_worker` (60-91)
with its own timeout + safety cap, but `file_excerpts`, `repo_lookup`,
`repo_status`, `summarize_file`, etc. call `tools.*` directly in-process. There IS
a real WHY (isolating the heaviest/Ollama-touching call so a hang or crash can't
take down the stdio server, plus the UTF-8 subprocess-encoding fix from
PROJECT_NOTES 1.0.1/1.0.3), but nothing at `prepare_context`/`_context_prepare_worker`
says so. A maintainer could "simplify" it back to an in-process call and silently
reintroduce a solved class of hang/encoding bug. *Direction:* a 2-line WHY comment
on `_context_prepare_worker` ("isolated in a subprocess because it is the heaviest,
Ollama-touching path; a hang/crash here must not take down the stdio loop, and the
worker enforces UTF-8 I/O — see PROJECT_NOTES 1.0.1/1.0.3"). This is precisely the
non-obvious WHY comment CLAUDE.md's standard exists for, and it's missing.

**5.3 — The MCP `prepare_context`/`context_prepare` alias pair mirrors the tools.py adapter pair — same naming-collision friction as finding 1.5 — `server.py:94-107` — severity: low**
Here the alias is at least labelled ("Compatibility alias for prepare_context;
retained for one release", 106), which is better than the tools.py pair (1.5). The
one-release-alias is also called out in the server instructions and PROJECT_STATUS.
Noted for consistency; the docstring here is adequate. Cross-reference finding 1.5.

**5.4 — Positive: `main()`'s lifecycle-registration comment (201-203) and the "never let bookkeeping prevent serving" comment (213) are exactly right** — both explain a real WHY (graceful stop, fail-open bookkeeping). `_resolve_repo_root`'s UNC-path handling (50-51) could use a one-line note, but the ambiguity-refusal errors (56-57) are self-documenting. Good comment hygiene overall.

**Ollama leads for cli/server:** the fast-rank pass surfaced `server.py` as a
secondary read for the "context_prepare" query (Entry 4) and vaguely flagged
"unclear dependencies / external state" in it — which, charitably, points at the
subprocess-worker indirection (finding 5.2). Verified: the model did NOT identify
the actual asymmetry or its WHY; it produced a generic "might depend on external
state" hunch. Junior-dev-grade: directionally near a real finding (5.2) but too
vague to be the basis for it. Recorded as a datapoint.

---

## 6. `neo_localmcp/ollama_client.py` (343 lines)

**Verdict:** A well-designed local-service supervisor: atomic-directory lock,
failure-cooldown circuit breaker, per-purpose bounded timeouts, and a strict
"deterministic fallback, never raise into the caller" contract that it honours
everywhere. The subtle pieces are well-commented (`unload_model` docstring 281-286,
the `num_predict` WHY 318-320, the `_resolve_installed_model` tag note 74). The
quality cost is the extremely repetitive result-dict construction and a few
mega-lines that pack too much onto one line to read comfortably. Nothing high.

### Findings

**6.1 - Result-dict construction is pervasively duplicated across warm/ensure/unload/chat (DRY) - `ollama_client.py:227-247, 291-300, 315-343` - severity: med**
Nearly every early-return builds `{"ok": ..., **current, "state": ..., "action":
..., "error": ..., "elapsed_seconds": round(time.monotonic() - started, 3)}` by
hand. `warm` has 5 such returns, `chat` has 4, `unload_model` 4 - each re-typing
the same keys with slight variation. The `elapsed_seconds` computation
(`round(time.monotonic() - started, 3)`) alone appears ~10 times. *Direction:* a
small `_result(ok, state, action, *, error=None, started=None, **extra)` factory
would collapse most of these and guarantee a consistent envelope (right now
`timeout_seconds`/`timed_out` appear in some error returns and not others - a subtle
inconsistency a shared factory would fix). This is the module's main readability debt.

**6.2 - The `chat` success return (337) is a single ~350-char line doing 8 things - `ollama_client.py:337` - severity: med**
Line 337 builds the entire success payload - `response`, `elapsed_seconds`,
`near_timeout` (with an inline `elapsed >= max(1, timeout_seconds - 10)`
computation), a nested `raw` dict comprehension over 6 keys, and `ollama_status` -
all on one line. It is the least-readable line in the module; the `near_timeout`
threshold and the `raw` key list both deserve to be visible, not buried mid-line.
*Direction:* break into a few assignments (`near_timeout = elapsed >= ...`;
`raw = {k: payload.get(k) for k in _TIMING_KEYS}`; then the dict). Pure readability.

**6.3 - `_model_for` has a precedence subtlety that reads as a possible latent bug - `ollama_client.py:66-70` - severity: low**
`return str(cfg.get("fast_model") if purpose in {"ranking","query"} else
cfg.get("summary_model") or cfg.get("fast_model") or "qwen3:8b")` - the
`... else X or Y or Z` binds as `else (X or Y or Z)`, so the fallback chain applies
only to the summary branch, NOT the ranking branch. A missing `fast_model` on the
ranking branch yields `str(None)` -> `"None"` (no `or "qwen3:8b"` guard). In
practice `config.py`'s defaults always set `fast_model`, so it never fires - but a
reader cannot tell that from this line, and it is genuinely confusing. *Direction:*
split the ternary and give both branches the same `or "qwen3:8b"` guard, or add a
WHY comment noting the config default is what makes the ranking branch safe.

**6.4 - Positive: the fallback-contract comments and state-machine design are exemplary.**
`unload_model` (281-286) explaining why it never raises, the circuit breaker
(`circuit_open`, 255-256), and the `num_predict` bound (318-320) all explain real
WHY. The module honours CLAUDE.md's "Ollama never blocks deterministic behavior"
rule in code, not just in principle. No noise comments.

**Ollama leads for ollama_client.py:** not run through the reranker (circular to ask
Ollama to review its own client, and the module is small enough to fully read).
Instead it was exercised *directly* and heavily via the perf log (Entries 2-4,
8-10) - the observed cold/warm/cache behavior (load-dominated 30B cost, clean
fallback, accurate bounded output) matches the code's design, corroborating that
the state machine works as written.

---

## 7. `neo_localmcp/lifecycle.py` (304 lines)

**Verdict: clean, nothing actionable.** This is the best-documented module in the
repo and a model for the rest of it. The module docstring (1-25) explains the entire
stop-file/self-exit design and *why* a signal wouldn't work on Windows; `pid_alive`
(70-102) documents the `WaitForSingleObject`-vs-`GetExitCodeProcess` choice and the
NULL-handle interpretation; `_graceful_self_exit` (265-278) explains why `os._exit`
is deliberate and what invariant (no long-lived in-process state) makes it safe.
Functions are short, single-purpose, and the section-comment dividers
(`# --- registry ---`) aid navigation without being noise. No DRY, KISS, or SOLID
issues found; no missing WHY comments; no noise comments. I looked specifically for
something to flag here and did not manufacture one - this module is genuinely good.

(One micro-note, not a finding: `force_terminate` at 109 has an inline
`# maps to TerminateProcess on Windows` comment duplicating the module docstring's
explanation; harmless and arguably helpful at the call site.)

---

## 8. `neo_localmcp/client_setup.py` (460 lines)

**Verdict:** Mostly clean with good WHY-comments on the genuinely tricky I/O
(`_read_config_for_edit` 53-61, `_atomic_write_text` 64-77, the marked-block
strip/replace 109-132). The quality problems are concentrated in two spots: the
`setup_claude_code` migration function is dense, and `remove_codex` re-reads the
config file three times to build one return dict. Scope-detection is also duplicated.

### Findings

**8.1 - `remove_codex` re-reads + re-parses the config file 3x inside one return dict - `client_setup.py:340, 350, 355` - severity: med**
`block_present` is computed at 340 (`path.read_text(...)`), then the return dict
recomputes essentially the same `"# BEGIN neo-localmcp" in path.read_text(...)`
check TWICE more - once in the `ok` expression (350) and once in `block_present_after`
(355) - each doing a fresh disk read + full-file parse. Three reads of the same file
in one function, and the `ok`/`block_present_after` expressions are near-identical
inline booleans that are hard to read. *Direction:* read the post-write text once
into a local (`after_text = path.read_text(...) if path.exists() else ""`) and derive
`block_present_after`/`ok` from it. Removes 2 redundant disk reads and clarifies the
return. This is the clearest actionable item in the module.

**8.2 - `setup_claude_code` is a ~55-line function with an inline 3-iteration migration loop (SRP/KISS) - `client_setup.py:149-204` - severity: med**
The function installs slash commands AND runs the full MCP-registration migration
(detect existing scope, remove it, re-add at user scope, fall back to classic add)
all inline in one `if apply:` block with a `for _ in range(3):` loop and multiple
nested breaks. The migration logic (167-193) is the hard part and deserves to be its
own `_migrate_claude_code_registration(claude, launcher) -> list[str]` helper,
leaving `setup_claude_code` to read as "install commands; migrate registration;
return status." The `for _ in range(3)` bound is also unexplained - why 3 attempts?
*Direction:* extract the migration helper and add a one-line WHY on the retry bound.

**8.3 - Scope-detection ternary is duplicated verbatim between setup and remove - `client_setup.py:178` and `298` - severity: low**
`"local" if "scope: local" in combined else ("user" if "scope: user" in combined
else ("project" if "scope: project" in combined else None))` appears identically in
both `setup_claude_code` (178) and `remove_claude_code` (298). *Direction:* a
`_detect_registered_scope(combined: str) -> str | None` helper used by both. Small,
but it is exact duplication of a fiddly nested ternary.

**8.4 - `remove_codex`'s `ok` is a triple-negative boolean that is genuinely hard to parse - `client_setup.py:350` - severity: low**
`"ok": not (apply and block_present and (path.exists() and "# BEGIN..." in
path.read_text(...)))`. Reading whether success means true requires unwinding a
`not(... and ... and (... and ...))`. *Direction:* compute
`block_present_after` first (see 8.1), then `ok = not (apply and block_present and
block_present_after)` reads as "if we tried to remove a present block, it must be
gone." Depends on 8.1.

**8.5 - Positive: the transactional-write and newline-preservation comments are exactly right.**
`_atomic_write_text` (64-67) and `_read_config_for_edit` (54-55) both explain real
cross-platform WHY (crash-safety, CRLF-vs-LF preservation) that a maintainer would
otherwise not know to keep. `setup_claude_desktop`'s "intentionally no longer
performed" note (227) prevents a plausible "why don't we just edit the JSON" regression.
Good comment hygiene.

**Ollama leads for lifecycle/client_setup:** not run through the reranker (installer/
client-integration code is outside what the ranking prompt is designed to review, and
both modules were fully read). No leads to record.

---

## 9. `neo_localmcp/wizard/*` (console 572, real_backend 406, fake_backend 316, backend 195, preflight 100)

**Verdict:** The wizard is well-architected and the `WizardBackend` Protocol seam
is real, not aspirational - `console.py` depends only on the Protocol, and
`real_backend.py` genuinely delegates every side effect to `installer/`/`config`/
`ollama_client`/`client_setup` with no reimplemented lifecycle policy (verified by
reading it: `run_operation` calls `install`/`reinstall`/`uninstall` directly). The
phase-machine is documented and the trickiest bug-derived decisions carry excellent
WHY comments. Findings are minor: a couple of private-symbol reaches across the
module boundary and a few untyped params.

### Findings

**9.1 - `real_backend` reaches into three private symbols across module boundaries - `real_backend.py:202` (`ollama_client._request_json`), `261` + `270` (`setup_cli._plan_key`, `setup_cli._DRY_RUN_PLANS`) - severity: med**
The backend calls `ollama_client._request_json(...)` (leading underscore = private)
for model sizes, and imports `setup_cli._plan_key` / `setup_cli._DRY_RUN_PLANS`
(both private) for the dry-run plan. The `setup_cli` import even carries a candid
inline comment "private plan tables live here; same repo" (259) - an honest
acknowledgement that this is reaching past the intended API. It works and is
low-risk within one repo, but it couples the wizard to internals that carry no
stability contract; a rename of any of these three silently breaks the wizard with
no type/lint signal. *Direction:* promote the three to public helpers
(`ollama_client.request_json` or a small `model_sizes()` API; a public
`setup_cli.dry_run_plan(key)`), or document them as intentional internal-but-stable.
This is the one real module-boundary finding in the wizard.

**9.2 - `_pick_model`'s `info` parameter is untyped - `console.py:316` - severity: low**
`def _pick_model(self, label: str, hint: list[str], info, current: str) -> str` -
`info` (an `OllamaInfo`) is the only unannotated parameter in the method. Every
other signature in the file is typed. *Direction:* annotate `info: "OllamaInfo"`
(import under `TYPE_CHECKING` to avoid a console->backend runtime import if
undesired). Trivial consistency fix.

**9.3 - `_clear()` shells out via `os.system` - `console.py:40-41` - severity: low**
`os.system("cls" if os.name == "nt" else "clear")` spawns a shell per screen
refresh. For an interactive wizard this is fine and portable, and the alternative
(ANSI escape `\033[2J\033[H`) doesn't work on legacy Windows consoles the project
explicitly supports - so this is arguably the *right* call. Flagged only because
`os.system` is a pattern reviewers reflexively question; a one-line WHY comment
("os.system for cls/clear because ANSI clear is unreliable on legacy Windows
consoles") would pre-empt that. Not a real problem.

**9.4 - Positive: the seam and the bug-derived WHY comments are exemplary.**
`console.py`'s module docstring (1-17) and `_run_phases` comment (180-185) fully
explain the phase machine; `_save_prefs`'s uninstall-skip comment (478-482)
documents the exact real bug PROJECT_NOTES 2026-07-03 (13) fixed (recreating a
just-wiped config dir); `real_backend.py`'s module docstring (1-9) states the
no-added-policy contract that the code then honours. The `# noqa: BLE001` markers on
the broad excepts are paired with a WHY on each ("a broken record file must not
break the UI", "size display is a nice-to-have, never fatal") - broad-except done
right. No noise comments.

**Ollama leads for wizard:** not applicable (installer/UI code is outside the
ranking prompt's remit; modules read directly).

**Not deep-audited (budget):** `wizard/backend.py` (Protocol + dataclasses - by
inspection a clean data-shape module), `wizard/fake_backend.py` (side-effect-free
test double, mirrors the real backend's shape), and `wizard/preflight.py` (stdlib
dependency bootstrap). Spot-reads showed nothing alarming; a future pass could
confirm the fake/real backends haven't drifted in the data shapes they return.

---

## Cross-cutting: docs-vs-code drift (issue #10's explicit "do the docs still describe the code?" ask)

Checked CLAUDE.md's module map and conventions against the actual code:

- **`config.py` "config.yaml"** - drift confirmed (finding C.3): the file is JSON,
  not YAML, despite the name and CLAUDE.md's module-map line. Med severity.
- **CLAUDE.md module map is otherwise accurate.** Spot-verified: `server.py` is the
  FastMCP entrypoint that registers with `lifecycle.py` (true, `server.main` 200-218);
  `tools.py` lists `prepare_context`/`context_prepare`/`file_excerpts`/
  `summarize_file`/`apply_patch` (all present); `repo_memory.py`'s named functions
  `get_boost_map`/`record_task_query`/`record_retrieval_feedback` all exist as
  described; `client_setup.py`'s `setup_*`/`remove_*` + `remove_client`/
  `remove_clients` dispatchers all present; `lifecycle.py`'s `neo-localmcp stop`
  graceful-stop is real. No stale module-map claims found.
- **CLAUDE.md "administration is CLI-only, never exposed as an MCP tool"** - verified
  true: `server.py`'s `@mcp.tool()` set exposes only context/lookup/status/doctor/
  refresh/summarize/apply-patch/record-change/ollama-status-ensure; no index/reset/
  stop/config tool is registered. The boundary claim holds.
- **`doctor`'s self-reported `commands` list (finding 1.6)** is the one place inside
  the code that can silently drift from `cli.py`'s real subcommand set - an
  in-code doc-drift hazard, already filed as finding 1.6.
- **The `context_prepare` one-release-alias** is consistently described as
  compatibility-only in CLAUDE.md, PROJECT_STATUS, `server.py` (106), and the server
  instructions - no drift, just a naming-clarity note (findings 1.5/5.3).

Net: docs are in good shape; the one genuine drift is the JSON-in-a-`.yaml`-file
naming (C.3), plus the self-listed `commands` array (1.6) as an in-code hazard.

---

## Summary and suggested priorities

**Overall:** the "right bones" the owner hoped for are genuinely there. Comment
quality across the codebase is *above* most human-written projects and squarely
matches CLAUDE.md's own "WHY-only" standard - the subtle, bug-derived decisions are
documented, and there is almost no comment noise. `lifecycle.py`, `repo_memory.py`,
and the wizard seam are the strongest work. The recurring quality debt is
**structural, not stylistic**: a few functions/modules carry too much at once, and
there is systematic duplication in the response-rendering (tools.py) and
result-dict-building (ollama_client.py, server.py error wrappers) layers.

**The one thing worth fixing first** is not a readability item at all but the
`.claude`-exclusion gap (C.1) - it actively degrades retrieval quality in this very
repo, was reproduced live, and is a one-line config change.

Ranked actionable set (high/med only - the individual GitHub issues cover these):

| Priority | Finding | Why first |
|---|---|---|
| 1 | C.1 `.claude` not excluded (high) | Live retrieval-quality bug; one-line fix |
| 2 | 1.1 `context_prepare` 8-job function (high) | Highest-leverage readability change |
| 3 | 5.1 + server error-wrapper DRY (med) | 9 copies; a decorator removes them safely |
| 4 | 6.1 ollama result-dict DRY (med) | ~10 copies; a factory fixes envelope drift too |
| 5 | 1.2 three/four renderer copies (med) | Consolidate the projection helpers |
| 6 | C.3 JSON-in-.yaml drift (med) | Real docs-vs-code drift a reader is misled by |
| 7 | 8.1 remove_codex reads file 3x (med) | Redundant I/O + unreadable boolean |
| 8 | 5.2 subprocess asymmetry undocumented (med) | Missing WHY invites a regression |
| 9 | 1.6 doctor commands list drift (med) | In-code sync hazard with cli.py |
| 10 | 3.1 filler/intent word-set DRY (med) | Invariant can silently drift |

Low-severity findings (1.4, 2.1-2.4, 3.2-3.5, 4.1-4.3, 6.3, 8.3-8.4, 9.2-9.3) are
polish - worth a sweep when touching the relevant file, not worth dedicated issues.

**What was NOT found (equally important):** no god-objects beyond tools.py's size,
no circular module dependencies, no comment noise worth flagging, no dead code
spotted in the audited modules, and no docs-drift beyond the two noted. The
correctness audit's "right bones" verdict holds up under a quality lens too.
