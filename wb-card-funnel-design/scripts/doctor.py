#!/usr/bin/env python3
"""Report local capabilities without exposing secrets."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import platform
import shutil
import sys


SKILL_ROOT = Path(__file__).resolve().parent.parent


def report() -> dict:
    fonts = SKILL_ROOT / "assets" / "fonts" / "inter"
    checks = {
        "python_3_10_plus": sys.version_info >= (3, 10),
        "pillow": importlib.util.find_spec("PIL") is not None,
        "inter_regular_bundled": (fonts / "Inter-Variable.ttf").is_file(),
        "inter_italic_bundled": (fonts / "Inter-Italic-Variable.ttf").is_file(),
        "font_license_bundled": (fonts / "OFL.txt").is_file(),
    }
    optional = {
        "tesseract": shutil.which("tesseract") is not None,
        "mpstats_token_present": bool(os.environ.get("MPSTATS_TOKEN")),
    }
    return {
        "status": "ready" if all(checks.values()) else "blocked",
        "platform": platform.system(),
        "platform_release": platform.release(),
        "machine": platform.machine(),
        "python": platform.python_version(),
        "required_local_checks": checks,
        "optional_local_checks": optional,
        "codex_capabilities_to_check_in_session": {
            "image_generation": "required for new photorealistic scenes; cannot be installed by this repository",
            "web_or_browser": "required for live competitor research; screenshots can be used as fallback",
        },
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--strict-local", action="store_true")
    args = parser.parse_args()
    result = report()
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if args.strict_local and result["status"] != "ready":
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
