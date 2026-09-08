from __future__ import annotations

import subprocess
import sys
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> None:
    # Compare fresh output with all targets, including already-dirty and untracked
    # files. Git status alone misses edits to files that were dirty before rendering.
    result = subprocess.run(
        [sys.executable, str(REPO_ROOT / "scripts/generate_network_variable_tabs.py"), "--check"],
        cwd=REPO_ROOT,
        check=False,
    )
    raise SystemExit(result.returncode)


if __name__ == "__main__":
    main()
