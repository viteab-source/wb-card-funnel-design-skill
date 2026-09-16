#!/usr/bin/env python3
"""Install the bundled skill and prepare its private runtime on macOS or Windows."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


REPO_ROOT = Path(__file__).resolve().parent
SOURCE = REPO_ROOT / "wb-card-funnel-design"


def ignore_files(_path: str, names: list[str]) -> set[str]:
    return {
        name for name in names
        if name == ".runtime" or name == "__pycache__" or name.endswith((".pyc", ".pyo"))
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    default_home = Path(os.environ.get("CODEX_HOME", Path.home() / ".codex"))
    parser.add_argument("--dest", type=Path, default=default_home / "skills", help="skills directory")
    parser.add_argument("--skip-bootstrap", action="store_true")
    args = parser.parse_args()

    destination = args.dest.expanduser().resolve() / SOURCE.name
    if destination.exists():
        raise SystemExit(f"destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SOURCE, destination, ignore=ignore_files)

    bootstrap_result = "skipped"
    if not args.skip_bootstrap:
        command = [sys.executable, str(destination / "scripts" / "bootstrap_runtime.py"), "--json"]
        subprocess.run(command, check=True)
        command = [
            sys.executable, str(destination / "scripts" / "run_tool.py"),
            "doctor.py", "--json", "--strict-local",
        ]
        subprocess.run(command, check=True)
        bootstrap_result = "ready"

    print(json.dumps({
        "status": "installed",
        "skill": SOURCE.name,
        "destination": str(destination),
        "runtime": bootstrap_result,
        "available": "next Codex turn",
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
