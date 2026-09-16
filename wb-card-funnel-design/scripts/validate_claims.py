#!/usr/bin/env python3
"""Validate claims in a production manifest against canonical intake facts/assets.

The validator is intentionally stdlib-only. It checks provenance and internal
consistency; it does not prove that a source or a fact is true.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Sequence, Set, Tuple


SUPPORTED_SCHEMA_VERSION = 1
FACT_STATUSES = {"confirmed", "unknown", "conflicting", "excluded"}
CLAIM_KINDS = {"explicit", "implied"}
ITEM_STATUSES = {"draft", "reviewed", "approved"}
READINESS_STATUSES = {
    "draft-brief",
    "brief-ready",
    "concept-ready",
    "production-ready",
}
INTAKE_READY_STATUSES = {"ready", "not_needed"}
VARIANT_SCOPES = {"single_variant", "all_variants"}
CONFIRMED_RIGHTS_STATUSES = {"user_confirmed"}

BOUNDARY = (
    "Validator checks references, confirmed status, scope, variant and declared "
    "readiness. Source and rights labels are structural declarations: this tool "
    "does not establish truth, verify legal rights, inspect pixels or certify "
    "current Wildberries compliance, and it does not assign production-ready."
)

MANUAL_CHECKS_REQUIRED = [
    {
        "id": "semantic_evidence_sufficiency",
        "check": "Confirm that the cited facts are sufficient for the actual wording and scene meaning.",
    },
    {
        "id": "rights_reality",
        "check": "Confirm the real legal right to use every photo, font, graphic and other source asset.",
    },
    {
        "id": "pixel_product_truth",
        "check": "Compare final pixels with the exact product and variant; reject invented or altered details.",
    },
    {
        "id": "ocr_text_accuracy",
        "check": "Read all visible text and numbers in the final images and verify spelling and meaning.",
    },
    {
        "id": "current_wb_compliance",
        "check": "Check the final media against current official Wildberries and category rules.",
    },
]


def _issue(code: str, path: str, message: str) -> Dict[str, str]:
    return {"code": code, "path": path, "message": message}


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _normal_text(value: Any) -> Optional[str]:
    if not isinstance(value, str):
        return None
    value = value.strip()
    return value or None


def _normal_variant(value: Any) -> Optional[str]:
    value = _normal_text(value)
    if value is None:
        return None
    return value.casefold()


def _string_list(
    value: Any,
    path: str,
    errors: List[Dict[str, str]],
) -> List[str]:
    if not isinstance(value, list):
        errors.append(_issue("invalid_type", path, "Expected an array of IDs."))
        return []
    result: List[str] = []
    seen: Set[str] = set()
    for index, item in enumerate(value):
        item_path = f"{path}[{index}]"
        if not _is_nonempty_string(item):
            errors.append(_issue("invalid_id", item_path, "ID must be a non-empty string."))
            continue
        item = item.strip()
        if item in seen:
            errors.append(_issue("duplicate_ref", item_path, f"Duplicate reference: {item}"))
            continue
        seen.add(item)
        result.append(item)
    return result


def _index_records(
    value: Any,
    path: str,
    errors: List[Dict[str, str]],
) -> Tuple[List[Dict[str, Any]], Dict[str, Dict[str, Any]]]:
    if not isinstance(value, list):
        errors.append(_issue("invalid_type", path, "Expected an array."))
        return [], {}

    records: List[Dict[str, Any]] = []
    index: Dict[str, Dict[str, Any]] = {}
    for position, raw in enumerate(value):
        item_path = f"{path}[{position}]"
        if not isinstance(raw, dict):
            errors.append(_issue("invalid_type", item_path, "Expected an object."))
            continue
        records.append(raw)
        record_id = raw.get("id")
        if not _is_nonempty_string(record_id):
            errors.append(_issue("invalid_id", f"{item_path}.id", "ID must be a non-empty string."))
            continue
        record_id = record_id.strip()
        if record_id in index:
            errors.append(_issue("duplicate_id", f"{item_path}.id", f"Duplicate ID: {record_id}"))
            continue
        index[record_id] = raw
    return records, index


def _schema_version(
    document: Dict[str, Any],
    label: str,
    errors: List[Dict[str, str]],
) -> None:
    version = document.get("schema_version")
    if type(version) is not int:  # bool is not a valid integer version
        errors.append(_issue("invalid_schema_version", f"{label}.schema_version", "Expected integer 1."))
    elif version != SUPPORTED_SCHEMA_VERSION:
        errors.append(
            _issue(
                "unsupported_schema_version",
                f"{label}.schema_version",
                f"Supported version is {SUPPORTED_SCHEMA_VERSION}, got {version}.",
            )
        )


def _scope_set(
    value: Any,
    path: str,
    errors: List[Dict[str, str]],
    default: Optional[Iterable[str]] = None,
) -> Set[str]:
    if value is None:
        return set(default or ())
    values = [value] if isinstance(value, str) else value
    if not isinstance(values, list):
        errors.append(_issue("invalid_scope", path, "Scope must be a string or an array of strings."))
        return set()
    result: Set[str] = set()
    for index, item in enumerate(values):
        if not _is_nonempty_string(item):
            errors.append(_issue("invalid_scope", f"{path}[{index}]", "Scope must be non-empty."))
            continue
        result.add(item.strip().casefold())
    return result


def _variant_supports(
    source_variant: Any,
    source_variant_scope: str,
    target_variant: Any,
    target_variant_scope: str,
) -> bool:
    source = _normal_variant(source_variant)
    target = _normal_variant(target_variant)
    if source_variant_scope == "all_variants":
        return True
    if target_variant_scope == "all_variants":
        return False
    return source is not None and target is not None and source == target


def _effective_variant(item: Dict[str, Any], target_variant: Any) -> Any:
    item_variant = item.get("variant")
    return target_variant if _normal_variant(item_variant) is None else item_variant


def _variant_scope(
    item: Dict[str, Any],
    inherited: str,
    path: str,
    errors: List[Dict[str, str]],
) -> str:
    scope = item.get("variant_scope", inherited)
    if scope not in VARIANT_SCOPES:
        errors.append(
            _issue(
                "invalid_variant_scope",
                path,
                "Use single_variant or all_variants.",
            )
        )
        return "single_variant"
    if scope == "all_variants" and _normal_variant(item.get("variant")) is not None:
        errors.append(
            _issue(
                "ambiguous_variant_scope",
                path,
                "all_variants must not be combined with an exact variant.",
            )
        )
    return scope


def _readiness_minimum(readiness: str) -> str:
    if readiness == "production-ready":
        return "approved"
    if readiness in {"brief-ready", "concept-ready"}:
        return "reviewed"
    return "draft"


def _status_meets(actual: Any, minimum: str) -> bool:
    order = {"draft": 0, "reviewed": 1, "approved": 2}
    return isinstance(actual, str) and actual in order and order[actual] >= order[minimum]


def validate_documents(intake: Any, manifest: Any) -> Dict[str, Any]:
    """Return a deterministic JSON-serializable validation report."""

    errors: List[Dict[str, str]] = []
    warnings: List[Dict[str, str]] = []

    if not isinstance(intake, dict):
        errors.append(_issue("invalid_root", "intake", "Expected a JSON object."))
        intake = {}
    if not isinstance(manifest, dict):
        errors.append(_issue("invalid_root", "manifest", "Expected a JSON object."))
        manifest = {}

    _schema_version(intake, "intake", errors)
    _schema_version(manifest, "manifest", errors)

    intake_file = manifest.get("intake_file")
    if not _is_nonempty_string(intake_file):
        errors.append(
            _issue(
                "missing_intake_link",
                "manifest.intake_file",
                "Manifest must point to the canonical intake JSON.",
            )
        )

    intake_run_id = _normal_text(intake.get("run_id"))
    manifest_run_id = _normal_text(manifest.get("run_id"))
    if intake_run_id and manifest_run_id and intake_run_id != manifest_run_id:
        errors.append(_issue("run_id_mismatch", "manifest.run_id", "run_id does not match intake."))
    elif intake_run_id and not manifest_run_id:
        errors.append(_issue("missing_run_id", "manifest.run_id", "Copy run_id from intake."))
    elif manifest_run_id and not intake_run_id:
        errors.append(_issue("missing_run_id", "intake.run_id", "intake run_id is missing."))

    product = intake.get("product")
    if not isinstance(product, dict):
        errors.append(_issue("invalid_type", "intake.product", "Expected an object."))
        product = {}

    target = manifest.get("target")
    if not isinstance(target, dict):
        errors.append(_issue("invalid_type", "manifest.target", "Expected an object."))
        target = {}

    intake_variant = product.get("variant")
    target_variant = target.get("variant")
    target_variant_scope = target.get("variant_scope")
    if target_variant_scope not in VARIANT_SCOPES:
        errors.append(
            _issue(
                "invalid_variant_scope",
                "manifest.target.variant_scope",
                "Use single_variant or all_variants.",
            )
        )
        target_variant_scope = "single_variant"
    if target_variant_scope == "all_variants" and _normal_variant(target_variant) is not None:
        errors.append(
            _issue(
                "ambiguous_variant_scope",
                "manifest.target",
                "all_variants target must not also specify an exact variant.",
            )
        )
    normalized_intake_variant = _normal_variant(intake_variant)
    normalized_target_variant = _normal_variant(target_variant)
    if target_variant_scope == "single_variant" and normalized_intake_variant and normalized_target_variant:
        if normalized_intake_variant != normalized_target_variant:
            errors.append(
                _issue(
                    "target_variant_mismatch",
                    "manifest.target.variant",
                    "Target variant does not match intake.product.variant.",
                )
            )

    readiness = manifest.get("readiness_status")
    if readiness not in READINESS_STATUSES:
        errors.append(
            _issue(
                "invalid_readiness_status",
                "manifest.readiness_status",
                f"Expected one of: {', '.join(sorted(READINESS_STATUSES))}.",
            )
        )
        readiness = "draft-brief"
    minimum_status = _readiness_minimum(readiness)

    facts, fact_index = _index_records(intake.get("facts"), "intake.facts", errors)
    assets, asset_index = _index_records(intake.get("assets"), "intake.assets", errors)
    claims, claim_index = _index_records(manifest.get("claims"), "manifest.claims", errors)
    slides, _slide_index = _index_records(manifest.get("slides"), "manifest.slides", errors)

    for position, fact in enumerate(facts):
        base = f"intake.facts[{position}]"
        status = fact.get("status")
        if status not in FACT_STATUSES:
            errors.append(_issue("invalid_fact_status", f"{base}.status", "Use confirmed, unknown, conflicting or excluded."))
        if not _is_nonempty_string(fact.get("statement")):
            errors.append(_issue("missing_fact_statement", f"{base}.statement", "Fact statement is required."))
        if status == "confirmed" and not _is_nonempty_string(fact.get("source")):
            errors.append(_issue("missing_fact_source", f"{base}.source", "Confirmed fact requires a source."))
        _scope_set(fact.get("scope"), f"{base}.scope", errors, default={"product"})
        _variant_scope(fact, "single_variant", f"{base}.variant_scope", errors)

    for position, asset in enumerate(assets):
        base = f"intake.assets[{position}]"
        if not _is_nonempty_string(asset.get("file")):
            errors.append(_issue("missing_asset_file", f"{base}.file", "Asset file is required."))
        if not _is_nonempty_string(asset.get("role")):
            errors.append(_issue("missing_asset_role", f"{base}.role", "Asset role is required."))
        _variant_scope(asset, "single_variant", f"{base}.variant_scope", errors)

    used_fact_ids: Set[str] = set()
    for position, claim in enumerate(claims):
        base = f"manifest.claims[{position}]"
        kind = claim.get("kind")
        if kind not in CLAIM_KINDS:
            errors.append(_issue("invalid_claim_kind", f"{base}.kind", "Use explicit or implied."))
        if not _is_nonempty_string(claim.get("text")):
            errors.append(_issue("missing_claim_text", f"{base}.text", "Describe the visible or implied claim."))
        claim_scope = _scope_set(claim.get("scope"), f"{base}.scope", errors)
        if not claim_scope:
            errors.append(_issue("missing_claim_scope", f"{base}.scope", "Claim scope is required."))
        claim_status = claim.get("status")
        if claim_status not in ITEM_STATUSES:
            errors.append(_issue("invalid_item_status", f"{base}.status", "Use draft, reviewed or approved."))
        elif not _status_meets(claim_status, minimum_status):
            errors.append(
                _issue(
                    "claim_not_ready",
                    f"{base}.status",
                    f"{readiness} requires claim status {minimum_status} or higher.",
                )
            )

        effective_variant = _effective_variant(claim, target_variant)
        effective_variant_scope = _variant_scope(
            claim,
            target_variant_scope,
            f"{base}.variant_scope",
            errors,
        )
        claim_variant = _normal_variant(claim.get("variant"))
        if (
            target_variant_scope == "single_variant"
            and normalized_target_variant
            and claim_variant not in {None, normalized_target_variant}
        ):
            errors.append(_issue("claim_variant_mismatch", f"{base}.variant", "Claim variant differs from target."))

        fact_refs = _string_list(claim.get("fact_refs"), f"{base}.fact_refs", errors)
        if not fact_refs:
            errors.append(_issue("claim_without_evidence", f"{base}.fact_refs", "Every explicit or implied claim needs a fact reference."))
        for ref_index, fact_id in enumerate(fact_refs):
            ref_path = f"{base}.fact_refs[{ref_index}]"
            fact = fact_index.get(fact_id)
            if fact is None:
                errors.append(_issue("missing_fact_ref", ref_path, f"Unknown fact ID: {fact_id}"))
                continue
            used_fact_ids.add(fact_id)
            if fact.get("status") != "confirmed":
                errors.append(_issue("fact_not_confirmed", ref_path, f"Fact {fact_id} is not confirmed."))
            if not _is_nonempty_string(fact.get("source")):
                errors.append(_issue("missing_fact_source", ref_path, f"Fact {fact_id} has no source."))
            fact_scopes = _scope_set(fact.get("scope"), f"intake.fact[{fact_id}].scope", errors, default={"product"})
            if claim_scope and "all" not in fact_scopes and not claim_scope.issubset(fact_scopes):
                errors.append(
                    _issue(
                        "fact_scope_mismatch",
                        ref_path,
                        f"Fact {fact_id} does not cover claim scope {sorted(claim_scope)}.",
                    )
                )
            fact_variant_scope = fact.get("variant_scope", "single_variant")
            if fact_variant_scope not in VARIANT_SCOPES:
                fact_variant_scope = "single_variant"
            if not _variant_supports(
                fact.get("variant"),
                fact_variant_scope,
                effective_variant,
                effective_variant_scope,
            ):
                errors.append(_issue("fact_variant_mismatch", ref_path, f"Fact {fact_id} is for another variant."))

    referenced_claim_ids: Set[str] = set()
    used_asset_ids: Set[str] = set()
    for position, slide in enumerate(slides):
        base = f"manifest.slides[{position}]"
        if not _is_nonempty_string(slide.get("role")):
            errors.append(_issue("missing_slide_role", f"{base}.role", "Slide role is required."))
        slide_status = slide.get("status")
        if slide_status not in ITEM_STATUSES:
            errors.append(_issue("invalid_item_status", f"{base}.status", "Use draft, reviewed or approved."))
        elif not _status_meets(slide_status, minimum_status):
            errors.append(
                _issue(
                    "slide_not_ready",
                    f"{base}.status",
                    f"{readiness} requires slide status {minimum_status} or higher.",
                )
            )

        slide_variant = _effective_variant(slide, target_variant)
        slide_variant_scope = _variant_scope(
            slide,
            target_variant_scope,
            f"{base}.variant_scope",
            errors,
        )
        explicit_slide_variant = _normal_variant(slide.get("variant"))
        if (
            target_variant_scope == "single_variant"
            and normalized_target_variant
            and explicit_slide_variant not in {None, normalized_target_variant}
        ):
            errors.append(_issue("slide_variant_mismatch", f"{base}.variant", "Slide variant differs from target."))

        claim_refs = _string_list(slide.get("claim_refs"), f"{base}.claim_refs", errors)
        asset_refs = _string_list(slide.get("asset_refs"), f"{base}.asset_refs", errors)
        for ref_index, claim_id in enumerate(claim_refs):
            ref_path = f"{base}.claim_refs[{ref_index}]"
            claim = claim_index.get(claim_id)
            if claim is None:
                errors.append(_issue("missing_claim_ref", ref_path, f"Unknown claim ID: {claim_id}"))
                continue
            referenced_claim_ids.add(claim_id)
            claim_variant_scope = claim.get("variant_scope", target_variant_scope)
            if claim_variant_scope not in VARIANT_SCOPES:
                claim_variant_scope = "single_variant"
            if not _variant_supports(
                _effective_variant(claim, target_variant),
                claim_variant_scope,
                slide_variant,
                slide_variant_scope,
            ):
                errors.append(_issue("claim_slide_variant_mismatch", ref_path, f"Claim {claim_id} is for another variant."))
        for ref_index, asset_id in enumerate(asset_refs):
            ref_path = f"{base}.asset_refs[{ref_index}]"
            asset = asset_index.get(asset_id)
            if asset is None:
                errors.append(_issue("missing_asset_ref", ref_path, f"Unknown asset ID: {asset_id}"))
                continue
            used_asset_ids.add(asset_id)
            asset_variant_scope = asset.get("variant_scope", "single_variant")
            if asset_variant_scope not in VARIANT_SCOPES:
                asset_variant_scope = "single_variant"
            if not _variant_supports(
                asset.get("variant"),
                asset_variant_scope,
                slide_variant,
                slide_variant_scope,
            ):
                errors.append(_issue("asset_variant_mismatch", ref_path, f"Asset {asset_id} is for another variant."))

        if readiness == "production-ready" and not asset_refs:
            errors.append(_issue("slide_without_asset", f"{base}.asset_refs", "Production-ready slide requires at least one asset."))

    for claim_id in sorted(set(claim_index) - referenced_claim_ids):
        errors.append(_issue("orphan_claim", f"manifest.claims[{claim_id}]", "Claim is not referenced by any slide."))

    for fact_id in sorted(set(fact_index) - used_fact_ids):
        if fact_index[fact_id].get("status") != "excluded":
            warnings.append(_issue("unused_fact", f"intake.facts[{fact_id}]", "Fact is not used by any claim."))
    for asset_id in sorted(set(asset_index) - used_asset_ids):
        warnings.append(_issue("unused_asset", f"intake.assets[{asset_id}]", "Asset is not used by any slide."))

    if readiness == "production-ready":
        if target_variant_scope == "single_variant" and normalized_target_variant is None:
            errors.append(_issue("missing_target_variant", "manifest.target.variant", "Production-ready manifest requires an exact target variant."))
        if target_variant_scope == "single_variant" and normalized_intake_variant is None:
            errors.append(_issue("missing_intake_variant", "intake.product.variant", "Production-ready intake requires the exact target variant."))
        if not slides:
            errors.append(_issue("missing_slides", "manifest.slides", "Production-ready manifest requires at least one slide."))
        for asset_id in sorted(used_asset_ids):
            rights_status = _normal_text(asset_index[asset_id].get("rights_status"))
            normalized_rights = rights_status.casefold() if rights_status else None
            if normalized_rights not in CONFIRMED_RIGHTS_STATUSES:
                errors.append(
                    _issue(
                        "asset_rights_not_confirmed",
                        f"intake.assets[{asset_id}].rights_status",
                        "Referenced asset needs rights_status=user_confirmed; unknown and restricted are blocked.",
                    )
                )
        intake_readiness = intake.get("readiness")
        if not isinstance(intake_readiness, dict):
            errors.append(_issue("missing_intake_readiness", "intake.readiness", "Production-ready manifest requires intake readiness."))
        else:
            for gate in ("design", "generation"):
                gate_data = intake_readiness.get(gate)
                gate_status = gate_data.get("status") if isinstance(gate_data, dict) else None
                if gate_status not in INTAKE_READY_STATUSES:
                    errors.append(
                        _issue(
                            "intake_gate_not_ready",
                            f"intake.readiness.{gate}.status",
                            f"Production-ready requires {gate} status ready or not_needed.",
                        )
                    )

    report_status = "pass" if not errors else "fail"
    return {
        "tool": "validate_claims",
        "report_schema_version": 1,
        "status": report_status,
        "declared_readiness": readiness,
        "claims_gate_ready": readiness == "production-ready" and not errors,
        "summary": {
            "errors": len(errors),
            "warnings": len(warnings),
            "facts": len(facts),
            "assets": len(assets),
            "claims": len(claims),
            "slides": len(slides),
        },
        "errors": errors,
        "warnings": warnings,
        "manual_checks_required": MANUAL_CHECKS_REQUIRED,
        "boundary": BOUNDARY,
    }


def _runtime_error(code: str, message: str) -> Dict[str, Any]:
    return {
        "tool": "validate_claims",
        "report_schema_version": 1,
        "status": "error",
        "summary": {"errors": 1, "warnings": 0},
        "errors": [_issue(code, "$", message)],
        "warnings": [],
        "manual_checks_required": MANUAL_CHECKS_REQUIRED,
        "boundary": BOUNDARY,
    }


def _emit(report: Dict[str, Any], pretty: bool = False) -> None:
    if pretty:
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(json.dumps(report, ensure_ascii=False, separators=(",", ":"), sort_keys=True))


def _load_json(path: str) -> Any:
    with Path(path).open("r", encoding="utf-8") as handle:
        return json.load(handle)


def _check_intake_path(
    report: Dict[str, Any],
    manifest: Dict[str, Any],
    actual_intake_path: str,
    manifest_path: str,
) -> None:
    declared_value = manifest.get("intake_file")
    if not _is_nonempty_string(declared_value):
        return

    actual = Path(actual_intake_path).expanduser().resolve(strict=False)
    declared = Path(declared_value).expanduser()
    if not declared.is_absolute():
        manifest_directory = Path(manifest_path).expanduser().resolve(strict=False).parent
        declared = manifest_directory / declared
    declared = declared.resolve(strict=False)
    if declared == actual:
        return

    report["errors"].append(
        _issue(
            "intake_path_mismatch",
            "manifest.intake_file",
            "Declared intake_file does not match the INTAKE_JSON passed to the validator.",
        )
    )
    report["summary"]["errors"] = len(report["errors"])
    report["status"] = "fail"
    report["claims_gate_ready"] = False


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    pretty = False
    if "--pretty" in args:
        pretty = True
        args.remove("--pretty")
    if any(arg in {"-h", "--help"} for arg in args):
        print("Usage: validate_claims.py [--pretty] INTAKE_JSON PRODUCTION_MANIFEST_JSON")
        return 0
    if len(args) != 2 or any(arg.startswith("-") for arg in args):
        _emit(_runtime_error("usage_error", "Expected INTAKE_JSON and PRODUCTION_MANIFEST_JSON."), pretty)
        return 2

    try:
        intake = _load_json(args[0])
        manifest = _load_json(args[1])
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        _emit(_runtime_error("input_error", str(exc)), pretty)
        return 2

    report = validate_documents(intake, manifest)
    _check_intake_path(report, manifest, args[0], args[1])
    _emit(report, pretty)
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
