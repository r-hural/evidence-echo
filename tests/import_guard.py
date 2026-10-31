"""Runs code in a child interpreter that refuses every import outside the standard library and evidence_echo."""
import os
import subprocess
import sys

from tests.conftest import PLUGIN_DIR

_PRELUDE = '''
import sys
sys.path.insert(0, {src!r})


class _StdlibOnly:
    def find_spec(self, name, path=None, target=None):
        top = name.split(".")[0]
        if top not in sys.stdlib_module_names and top != "evidence_echo":
            raise ImportError("non-stdlib import: " + name)


sys.meta_path.insert(0, _StdlibOnly())
'''


def run_guarded(code: str, stdin: bytes = b"", env=None) -> subprocess.CompletedProcess:
    src = str(PLUGIN_DIR / "src")
    return subprocess.run([sys.executable, "-c", _PRELUDE.format(src=src) + code], input=stdin,
                          capture_output=True, env=env if env is not None else os.environ.copy(),
                          timeout=120)
