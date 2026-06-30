# V4.2.1 Changes — Determinism Hotfix

- Fixed repeated deterministic `context` calls changing scores due to nondeterministic ripgrep traversal by using `rg --sort path`.
- Sorted filesystem traversal during indexing.
- Changed candidate category boosts to apply once per file instead of once per evidence hit.
- Filtered FTS lookup to repository context rows only and added stable ordering.
- Stopped recording context queries by default so context lookup does not mutate the DB. Set `NEO_LOCALMCP_RECORD_CONTEXT_QUERIES=1` to opt in.
- Prioritized direct task line hints over generic early symbols in compact guidance.
- Bumped indexer version to `0.4.2.1`; run `neo-localmcp reindex` after install.
