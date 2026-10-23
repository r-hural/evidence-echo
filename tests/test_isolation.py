import os
import subprocess
import sys
from pathlib import Path


def test_child_processes_get_a_temp_home(tmp_path):
    out = subprocess.run([sys.executable, "-c", "from pathlib import Path; print(Path.home())"],
                         capture_output=True, text=True, env=os.environ.copy(), check=True)
    assert Path(out.stdout.strip()) == tmp_path / "home"
