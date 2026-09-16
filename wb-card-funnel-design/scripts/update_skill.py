#!/usr/bin/env python3
"""Check and apply a validated GitHub Release update with rollback."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from urllib.request import Request, urlopen
import uuid
import zipfile


SKILL_ROOT = Path(__file__).resolve().parent.parent
RELEASE_CONFIG = SKILL_ROOT / "release.json"


def version_tuple(value: str) -> tuple[int, int, int]:
    clean = value.strip().removeprefix("v").split("-", 1)[0]
    parts = clean.split(".")
    if len(parts) != 3 or any(not part.isdigit() for part in parts):
        raise ValueError(f"unsupported semantic version: {value}")
    return tuple(map(int, parts))  # type: ignore[return-value]


def load_config(root: Path = SKILL_ROOT) -> dict:
    return json.loads((root / "release.json").read_text(encoding="utf-8"))


def github_json(url: str) -> dict:
    request = Request(url, headers={"Accept": "application/vnd.github+json", "User-Agent": "wb-card-funnel-design-updater"})
    with urlopen(request, timeout=30) as response:
        return json.load(response)


def latest_release(config: dict) -> dict:
    return github_json(f"https://api.github.com/repos/{config['repository']}/releases/latest")


def validate_candidate(candidate: Path, expected_version: str) -> None:
    required = ["SKILL.md", "VERSION", "release.json", "scripts/bootstrap_runtime.py", "scripts/doctor.py"]
    missing = [name for name in required if not (candidate / name).is_file()]
    if missing:
        raise RuntimeError(f"release candidate is incomplete: {missing}")
    actual = (candidate / "VERSION").read_text(encoding="utf-8").strip()
    if version_tuple(actual) != version_tuple(expected_version):
        raise RuntimeError(f"release version mismatch: expected {expected_version}, got {actual}")
    config = load_config(candidate)
    if config.get("name") != "wb-card-funnel-design":
        raise RuntimeError("release contains a different skill")


def download_candidate(release: dict, temp_root: Path) -> Path:
    archive = temp_root / "release.zip"
    request = Request(release["zipball_url"], headers={"User-Agent": "wb-card-funnel-design-updater"})
    with urlopen(request, timeout=60) as response, archive.open("wb") as output:
        shutil.copyfileobj(response, output)
    extracted = temp_root / "extracted"
    with zipfile.ZipFile(archive) as bundle:
        bundle.extractall(extracted)
    matches = list(extracted.glob("*/wb-card-funnel-design"))
    if len(matches) != 1:
        raise RuntimeError("release archive does not contain one wb-card-funnel-design directory")
    return matches[0]


def preflight(candidate: Path) -> None:
    bootstrap = candidate / "scripts" / "bootstrap_runtime.py"
    subprocess.run([sys.executable, str(bootstrap), "--json"], check=True, cwd=candidate)
    subprocess.run(
        [sys.executable, str(bootstrap), "--exec", "--", "-m", "unittest", "discover", "-s", "scripts", "-p", "test_*.py"],
        check=True, cwd=candidate,
    )
    subprocess.run(
        [sys.executable, str(candidate / "scripts" / "run_tool.py"), "doctor.py", "--json", "--strict-local"],
        check=True, cwd=candidate,
    )


def apply_update(candidate: Path, version: str) -> Path:
    parent = SKILL_ROOT.parent
    incoming = parent / f".{SKILL_ROOT.name}-incoming-{uuid.uuid4().hex[:8]}"
    backup = parent / f"{SKILL_ROOT.name}.backup-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}"
    shutil.copytree(candidate, incoming)
    try:
        os.replace(SKILL_ROOT, backup)
        os.replace(incoming, SKILL_ROOT)
    except Exception:
        if SKILL_ROOT.exists() and not backup.exists():
            shutil.rmtree(SKILL_ROOT)
        if backup.exists() and not SKILL_ROOT.exists():
            os.replace(backup, SKILL_ROOT)
        if incoming.exists():
            shutil.rmtree(incoming)
        raise
    receipt = {
        "status": "updated",
        "version": version.removeprefix("v"),
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "backup": str(backup),
    }
    (SKILL_ROOT / "update-receipt.json").write_text(json.dumps(receipt, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return backup


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if args.check and args.apply:
        parser.error("choose --check or --apply")

    config = load_config()
    current = (SKILL_ROOT / "VERSION").read_text(encoding="utf-8").strip()
    release = latest_release(config)
    latest = release["tag_name"].removeprefix("v")
    available = version_tuple(latest) > version_tuple(current)
    result = {
        "current_version": current,
        "latest_version": latest,
        "update_available": available,
        "release_url": release["html_url"],
    }
    if not args.apply or not available:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0

    with tempfile.TemporaryDirectory(prefix="wb-card-funnel-update-") as tmp:
        candidate = download_candidate(release, Path(tmp))
        validate_candidate(candidate, latest)
        preflight(candidate)
        backup = apply_update(candidate, latest)
    result.update({"status": "updated", "backup": str(backup)})
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
