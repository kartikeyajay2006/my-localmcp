# Dogfooding notes — Issue #7 (schema migration-safety regression test)

Running log of using neo-localmcp's own CLI (`context`/`lookup`/`file`) to do this task.

## Setup

- `neo-localmcp` on PATH at `/c/users/neel/appdata/roaming/python/python314/scripts/neo-localmcp`.
- Working in worktree `agent-a1cdf4b932c12982a`. Repo memory is centralized (`~/.neo-localmcp/repo-context.sqlite`), shared across all indexed repos, keyed by repo_id (canonical root + git remote).

## Queries run (chronological)

### Query 1 — `neo-localmcp context "schema evolution and migration in repo memory: init_db, CREATE TABLE, ALTER TABLE ADD COLUMN, INDEXER_VERSION" --repo-root . --token-budget 1500`

- **Parser behavior:** Strong terms extracted `init_db, TABLE, ALTER, COLUMN, INDEXER_VERSION`; weak terms `schema, evolution, migration, memory`; ignored filler `and, in, repo`. Reasonable split.
- **Ranking result (friction, worth an issue):** the #1 "Read first" hit was `neo_localmcp/installer/migration.py` (score 263) — a **filesystem-layout** migration module (legacy install dirs), which is semantically unrelated to *SQL schema* migration. It outranked the actual target `neo_localmcp/repo_memory.py` (score 148, ranked #3). The word "migration" is overloaded in this repo, and the ranker keyed on the literal token match in the filename/symbols rather than the schema/SQL terms that pin the real intent. `tests/installer/test_migration.py` (#6) is likewise the wrong "migration."
- **Did it point me to the right code fast?** Partially. `repo_memory.py` *was* in the top 3 and the guidance explicitly listed `init_db around line 38`, which is exactly right. But the top slot was a false lead; a less careful agent following "Read first: 1." literally would have opened the wrong file first. I already knew from the issue that `repo_memory.py` was the target, so I wasn't misled — but the ranking would mislead someone who didn't.
- **Line hints:** The `repo_memory.py` hints (`around line 38`, `connect around line 29`, `get_repo_meta around line 152`, `set_repo_meta around line 157`) centered on `init_db` correctly. Good.

### Query 2 — `neo-localmcp lookup "init_db" --repo-root .`

- **Result:** Clean, exact, fast. Single symbol hit `neo_localmcp/repo_memory.py:init_db` with `start_line: 38, end_line: 118`. This is `lookup` working exactly as intended — a known symbol name resolves precisely. No friction. This is the CLI's strongest surface for this kind of task.
- The `start_line/end_line` (38–118) let me Read exactly the right span had I wanted to; I read the whole file anyway since I needed the full schema + every ALTER/CREATE and the surrounding indexing functions to design a faithful "old schema" seed.

## Observations / friction summary

1. **"migration" overload mis-ranks (the one real gap).** For a task about *SQL schema* migration, the top-ranked result was *filesystem-layout* migration code. Pure lexical token matching on an overloaded word, with no disambiguation from the co-occurring strong terms (`init_db`, `ALTER TABLE`, `COLUMN`) that clearly scope the intent to SQL. Filed as an issue (see below).
2. **`lookup` is excellent for known symbols.** When you can name the thing (`init_db`), `lookup` beats `context` decisively — exact file + line range, zero noise.
3. **`context` guidance text is genuinely useful** ("Do not grep broadly yet; use this result to narrow the first reads" + explicit line hints). It correctly nudged me away from broad grep. The one caveat is that "Read first: 1." implies confidence in the #1 slot that wasn't warranted here.
4. No flag/output-formatting friction. Output is readable plaintext; the estimated-tokens footer did not render in these runs (see token note below).

## Token-cost comparison: with-MCP vs. approximate without-MCP

**Caveat:** these are estimates. Token counts here are char-derived (`chars ÷ 4 ≈ tokens`) — the same known limitation called out in `PROJECT_STATUS.md` (counts are estimated from returned characters until real client usage telemetry exists). Treat as order-of-magnitude, not precise.

Note: neither `context` nor `lookup` printed an `estimated_tokens`/budget footer in these CLI runs (the footer is described as part of the MCP-tool response; the bare `context` CLI output here did not include it), so the with-MCP figures below are measured from the actual stdout size I received.

| Step | With-MCP (actual output) | Without-MCP (counterfactual raw read) | Basis |
|---|---|---|---|
| Find schema/init_db code (Query 1 `context`) | ~2,600 chars out ≈ **~650 tok** | Grep "migration"/"init_db"/"schema" then read the candidate files end-to-end to figure out which is the SQL one: `repo_memory.py` (~33,600 chars) + `installer/migration.py` (~6,900) + at least skim `config.py` for `db_path` (~7,700) ≈ 48,200 chars ≈ **~12,050 tok** | with-MCP = size of context stdout; without = whole-file sizes I'd otherwise have read to locate + disambiguate |
| Resolve `init_db` exactly (Query 2 `lookup`) | ~600 chars out ≈ **~150 tok** | (subsumed — I still had to read `repo_memory.py` fully to write a faithful seed, so `lookup` mainly *confirmed* the location cheaply) | lookup stdout size |

**Honest accounting for *this specific* task:** I still ended up reading `repo_memory.py` in full (33,600 chars ≈ 8,400 tok) via `Read`, because writing a faithful "old schema" seed required seeing every `CREATE TABLE`, every `ALTER TABLE`, and the surrounding indexing functions — a whole-file read was genuinely necessary here, not avoidable by excerpts. I also read `tests/test_retrieval_memory.py` (~28,000 chars ≈ 7,000 tok) and `conftest.py` (small) to match conventions. So the MCP did **not** save me the big read on this task — the task inherently needed the whole schema surface.

**Where the MCP did save tokens:** the *discovery/localization* step. Without it, disambiguating "which migration code is the SQL one" would have meant reading `installer/migration.py` + `test_migration.py` + skimming `config.py` before I even confirmed `repo_memory.py` was the target — roughly 12,050 tok of exploratory reading — versus ~800 tok of `context`+`lookup` output that pointed me (mostly) at the right file and the exact `init_db` line range. Net discovery saving ≈ **~11,250 tok (~93% on the discovery phase)**, even after docking the mis-ranked #1 hit.

**Bottom line:** On the *discovery* axis this task is comfortably inside the ≥50% target (~93% fewer discovery tokens). On *total task tokens* the win is smaller and more honest: the implementation genuinely required a full read of `repo_memory.py` regardless, so the MCP's leverage was concentrated in localization, not in replacing the core read. That's the expected shape for a "I must understand this whole module to extend it faithfully" task, as opposed to a "jump to one function and patch it" task where excerpt-only retrieval would have saved the big read too. Not a failure of the tool — just a task whose irreducible cost is reading the thing being tested.
</content>
</invoke>
