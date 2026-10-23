import ast
import tomllib

from tests.conftest import PLUGIN_DIR


def _py_files():
    for sub in ("src", "scripts", "hooks"):
        yield from sorted((PLUGIN_DIR / sub).rglob("*.py"))


def test_sources_parse_as_python_311():
    files = list(_py_files())
    assert files
    for path in files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path),
                  feature_version=(3, 11))


def test_requires_python_floor():
    data = tomllib.loads((PLUGIN_DIR / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["requires-python"] == ">=3.11"
