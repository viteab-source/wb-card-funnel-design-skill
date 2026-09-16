#!/usr/bin/env python3
"""Create a private cross-platform runtime for this skill without admin rights."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import venv


SKILL_ROOT = Path(__file__).resolve().parent.parent
RUNTIME_DIR = SKILL_ROOT / ".runtime"
STATE_FILE = RUNTIME_DIR / "bootstrap-state.json"
REQUIREMENTS = SKILL_ROOT / "requirements.txt"


def runtime_python() -> Path:
    if os.name == "nt":
        return RUNTIME_DIR / "Scripts" / "python.exe"
    return RUNTIME_DIR / "bin" / "python"


def fingerprint() -> str:
    digest = hashlib.sha256()
    digest.update(REQUIREMENTS.read_bytes())
    digest.update(f"{sys.version_info.major}.{sys.version_info.minor}".encode())
    digest.update(sys.platform.encode())
    return digest.hexdigest()


def current_state() -> dict:
    try:
        return json.loads(STATE_FILE.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def runtime_ready() -> bool:
    state = current_state()
    return runtime_python().exists() and state.get("fingerprint") == fingerprint()


def ensure_runtime(force: bool = False) -> dict:
    if sys.version_info < (3, 10):
        raise RuntimeError("Python 3.10 or newer is required")
    if runtime_ready() and not force:
        return current_state()

    # A changed Python minor version or requirements file must not reuse an old venv.
    builder = venv.EnvBuilder(with_pip=True, clear=True)
    builder.create(RUNTIME_DIR)
    py = runtime_python()
    subprocess.run(
        [str(py), "-m", "pip", "install", "--disable-pip-version-check", "-r", str(REQUIREMENTS)],
        check=True,
    )
    subprocess.run([str(py), "-c", "from PIL import Image, ImageDraw, ImageFont"], check=True)
    state = {
        "status": "ready",
        "fingerprint": fingerprint(),
        "python": str(py),
        "platform": sys.platform,
        "python_version": f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}",
    }
    STATE_FILE.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return state


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--exec", action="store_true", dest="exec_requested")
    args, remainder = parser.parse_known_args()
    if remainder and not args.exec_requested:
        parser.error(f"unrecognized arguments: {' '.join(remainder)}")

    if args.check:
        state = current_state()
        state["ready"] = runtime_ready()
    else:
        state = ensure_runtime(args.force)

    if args.json or not args.exec_requested:
        print(json.dumps(state, ensure_ascii=False, indent=2))

    if args.exec_requested:
        command = list(remainder)
        if command and command[0] == "--":
            command = command[1:]
        if not command:
            raise SystemExit("--exec requires a Python script or module arguments")
        return subprocess.call([str(runtime_python()), *command], cwd=SKILL_ROOT)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
