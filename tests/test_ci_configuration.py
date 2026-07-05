from __future__ import annotations

import ast
import tomllib
from pathlib import Path


ROOT = Path(__file__).parents[1]


def test_real_stdio_integration_is_isolated_from_xdist() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    markers = pyproject["tool"]["pytest"]["ini_options"]["markers"]
    assert any(marker.startswith("serial:") for marker in markers)

    workflow = (ROOT / ".github" / "workflows" / "setup-v2.yml").read_text(encoding="utf-8")
    assert '-m "not slow and not serial" -n auto' in workflow
    assert '-m "serial and not slow"' in workflow
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
