#!/usr/bin/env python3
"""Read visible text in final media and compare it with a per-slide text plan.

This is an OCR gate, not a spelling oracle. Low-confidence or unmatched text
requires human review. Vision is used on macOS when available; Tesseract is a
fallback when Russian training data is installed.
"""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path
from typing import Any


def normalise(value: str) -> str:
    value = unicodedata.normalize("NFKC", value).casefold().replace("ё", "е")
    value = value.replace("—", "-").replace("–", "-").replace("−", "-")
    value = re.sub(r"[^\w\s+\-]", "", value, flags=re.UNICODE)
    return " ".join(value.split())


def distance(a: str, b: str) -> int:
    rows = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        new = [i]
        for j, cb in enumerate(b, 1):
            new.append(min(new[j - 1] + 1, rows[j] + 1, rows[j - 1] + (ca != cb)))
        rows = new
    return rows[-1]


def similarity(a: str, b: str) -> float:
    a, b = normalise(a), normalise(b)
    if not a and not b:
        return 1.0
    return 1.0 - distance(a, b) / max(len(a), len(b), 1)


def safe_output(root: Path, output: str) -> Path:
    if not isinstance(output, str) or not output or Path(output).is_absolute():
        raise ValueError("output must be a non-empty relative path")
    path = (root / output).resolve()
    if root.resolve() not in path.parents:
        raise ValueError("output escapes the run directory")
    if path.suffix.lower() not in {".png", ".jpg", ".jpeg", ".webp"}:
        raise ValueError("output is not a supported image")
    return path


def vision_lines(path: Path) -> list[dict[str, Any]] | None:
    try:
        import Foundation  # type: ignore
        import Vision  # type: ignore
    except ImportError:
        return None
    request = Vision.VNRecognizeTextRequest.alloc().init()
    request.setRecognitionLevel_(Vision.VNRequestTextRecognitionLevelAccurate)
    languages, error = request.supportedRecognitionLanguagesAndReturnError_(None)
    if error or not languages or "ru-RU" not in languages:
        return None
    request.setRecognitionLanguages_(["ru-RU", "en-US"])
    request.setUsesLanguageCorrection_(True)
    url = Foundation.NSURL.fileURLWithPath_(str(path))
    handler = Vision.VNImageRequestHandler.alloc().initWithURL_options_(url, {})
    ok, error = handler.performRequests_error_([request], None)
    if not ok or error:
        raise RuntimeError(f"Vision OCR failed: {error}")
    from PIL import Image  # type: ignore
    with Image.open(path) as source:
        width, height = source.size
    lines = []
    for result in request.results() or []:
        candidates = result.topCandidates_(1)
        if candidates:
            rect = result.boundingBox()
            lines.append({"text": str(candidates[0].string()), "confidence": float(candidates[0].confidence()),
                          "box": [round(rect.origin.x * width),
                                  round((1 - rect.origin.y - rect.size.height) * height),
                                  round((rect.origin.x + rect.size.width) * width),
                                  round((1 - rect.origin.y) * height)]})
    return lines


def tesseract_languages(tessdata_dir: str | None) -> set[str]:
    if not shutil.which("tesseract"):
        return set()
    cmd = ["tesseract", "--list-langs"]
    if tessdata_dir:
        cmd += ["--tessdata-dir", tessdata_dir]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
    return set(result.stdout.splitlines() + result.stderr.splitlines()) & {"rus", "eng"}


def tesseract_crop(path: Path, box: list[int], tessdata_dir: str | None) -> str | None:
    if "rus" not in tesseract_languages(tessdata_dir):
        return None
    try:
        from PIL import Image  # type: ignore
    except ImportError:
        return None
    with Image.open(path) as source:
        with tempfile.NamedTemporaryFile(suffix=".png") as temp:
            source.crop(tuple(box)).save(temp.name)
            cmd = ["tesseract", temp.name, "stdout", "-l", "rus+eng", "--oem", "1", "--psm", "7"]
            if tessdata_dir:
                cmd += ["--tessdata-dir", tessdata_dir]
            result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode:
        return None
    return result.stdout.strip()


def inside(line_box: list[int], target: list[int]) -> bool:
    x0, y0, x1, y1 = line_box
    return target[0] <= (x0 + x1) / 2 <= target[2] and target[1] <= (y0 + y1) / 2 <= target[3]


def evaluate(expected: str, vision: list[dict[str, Any]] | None, fallback: str | None) -> dict[str, Any]:
    matches = []
    for line in vision or []:
        matches.append((similarity(expected, line["text"]), line["confidence"], "vision", line["text"]))
    if fallback:
        # Tesseract's plain-text CLI output has no calibrated confidence.
        matches.append((similarity(expected, fallback), 0.0, "tesseract", fallback))
    if not matches:
        return {"status": "blocked", "expected": expected, "observed": None, "reason": "ocr_unavailable"}
    score, confidence, backend, observed = max(matches, key=lambda row: (row[0], row[1]))
    status = "pass" if backend == "vision" and score >= 0.95 and confidence >= 0.5 else "review"
    return {"status": status, "expected": expected, "observed": observed,
            "similarity": round(score, 3), "confidence": round(confidence, 3), "backend": backend}


def validate(run_dir: Path, plan_path: Path, tessdata_dir: str | None = None) -> dict[str, Any]:
    root = run_dir.resolve()
    plan = json.loads(plan_path.read_text(encoding="utf-8"))
    media = json.loads((root / "media-manifest.json").read_text(encoding="utf-8"))
    if plan.get("run_id") != media.get("run_id"):
        raise ValueError("run_id differs between OCR plan and media manifest")
    outputs = {slide["id"]: slide["output"] for slide in media["slides"]}
    reports = []
    seen = set()
    for slide in plan.get("slides", []):
        slide_id = slide.get("id")
        if slide_id in seen or slide_id not in outputs:
            raise ValueError(f"unknown or duplicate slide: {slide_id}")
        seen.add(slide_id)
        path = safe_output(root, outputs[slide_id])
        if not path.is_file():
            raise ValueError(f"missing final image: {path}")
        lines = vision_lines(path)
        checks = []
        if not slide.get("text"):
            raise ValueError(f"OCR plan has no text in {slide_id}")
        from PIL import Image  # type: ignore
        with Image.open(path) as source:
            width, height = source.size
        for item in slide.get("text", []):
            expected, box = item.get("expected"), item.get("box")
            if not isinstance(expected, str) or not expected.strip():
                raise ValueError(f"empty expected text in {slide_id}")
            if not isinstance(box, list) or len(box) != 4 or not all(isinstance(x, int) for x in box):
                raise ValueError(f"invalid OCR crop in {slide_id}")
            if not (0 <= box[0] < box[2] and 0 <= box[1] < box[3]):
                raise ValueError(f"invalid OCR crop bounds in {slide_id}")
            if box[2] > width or box[3] > height:
                raise ValueError(f"OCR crop exceeds image in {slide_id}")
            local_lines = [row for row in lines or [] if inside(row["box"], box)]
            best = max((similarity(expected, row["text"]) for row in local_lines), default=0.0)
            fallback = tesseract_crop(path, box, tessdata_dir) if best < 0.95 else None
            check = evaluate(expected, local_lines, fallback)
            check["box"] = box
            checks.append(check)
        reports.append({"id": slide_id, "output": outputs[slide_id], "checks": checks,
                        "vision_lines": lines, "backend": "vision" if lines is not None else "tesseract"})
    if seen != set(outputs):
        raise ValueError("OCR plan must cover every final slide")
    statuses = [check["status"] for slide in reports for check in slide["checks"]]
    status = "blocked" if "blocked" in statuses else "review" if "review" in statuses else "pass"
    return {"validator": "validate_ocr", "run_id": plan["run_id"], "status": status,
            "summary": {"slides": len(reports), "text_items": len(statuses),
                        "pass": statuses.count("pass"), "review": statuses.count("review"),
                        "blocked": statuses.count("blocked")},
            "slides": reports,
            "boundary": "OCR is a detector. Low-confidence mismatches need pixel review; this report cannot assign production-ready or prove product truth."}


def main(argv: list[str]) -> int:
    if len(argv) < 2 or len(argv) > 4:
        print("usage: validate_ocr.py RUN_DIR [OCR_PLAN.json] [TESSDATA_DIR]", file=sys.stderr)
        return 2
    root = Path(argv[1])
    plan = Path(argv[2]) if len(argv) >= 3 else root / "ocr-text-plan.json"
    tessdata = argv[3] if len(argv) >= 4 else None
    try:
        report = validate(root, plan, tessdata)
    except (OSError, ValueError, KeyError, TypeError, ImportError, json.JSONDecodeError, RuntimeError) as error:
        print(json.dumps({"validator": "validate_ocr", "status": "error", "error": str(error)}, ensure_ascii=False, indent=2))
        return 2
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
