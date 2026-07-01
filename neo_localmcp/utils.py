from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Iterable

from .config import load_config


def hidden_subprocess_kwargs() -> dict[str, int]:
    """Keep short-lived helpers from allocating console hosts on Windows."""
    if os.name == "nt":
        return {"creationflags": int(subprocess.CREATE_NO_WINDOW)}
    return {}


def run_command(args: list[str], cwd: Path | None = None, timeout: int = 30) -> dict[str, Any]:
    try:
        # Never let Git or another helper inherit the MCP server's protocol stdin.
        # On stdio transports an inherited handle can consume or retain JSON-RPC
        # traffic, making a completed tool handler appear to hang forever.
        proc = subprocess.run(
            args, cwd=str(cwd) if cwd else None, stdin=subprocess.DEVNULL,
            capture_output=True, text=True, errors="replace", timeout=timeout,
            **hidden_subprocess_kwargs(),
        )
        return {"returncode": proc.returncode, "stdout": proc.stdout, "stderr": proc.stderr, "cmd": args}
    except FileNotFoundError:
        return {"returncode": 127, "stdout": "", "stderr": f"Command not found: {args[0]}", "cmd": args}
    except subprocess.TimeoutExpired as exc:
        return {"returncode": 124, "stdout": exc.stdout or "", "stderr": f"Timed out after {timeout}s\n{exc.stderr or ''}", "cmd": args}


def which(name: str) -> str | None:
    return shutil.which(name)


def git_root(start: Path | None = None) -> Path | None:
    start = (start or Path.cwd()).expanduser().resolve()
    cwd = start if start.is_dir() else start.parent
    result = run_command(["git", "rev-parse", "--show-toplevel"], cwd=cwd, timeout=10)
    if result["returncode"] == 0 and result["stdout"].strip():
        return Path(result["stdout"].strip()).resolve()
    cur = cwd
    for candidate in [cur, *cur.parents]:
        if (candidate / ".git").exists():
            return candidate.resolve()
    return None


def repo_root_or_cwd(repo_root: str | Path | None = None) -> Path:
    cfg = load_config()
    if repo_root and str(repo_root) not in {".", "auto"}:
        return Path(repo_root).expanduser().resolve()
    env_root = os.environ.get("NEO_LOCALMCP_REPO")
    if env_root:
        return Path(env_root).expanduser().resolve()
    detected = git_root(Path.cwd())
    if detected:
        return detected
    default_root = cfg.get("repo", {}).get("default_root", "auto")
    if default_root and default_root not in {".", "auto"}:
        return Path(default_root).expanduser().resolve()
    return Path.cwd().resolve()


def safe_path(path: str | Path, repo_root: str | Path | None = None) -> Path:
    root = repo_root_or_cwd(repo_root)
    p = Path(path).expanduser()
    if not p.is_absolute():
        p = root / p
    resolved = p.resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Path escapes repo root: {resolved} not under {root}") from exc
    return resolved


def rel(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path)


def read_text_file(path: Path, max_chars: int | None = None) -> str:
    data = path.read_bytes()
    if b"\x00" in data[:4096]:
        raise ValueError(f"Binary file refused: {path}")
    text = data.decode("utf-8", errors="replace")
    if max_chars and len(text) > max_chars:
        return text[:max_chars] + f"\n\n[TRUNCATED: file had {len(text)} chars, returned first {max_chars}]"
    return text


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def language_for_path(path: Path) -> str:
    name = path.name.lower()
    suffix = path.suffix.lower()
    return {
        ".py": "python", ".cs": "csharp", ".xaml": "xaml", ".xml": "xml", ".json": "json",
        ".yml": "yaml", ".yaml": "yaml", ".toml": "toml", ".md": "markdown", ".js": "javascript",
        ".jsx": "javascript", ".ts": "typescript", ".tsx": "typescript", ".vue": "vue", ".svelte": "svelte",
        ".ps1": "powershell", ".sh": "shell", ".bat": "batch", ".cmd": "batch", ".sql": "sql",
        ".go": "go", ".rs": "rust", ".java": "java", ".kt": "kotlin", ".swift": "swift",
        ".rb": "ruby", ".php": "php", ".html": "html", ".css": "css", ".scss": "scss",
        ".csproj": "xml", ".sln": "solution",
    }.get(suffix, "dockerfile" if name == "dockerfile" else "text")


def is_probably_text(path: Path) -> bool:
    cfg = load_config()
    includes = set(str(x).lower() for x in cfg.get("repo", {}).get("include_extensions", []))
    return path.suffix.lower() in includes or path.name in includes or path.name in {"Dockerfile", "Makefile", "Rakefile", "Gemfile"}


def scan_repo_files(
    repo_root: str | Path | None = None,
    folder: str = ".",
    max_files: int | None = None,
) -> tuple[list[Path], int, bool]:
    """Return selected files, total eligible files, and whether selection is complete.

    Traversal always counts the full eligible manifest. This prevents a capped index
    from looking complete merely because iteration stopped at the cap.
    """
    cfg = load_config()
    root = repo_root_or_cwd(repo_root)
    start = safe_path(folder, root)
    exclude_dirs = set(cfg.get("repo", {}).get("exclude_dirs", []))
    configured_limit = cfg.get("repo", {}).get("max_files")
    limit_value = max_files if max_files is not None else configured_limit
    limit = int(limit_value) if limit_value not in (None, "", 0, "0") else None
    max_file_bytes = int(cfg.get("repo", {}).get("max_file_bytes", 750_000))
    if start.is_file():
        selected = [start] if is_probably_text(start) else []
        return selected, len(selected), True
    found: list[Path] = []
    eligible = 0
    for dirpath, dirnames, filenames in os.walk(start):
        dirnames[:] = sorted(d for d in dirnames if d not in exclude_dirs and not d.endswith(".egg-info") and d != "__pycache__" and not d.startswith(".neo-localmcp"))
        for filename in sorted(filenames):
            p = Path(dirpath) / filename
            if not is_probably_text(p):
                continue
            try:
                if p.stat().st_size > max_file_bytes:
                    continue
            except OSError:
                continue
            eligible += 1
            if limit is None or len(found) < limit:
                found.append(p)
    return found, eligible, len(found) == eligible


def iter_repo_files(repo_root: str | Path | None = None, folder: str = ".", max_files: int | None = None) -> list[Path]:
    return scan_repo_files(repo_root, folder=folder, max_files=max_files)[0]


def git_info(root: Path) -> dict[str, Any]:
    branch = run_command(["git", "branch", "--show-current"], cwd=root, timeout=10)
    commit = run_command(["git", "rev-parse", "HEAD"], cwd=root, timeout=10)
    remote = run_command(["git", "remote", "get-url", "origin"], cwd=root, timeout=10)
    status = run_command(["git", "status", "--porcelain"], cwd=root, timeout=10)
    return {
        "branch": branch["stdout"].strip() if branch["returncode"] == 0 else None,
        "commit": commit["stdout"].strip() if commit["returncode"] == 0 else None,
        "remote": remote["stdout"].strip() if remote["returncode"] == 0 else None,
        "dirty_files": len(status["stdout"].splitlines()) if status["returncode"] == 0 else None,
    }


def repo_id(root: Path) -> str:
    info = git_info(root)
    canonical_root = os.path.normcase(str(root.resolve()))
    remote = str(info.get("remote") or "")
    digest = hashlib.sha256(f"{canonical_root}\n{remote}".encode("utf-8")).hexdigest()[:20]
    label = re.sub(r"[^A-Za-z0-9_.-]+", "_", root.name).strip("_") or "repo"
    return f"{label[:80]}-{digest}"


def extract_markdown_headings(text: str) -> list[dict[str, Any]]:
    """Return ATX headings as addressable section symbols.

    Each heading spans from its own line to just before the next heading of
    equal-or-higher level (or EOF), giving prose docs the same line-range
    addressability code symbols already have. Headings inside fenced code blocks
    are ignored so example markdown does not create phantom sections.
    """
    lines = text.splitlines()
    found: list[dict[str, Any]] = []
    fence: str | None = None  # active code-fence marker ('```' or '~~~')
    for idx, line in enumerate(lines, start=1):
        stripped = line.strip()
        if fence is not None:
            if stripped.startswith(fence):
                fence = None
            continue
        if stripped.startswith("```") or stripped.startswith("~~~"):
            fence = stripped[:3]
            continue
        # CommonMark: 4+ leading spaces is an indented code block, not a heading.
        if len(line) - len(line.lstrip(" ")) >= 4:
            continue
        m = re.match(r"^(#{1,6})\s+(.+?)\s*#*\s*$", stripped)
        if not m:
            continue
        name = m.group(2).strip()
        if name:
            found.append({"level": len(m.group(1)), "name": name[:300], "signature": stripped[:300], "start_line": idx})
    total = len(lines)
    rows: list[dict[str, Any]] = []
    for i, h in enumerate(found):
        end = total
        for nxt in found[i + 1:]:
            if nxt["level"] <= h["level"]:
                end = nxt["start_line"] - 1
                break
        rows.append({"kind": "heading", "name": h["name"], "signature": h["signature"], "start_line": h["start_line"], "end_line": max(h["start_line"], end)})
    return rows[:400]


def extract_symbols(text: str, language: str) -> list[dict[str, Any]]:
    if language == "markdown":
        return extract_markdown_headings(text)
    rows: list[dict[str, Any]] = []
    lines = text.splitlines()
    patterns: list[tuple[str, str]] = []
    if language == "python":
        patterns = [("class", r"^\s*class\s+([A-Za-z_]\w*)"), ("function", r"^\s*(?:async\s+)?def\s+([A-Za-z_]\w*)\s*\(")]
    elif language == "csharp":
        patterns = [
            ("type", r"\b(?:class|record|struct|interface|enum)\s+([A-Za-z_]\w*)"),
            ("method", r"\b(?:public|private|protected|internal|static|async|virtual|override|sealed|partial|extern|\s)+\s*[A-Za-z_<>,\[\]?]+\s+([A-Za-z_]\w*)\s*\("),
            ("property", r"\b(?:public|private|protected|internal|static|virtual|override|sealed|partial|\s)+\s*[A-Za-z_<>,\[\]?]+\s+([A-Za-z_]\w*)\s*\{"),
        ]
    elif language in {"typescript", "javascript"}:
        patterns = [
            ("class", r"\bclass\s+([A-Za-z_]\w*)"),
            ("function", r"\bfunction\s+([A-Za-z_]\w*)\s*\("),
            ("function", r"\b(?:const|let|var)\s+([A-Za-z_]\w*)\s*=\s*(?:async\s*)?\(?"),
            ("export", r"\bexport\s+(?:default\s+)?(?:class|function|const|let|var|interface|type)\s+([A-Za-z_]\w*)"),
        ]
    elif language == "swift":
        patterns = [("type", r"\b(?:class|struct|enum|protocol)\s+([A-Za-z_]\w*)"), ("function", r"\bfunc\s+([A-Za-z_]\w*)\s*\(")]
    elif language in {"xaml", "xml"}:
        patterns = [("named_element", r"\b(?:x:Name|Name)\s*=\s*[\"']([^\"']+)[\"']"), ("binding", r"\bBinding\s+([A-Za-z_]\w*)")]

    for idx, line in enumerate(lines, start=1):
        for kind, pattern in patterns:
            m = re.search(pattern, line)
            if m:
                rows.append({"kind": kind, "name": m.group(1), "signature": line.strip()[:300], "start_line": idx, "end_line": min(len(lines), idx + 80)})
    return rows[:400]


def rg_search(query: str, root: Path, max_results: int = 80) -> list[dict[str, Any]]:
    if which("rg"):
        # --sort path disables ripgrep's parallel traversal ordering, making repeated deterministic context calls stable.
        cmd = ["rg", "--line-number", "--column", "--hidden", "--sort", "path", "--glob", "!.git", "--ignore-case", query, str(root)]
        result = run_command(cmd, cwd=root, timeout=20)
        if result["returncode"] not in (0, 1):
            return [{"error": result["stderr"] or result["stdout"]}]
        rows: list[dict[str, Any]] = []
        for raw in result["stdout"].splitlines()[:max_results]:
            m = re.match(r"^(.*?):(\d+):(\d+):(.*)$", raw)
            if m:
                rows.append({"path": rel(Path(m.group(1)), root), "line": int(m.group(2)), "column": int(m.group(3)), "text": m.group(4).strip()})
            else:
                rows.append({"raw": raw})
        return rows
    needle = query.lower()
    rows = []
    for p in iter_repo_files(root, max_files=3000):
        try:
            for idx, line in enumerate(read_text_file(p, 500_000).splitlines(), start=1):
                if needle in line.lower():
                    rows.append({"path": rel(p, root), "line": idx, "column": max(1, line.lower().find(needle) + 1), "text": line.strip()})
                    if len(rows) >= max_results:
                        return rows
        except Exception:
            continue
    return rows


def simple_terms(text: str, limit: int = 10) -> list[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_]{2,}", text)
    stop = {"this", "that", "with", "from", "when", "where", "will", "have", "after", "before", "into", "using", "error", "issue", "problem", "the", "and", "for", "not", "can", "does", "what", "why", "how", "we", "are", "building", "debugging"}
    counts: dict[str, int] = {}
    for word in words:
        lw = word.lower()
        if lw in stop:
            continue
        counts[word] = counts.get(word, 0) + 1
    return [w for w, _ in sorted(counts.items(), key=lambda item: item[1], reverse=True)[:limit]]
