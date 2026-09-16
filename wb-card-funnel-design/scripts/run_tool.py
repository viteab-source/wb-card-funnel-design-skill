#!/usr/bin/env python3
"""Bootstrap the private runtime and run one trusted skill script in it."""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess

from bootstrap_runtime import SKILL_ROOT, ensure_runtime, runtime_python


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("tool", help="script filename inside scripts/")
    parser.add_argument("args", nargs=argparse.REMAINDER)
    ns = parser.parse_args()
    tool = (SKILL_ROOT / "scripts" / ns.tool).resolve()
    scripts_root = (SKILL_ROOT / "scripts").resolve()
    if tool.parent != scripts_root or not tool.is_file() or tool.suffix != ".py":
        raise SystemExit(f"unknown skill tool: {ns.tool}")
    ensure_runtime()
    return subprocess.call([str(runtime_python()), str(tool), *ns.args], cwd=SKILL_ROOT)


if __name__ == "__main__":
    raise SystemExit(main())
