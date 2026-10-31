"""Guard: the old concept names (docs/GLOSSARY.md) stay out of the shipped plugin.

Frozen history keeps them on purpose: store.py legacy constants and schema steps, settings.py RETIRED,
the migrate.py docstring that describes the rename, and the README line naming the replaced keys."""
import ast
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PLUGIN = ROOT / "plugins" / "evidence-echo"
OLD = re.compile(r"theme|research_horizon|facts\.entities\b|studies\.entities\b|waiting to be sorted|not_sorted|not-sorted", re.I)
SCAN_DIRS = ("src", "skills", "agents", "templates", "hooks", "scripts")
SUFFIXES = {".py", ".md", ".json", ".html", ".txt", ".toml", ".yaml", ".yml", ".sh"}

# file (relative to the plugin) -> predicate on a top-level node name that may keep old names
FROZEN = {
    "src/evidence_echo/core/store.py": lambda n: n.startswith("_LEGACY_") or n == "_STEP7_COLUMNS"
    or re.fullmatch(r"_step[1-7]", n),
    "src/evidence_echo/core/settings.py": lambda n: n == "RETIRED",
    "src/evidence_echo/core/migrate.py": lambda n: n == "rename_retired_settings",
}


def _frozen_lines(path: Path, allowed) -> set:
    lines = set()
    for node in ast.parse(path.read_text(encoding="utf-8")).body:
        names = []
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            names = [node.name]
        elif isinstance(node, ast.Assign):
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
        elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
            names = [node.target.id]
        if any(allowed(n) for n in names):
            lines.update(range(node.lineno, node.end_lineno + 1))
    return lines


def _files():
    for d in SCAN_DIRS:
        for p in (PLUGIN / d).rglob("*"):
            if p.is_file() and p.suffix in SUFFIXES and "__pycache__" not in p.parts:
                yield p
    yield PLUGIN / "README.md"
    yield PLUGIN / ".mcp.json"


def test_no_old_names_in_plugin():
    hits = []
    for path in _files():
        rel = path.relative_to(PLUGIN).as_posix()
        frozen = _frozen_lines(path, FROZEN[rel]) if rel in FROZEN else set()
        for no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not OLD.search(line) or no in frozen:
                continue
            if rel == "README.md" and "replaces `lookback_days`" in line:
                continue
            hits.append(f"{rel}:{no}: {line.strip()}")
    assert not hits, "old names outside frozen history:\n" + "\n".join(hits)


def test_guard_scans_the_server_files():
    names = {p.relative_to(PLUGIN).as_posix() for p in _files()}
    assert {"scripts/ev_mcp.py", ".mcp.json", "src/evidence_echo/mcp_server.py"} <= names
