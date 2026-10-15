"""The offline tier must work with no database at all.

`pytest -m "not db"` is only worth having if it never touches the network.
This runs it in a child process pointed at a port nothing listens on, so a
test that was not marked (the marker is a text heuristic in conftest.py)
fails here, by name, instead of surprising someone on a day the network is
down.
"""

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def test_the_offline_tier_passes_with_no_database():
    env = {
        **os.environ,
        "DB_HOST": "127.0.0.1",
        "DB_PORT": "1",
        "DB_PASSWORD": "unused",
        "FG_TEST_NO_CONNECT_RETRY": "1",
    }
    result = subprocess.run(
        [
            sys.executable, "-m", "pytest", "-m", "not db", "-q", "-p", "no:cacheprovider",
            "--ignore=tests/test_offline_tier.py", "-x",
        ],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=600,
    )
    tail = "\n".join(result.stdout.splitlines()[-25:])
    assert result.returncode == 0, f"offline tier failed with no database:\n{tail}"
