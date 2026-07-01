from __future__ import annotations

import os
import subprocess
from pathlib import Path


def test_build_mcpb_sh_reads_version_with_macos_sed(tmp_path: Path) -> None:
    root = Path(__file__).parents[1]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_npx = fake_bin / "npx"
    fake_npx.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    fake_npx.chmod(0o755)
    target = tmp_path / "bundle.mcpb"

    result = subprocess.run(
        ["bash", str(root / "scripts" / "build-mcpb.sh"), str(target)],
        cwd=root,
        env={**os.environ, "PATH": f"{fake_bin}:{os.environ['PATH']}"},
        capture_output=True,
        text=True,
        timeout=10,
    )

    assert result.returncode == 0, result.stderr
    assert f"Built {target}" in result.stdout
