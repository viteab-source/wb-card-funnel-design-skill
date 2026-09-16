#!/usr/bin/env python3
"""Block WB design stages until their recorded evidence is complete."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path


IMAGE_SUFFIXES = {".png", ".jpg", ".jpeg", ".webp"}
PROMPT_DIR = Path("media/hero-concepts/prompts")


def _load_json(path: Path, errors: list[str], label: str) -> dict | None:
    if not path.is_file() or path.stat().st_size == 0:
        errors.append(f"missing_or_empty:{label}")
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        errors.append(f"{label}:invalid_json")
        return None
    if not isinstance(data, dict):
        errors.append(f"{label}:expected_object")
        return None
    return data


def _nonempty(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_id(value: object) -> bool:
    return _nonempty(value) or (isinstance(value, int) and value > 0)


def _valid_status(data: dict, field: str, values: set[str], errors: list[str], label: str) -> None:
    if data.get(field) not in values:
        errors.append(f"{label}:{field}_not_accepted")


def validate(run_dir: Path, phase: str) -> dict:
    errors: list[str] = []

    intake = _load_json(run_dir / "intake.json", errors, "intake.json")
    if intake is not None:
        product = intake.get("product")
        if not isinstance(product, dict) or not _nonempty(product.get("name")) or not _nonempty(product.get("variant")):
            errors.append("intake.json:product_name_and_exact_variant_required")
        messages = intake.get("commercial_messages")
        if not isinstance(messages, list) or not any(
            isinstance(item, dict) and item.get("status") == "confirmed"
            and _nonempty(item.get("buyer_question"))
            and isinstance(item.get("fact_refs"), list) and bool(item["fact_refs"])
            for item in messages
        ):
            errors.append("intake.json:confirmed_commercial_message_required")

    passport = run_dir / "product-passport.md"
    if not passport.is_file() or passport.stat().st_size < 80:
        errors.append("missing_or_empty:product-passport.md")

    research = _load_json(run_dir / "research-evidence.json", errors, "research-evidence.json")
    if research is not None:
        _valid_status(research, "status", {"reviewed", "approved"}, errors, "research-evidence.json")
        context = research.get("search_context")
        if not isinstance(context, dict) or not all(_nonempty(context.get(key)) for key in ("query", "observed_at", "region", "device")):
            errors.append("research-evidence.json:search_context_incomplete")
        ids = research.get("competitor_ids")
        if not isinstance(ids, list) or len({str(item) for item in ids if _valid_id(item)}) < 2:
            errors.append("research-evidence.json:need_2_competitor_ids")

    shortlist = _load_json(run_dir / "competitor-shortlist.json", errors, "competitor-shortlist.json")
    approved_ids: set[str] = set()
    if shortlist is not None:
        _valid_status(shortlist, "status", {"user_approved"}, errors, "competitor-shortlist.json")
        approved = shortlist.get("approved_competitor_ids")
        if isinstance(approved, list):
            approved_ids = {str(item) for item in approved if _valid_id(item)}
        if len(approved_ids) < 2:
            errors.append("competitor-shortlist.json:need_2_user_approved_competitors")
        if not _nonempty(shortlist.get("user_evidence")):
            errors.append("competitor-shortlist.json:user_evidence_required")
        candidates = shortlist.get("candidates")
        candidate_index = {
            str(item.get("competitor_id")): item
            for item in candidates
            if isinstance(item, dict) and _valid_id(item.get("competitor_id"))
        } if isinstance(candidates, list) else {}
        for competitor_id in approved_ids:
            candidate = candidate_index.get(competitor_id)
            if not candidate or candidate.get("user_decision") != "approved":
                errors.append(f"competitor-shortlist.json:approved_candidate_missing_or_unmarked:{competitor_id}")
            elif not all(_nonempty(candidate.get(field)) for field in ("url", "cover_file", "match_reason")):
                errors.append(f"competitor-shortlist.json:approved_candidate_incomplete:{competitor_id}")

    funnel = _load_json(run_dir / "competitor-funnel-analysis.json", errors, "competitor-funnel-analysis.json")
    if funnel is not None:
        _valid_status(funnel, "status", {"reviewed", "approved"}, errors, "competitor-funnel-analysis.json")
        funnel_ids = {str(item) for item in funnel.get("approved_competitor_ids", []) if _valid_id(item)} if isinstance(funnel.get("approved_competitor_ids"), list) else set()
        if approved_ids and funnel_ids != approved_ids:
            errors.append("competitor-funnel-analysis.json:approved_ids_mismatch")
        competitors = funnel.get("competitors")
        competitor_index = {
            str(item.get("competitor_id")): item
            for item in competitors
            if isinstance(item, dict) and _valid_id(item.get("competitor_id"))
        } if isinstance(competitors, list) else {}
        required_slide_fields = ("position", "file", "visible_text", "role", "buyer_question", "scene_or_angle", "composition")
        for competitor_id in approved_ids:
            item = competitor_index.get(competitor_id)
            if not item or item.get("gallery_complete") is not True:
                errors.append(f"competitor-funnel-analysis.json:gallery_not_complete:{competitor_id}")
                continue
            slides = item.get("slides")
            if not isinstance(slides, list) or not slides or item.get("slide_count") != len(slides):
                errors.append(f"competitor-funnel-analysis.json:slide_count_invalid:{competitor_id}")
                continue
            for index, slide in enumerate(slides, 1):
                if not isinstance(slide, dict) or any(
                    not (_valid_id(slide.get(field)) if field == "position" else _nonempty(slide.get(field)) or (field == "visible_text" and isinstance(slide.get(field), list) and bool(slide.get(field))))
                    for field in required_slide_fields
                ):
                    errors.append(f"competitor-funnel-analysis.json:slide_incomplete:{competitor_id}:{index}")

    analysis = run_dir / "competitor-analysis.md"
    if not analysis.is_file() or len(analysis.read_text(encoding="utf-8", errors="ignore").strip()) < 120:
        errors.append("missing_or_empty:competitor-analysis.md")

    sources = list((run_dir / "competitors").glob("wb-*/source.json"))
    competitors_with_images = {
        source.parent for source in sources
        if any(path.suffix.lower() in IMAGE_SUFFIXES and path.stat().st_size > 0 for path in source.parent.iterdir() if path.is_file())
    }
    if len(competitors_with_images) < 2:
        errors.append("competitors:need_at_least_2_with_source_and_images")

    dna = _load_json(run_dir / "visual-dna.json", errors, "visual-dna.json")
    if dna is not None:
        _valid_status(dna, "status", {"reviewed", "approved"}, errors, "visual-dna.json")
        anchor = dna.get("product_anchor")
        direction = dna.get("art_direction")
        if not isinstance(anchor, dict) or not isinstance(anchor.get("asset_refs"), list) or not anchor["asset_refs"]:
            errors.append("visual-dna.json:product_anchor_missing")
        if not isinstance(anchor, dict) or not isinstance(anchor.get("immutable_traits"), list) or not anchor["immutable_traits"]:
            errors.append("visual-dna.json:immutable_traits_missing")
        if not isinstance(direction, dict) or not _nonempty(direction.get("id")):
            errors.append("visual-dna.json:art_direction_missing")
        elif direction.get("approval_status") not in {"approved", "autopilot_selected"}:
            errors.append("visual-dna.json:art_direction_not_accepted")
        qa = dna.get("qa")
        if not isinstance(qa, dict) or qa.get("art_direction_checked") is not True or qa.get("product_fidelity_checked") is not True:
            errors.append("visual-dna.json:prehero_qa_incomplete")

    prompts_path = run_dir / PROMPT_DIR
    prompts = sorted(path for path in prompts_path.glob("*.md") if path.is_file() and path.stat().st_size >= 80)
    hashes = {hashlib.sha256(path.read_bytes()).hexdigest() for path in prompts}
    if len(prompts) < 3:
        errors.append("hero_concepts:need_3_saved_prompts")
    elif len(hashes) < 3:
        errors.append("hero_concepts:prompts_must_be_distinct")

    prompt_ids = {path.stem for path in prompts}
    if phase in {"gallery", "delivery"}:
        contact_sheet = run_dir / "media/hero-concepts/contact-sheet.png"
        if not contact_sheet.is_file() or contact_sheet.stat().st_size == 0:
            errors.append("missing_or_empty:media/hero-concepts/contact-sheet.png")
        selection = _load_json(run_dir / "hero-selection.json", errors, "hero-selection.json")
        if selection is not None:
            _valid_status(selection, "status", {"user_approved", "autopilot_selected"}, errors, "hero-selection.json")
            candidate = selection.get("selected_candidate")
            if not _nonempty(candidate):
                errors.append("hero-selection.json:selected_candidate_missing")
            elif candidate not in prompt_ids:
                errors.append("hero-selection.json:selected_candidate_not_in_prompts")
            reviewed = selection.get("reviewed_candidates")
            if not isinstance(reviewed, list) or not prompt_ids.issubset(set(reviewed)):
                errors.append("hero-selection.json:reviewed_candidates_incomplete")

    if phase == "delivery":
        qa = _load_json(run_dir / "qa-checklist.json", errors, "qa-checklist.json")
        if qa is not None:
            _valid_status(qa, "status", {"pass"}, errors, "qa-checklist.json")
            required = {"product_fidelity", "claims_and_implied_claims", "rights", "mobile_readability", "wb_rules", "series_review"}
            checks = qa.get("checks")
            found = {item.get("id") for item in checks if isinstance(item, dict) and item.get("status") == "pass"} if isinstance(checks, list) else set()
            if not required.issubset(found):
                errors.append("qa-checklist.json:required_manual_checks_not_passed")

    return {"tool": "validate_design_gate", "phase": phase, "run_dir": str(run_dir), "status": "pass" if not errors else "fail", "errors": errors}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("run_dir", type=Path)
    parser.add_argument("--phase", choices=("hero", "gallery", "delivery"), required=True)
    args = parser.parse_args()
    report = validate(args.run_dir.resolve(), args.phase)
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
