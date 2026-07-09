from __future__ import annotations

import copy
import json
import re

from neo_localmcp import config, repo_utils as utils


def _seed(repo, real_files, excluded_dirs):
    for rel_path in real_files:
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("VALUE = 1\n", encoding="utf-8")
    for dir_name, rel_path in excluded_dirs:
        path = repo / dir_name / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("noise = 1\n", encoding="utf-8")


def test_exact_configured_dir_names_are_still_excluded(tmp_path, isolated_config):
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(repo, ["real.py"], [(".git", "config.py"), ("node_modules", "pkg.py"), (".venv", "site.py")])

    found, eligible, complete = utils.scan_repo_files(repo)
    rels = {utils.rel(p, repo) for p in found}

    assert rels == {"real.py"}
    assert eligible == 1
    assert complete is True


def test_claude_worktree_dir_is_excluded(tmp_path, isolated_config):
    """Regression: `.claude/worktrees/` holds full sibling repo copies for parallel
    agent sessions -- without this exclusion, each duplicate repo_memory.py/utils.py
    outranks the real working-tree file (issue #28, same class of bug as the
    .venv* case above; reproduced live 2026-07-04, see docs/CODE_QUALITY_AUDIT.md
    finding C.1)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(
        repo,
        ["real.py"],
        [(".claude", "worktrees/agent-abc123/neo_localmcp/repo_memory.py")],
    )

    found, eligible, complete = utils.scan_repo_files(repo)
    rels = {utils.rel(p, repo) for p in found}

    assert rels == {"real.py"}
    assert eligible == 1
    assert complete is True


def _write_persisted_config(cfg: dict) -> None:
    path = config.CONFIG_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(cfg, indent=2), encoding="utf-8")


def test_stale_persisted_exclude_dirs_cannot_defeat_code_owned_excludes(tmp_path, isolated_config):
    """A persisted config whose `repo.exclude_dirs` predates a newly-added
    default (e.g. `.claude`) must not defeat that default. Safety excludes are
    code-owned; a persisted list can add, never override. Regression for #41:
    reinstall preserves an old config.yaml, so before this fix the `.claude`
    exclusion (#28) never reached existing installs -- verified live 2026-07-04
    ranking a `.claude/worktrees/` copy as READ FIRST #1."""
    stale = copy.deepcopy(config.DEFAULT_CONFIG)
    stale["repo"]["exclude_dirs"] = [d for d in stale["repo"]["exclude_dirs"] if d != ".claude"]
    _write_persisted_config(stale)

    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(repo, ["real.py"], [(".claude", "worktrees/agent-abc123/neo_localmcp/repo_memory.py")])

    found, eligible, complete = utils.scan_repo_files(repo)
    rels = {utils.rel(p, repo) for p in found}

    assert rels == {"real.py"}
    assert eligible == 1
    assert complete is True


def test_pytest_cache_dir_is_excluded_by_default(tmp_path, isolated_config):
    """`.pytest_cache` is a generic tooling artifact (like `.git`/`node_modules`)
    and must not be indexed as repository source (#41)."""
    repo = tmp_path / "repo"
    repo.mkdir()
    # `.pytest_cache/README.md` is a real, text-detected artifact -- exactly what
    # polluted context live on 2026-07-04 -- so it is indexed unless the dir is excluded.
    _seed(repo, ["real.py"], [(".pytest_cache", "README.md")])

    found, eligible, complete = utils.scan_repo_files(repo)
    rels = {utils.rel(p, repo) for p in found}

    assert rels == {"real.py"}
    assert eligible == 1
    assert complete is True


def test_extra_exclude_dirs_are_added_to_code_owned_excludes(tmp_path, isolated_config):
    """Users add their own excluded directories via `repo.extra_exclude_dirs`;
    these stack on top of the code-owned safety set rather than replacing it, so
    both a built-in exclude and a user exclude are honored at once (#41)."""
    cfg = copy.deepcopy(config.DEFAULT_CONFIG)
    cfg["repo"]["extra_exclude_dirs"] = ["my_generated"]
    _write_persisted_config(cfg)

    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(repo, ["real.py"], [(".git", "config.py"), ("my_generated", "artifact.py")])

    found, eligible, complete = utils.scan_repo_files(repo)
    rels = {utils.rel(p, repo) for p in found}

    assert rels == {"real.py"}
    assert eligible == 1
    assert complete is True


def test_differently_named_venv_dirs_are_excluded(tmp_path, isolated_config):
    """Regression: a leftover disposable venv with a non-exact name (e.g. a local
    Windows lifecycle test's '.venv-phase14', or this project's own installer
    convention '.venv-nlm-v1.0.10') must not be indexed as repository source --
    reproduced live 2026-07-03, see PROJECT_NOTES.md."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(
        repo,
        ["real.py"],
        [
            (".venv-phase14", "Lib/site-packages/_pytest/__init__.py"),
            (".venv-nlm-v1.0.10", "Scripts/python.exe"),
            ("venv-old", "lib/site.py"),
            ("venvs", "20260101-1234/lib/site.py"),
        ],
    )

    found, eligible, complete = utils.scan_repo_files(repo)
    rels = {utils.rel(p, repo) for p in found}

    assert rels == {"real.py"}
    assert eligible == 1
    assert complete is True


def test_dirs_that_merely_contain_venv_substring_are_not_excluded(tmp_path, isolated_config):
    """The broadened pattern must stay a prefix match, not a substring match --
    a real source directory that happens to contain 'venv' elsewhere in its name
    should still be indexed."""
    repo = tmp_path / "repo"
    repo.mkdir()
    _seed(repo, ["real.py", "eventvenue/handler.py"], [])

    found, eligible, complete = utils.scan_repo_files(repo)
    rels = {utils.rel(p, repo) for p in found}

    assert rels == {"real.py", "eventvenue/handler.py"}
    assert eligible == 2
    assert complete is True


def test_rg_search_fallback_interprets_the_query_as_a_regex(tmp_path, isolated_config, monkeypatch):
    """Regression: on a host without ripgrep on PATH (e.g. GitHub's macos-latest
    CI runner has no rg preinstalled, unlike the windows-latest runner used
    during local development), the pure-Python fallback path did a literal
    substring match of the *entire* regex-syntax query string -- including the
    '(?:...|...)' alternation syntax mcp/memory.py always sends it -- against each
    line, so it silently matched nothing at all, for single or multi-term
    batches alike. Surfaced live by CI on 2026-07-03; see PROJECT_NOTES.md."""
    monkeypatch.setattr(utils, "which", lambda name: None)
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "module_a.py").write_text("def handler():\n    # marks RareMarkerNeedle usage here\n    return 1\n", encoding="utf-8")

    pattern = "(?:" + "|".join(re.escape(t) for t in ["worker", "RareMarkerNeedle"]) + ")"
    rows = utils.rg_search(pattern, repo)

    assert any(row.get("path") == "module_a.py" and row.get("line") == 2 for row in rows)
