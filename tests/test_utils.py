from __future__ import annotations

from neo_localmcp import utils


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
