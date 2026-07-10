from __future__ import annotations

import ast
import itertools
import subprocess
import sys
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_real_stdio_integration_is_isolated_from_xdist() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("serial:") for marker in markers)

    workflow = (ROOT / ".github" / "workflows" / "setup-v2.yml").read_text(encoding="utf-8")
    # The "other" area bucket (#13) is the one whose parallel "Fast tests" step
    # must still exclude serial tests; the dedicated "Serial tests" step runs
    # them alone, without -n, in that same bucket.
    assert '-m "not retrieval and not ollama and not slow and not serial" --ignore=tests/installer' in workflow
    assert '-m "serial and not slow" --ignore=tests/installer' in workflow
    serial_command = next(line for line in workflow.splitlines() if '-m "serial and not slow"' in line)
    assert " -n " not in serial_command

    distribution_tests = ast.parse(
        (ROOT / "tests" / "test_distribution.py").read_text(encoding="utf-8")
    )
    target = next(
        node
        for node in distribution_tests.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
        and node.name == "test_repo_tools_respond_over_real_stdio"
    )
    decorators = {ast.unparse(decorator) for decorator in target.decorator_list}
    assert "pytest.mark.serial" in decorators


def test_installer_bucket_and_lifecycle_job_skip_on_non_installer_changes() -> None:
    """Regression for #48 phase 1: a PR touching only non-installer code should
    skip the fast job's `installer` bucket and the entire `slow`/lifecycle job
    (every test in it is an installer lifecycle test), while every other area
    still runs. Any change to installer code -- or to this workflow file
    itself, so the skip logic can't silently go unverified by its own future
    edits -- must still run everything, same as before this optimization.
    """
    workflow = (ROOT / ".github" / "workflows" / "setup-v2.yml").read_text(encoding="utf-8")

    for path in (
        "'setup.py'",
        "'neo_localmcp/installer/**'",
        "'packaging/claude-desktop/**'",
        "'tests/installer/**'",
        "'.github/workflows/setup-v2.yml'",
    ):
        assert path in workflow, f"installer filter should list {path}"

    # Fast job: only the installer-area matrix combo is gated by the extra
    # clause; retrieval/ollama/other are untouched by it.
    fast_gate = "matrix.area != 'installer' || steps.changes.outputs.installer == 'true'"
    assert workflow.count(fast_gate) == 2, "expected the fast job's Install + Fast-tests steps to share this gate"

    # Slow job: the whole job is installer-only, so all 3 steps share the same gate.
    slow_gate = "steps.changes.outputs.code == 'true' && steps.changes.outputs.installer == 'true'"
    assert workflow.count(slow_gate) == 3, "expected all 3 slow-job steps to share this gate"


def _collect_node_ids(*args: str) -> set[str]:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "--collect-only", "-q", *args],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    return {line.strip() for line in result.stdout.splitlines() if "::" in line}


def test_every_test_is_covered_by_exactly_one_ci_bucket() -> None:
    """Regression for #13: the CI workflow splits "fast" into per-area buckets
    (installer/retrieval/ollama/other) by pytest marker + path, not by an
    exhaustive file list. If a bucket's -m filter drifts from the actual
    markers (or a new test is added with no marker and no bucket claims it),
    that test would silently stop running in CI with nothing surfacing the
    gap -- the same failure mode #48 (selective execution) warns about for
    path-based selection. This collects each bucket the same way the workflow
    does and asserts they union to the full suite with no gaps or overlaps.
    """
    everything = _collect_node_ids()
    assert everything, "sanity check: pytest collected zero tests"
    buckets = {
        "installer": _collect_node_ids("tests/installer"),
        "retrieval": _collect_node_ids("-m", "retrieval", "--ignore=tests/installer"),
        "ollama": _collect_node_ids("-m", "ollama", "--ignore=tests/installer"),
        "other": _collect_node_ids("-m", "not retrieval and not ollama", "--ignore=tests/installer"),
    }
    union = set().union(*buckets.values())
    missing = everything - union
    assert not missing, f"test(s) collected by no CI bucket, would silently never run: {sorted(missing)}"
    extra = union - everything
    assert not extra, f"CI bucket collected test(s) outside the full suite (should be impossible): {sorted(extra)}"
    for (name_a, set_a), (name_b, set_b) in itertools.combinations(buckets.items(), 2):
        overlap = set_a & set_b
        assert not overlap, f"test(s) collected by both '{name_a}' and '{name_b}' CI buckets (would run twice): {sorted(overlap)}"
