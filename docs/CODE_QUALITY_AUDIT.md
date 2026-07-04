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
| — | `config.py` | Solid tuning comments, but `.claude` missing from `exclude_dirs` (high, causes worktree pollution), constants/functions DRY split, JSON-in-.yaml drift. 4 findings (1 high, 2 med). |

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
