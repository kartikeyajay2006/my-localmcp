from __future__ import annotations

import json
import sys
import traceback
from typing import Any

from . import tools


def _configure_utf8_stdio() -> None:
    """Keep the JSON/text worker protocol independent of the Windows code page."""
    for stream in (sys.stdin, sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if callable(reconfigure):
            reconfigure(encoding="utf-8", errors="replace")


def main() -> int:
    _configure_utf8_stdio()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        task = str(payload.get("task") or "")
        repo_root = payload.get("repo_root") or "auto"
        max_files = int(payload.get("max_files") or 80)
        limit = int(payload.get("limit") or 5)
        use_ollama = bool(payload.get("use_ollama"))
        model = payload.get("model")
        output_format = payload.get("output_format") or "mcp_text"
        token_budget = int(payload.get("token_budget") or 3000)
        if not task.strip():
            print("neo-localmcp context_prepare error: missing task")
            return 2
        result = tools.context_prepare(
            task,
            repo_root,
            max_files=max_files,
            limit=limit,
            use_ollama=use_ollama,
            model=model,
            output_format=output_format,
            token_budget=token_budget,
        )
        # Keep worker stdout as the tool payload only. Diagnostics belong on stderr.
        sys.stdout.write(result)
        if not result.endswith("\n"):
            sys.stdout.write("\n")
        return 0
    except Exception as exc:
        err: dict[str, Any] = {
            "ok": False,
            "product": "neo-localmcp",
            "worker": "context_worker",
            "error": str(exc),
            "traceback_tail": traceback.format_exc()[-4000:],
        }
        print(json.dumps(err, indent=2, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
