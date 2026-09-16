#!/usr/bin/env python3
"""Portable technical preflight for a linked package of final WB gallery images.

The manifest supplies the dated working profile. This validator does not claim
that the profile is a current WB rule and does not perform semantic visual QA.
"""

from __future__ import annotations

import hashlib
import json
import struct
import sys
import zlib
from datetime import date
from pathlib import Path
from typing import Any


REPORT_VERSION = 1
SUPPORTED_FORMATS = {"png", "jpeg", "webp"}
NOT_CHECKED = [
    "full_pixel_decode_and_encoding_integrity",
    "ocr_and_text_accuracy",
    "color_profile_and_visual_rendering",
    "asset_rights_and_licenses",
    "product_and_variant_visual_fidelity",
    "current_wb_policy_compliance",
]
BOUNDARY = (
    "This is a technical media gate only. A pass does not assign production-ready; "
    "claims, pixels, product truth, text, rights and current WB rules still require "
    "their declared checks."
)


class ImageHeaderError(ValueError):
    """Raised when an image signature or structural header is invalid."""


def _issue(
    issues: list[dict[str, str]],
    code: str,
    path: str,
    message: str,
    *,
    severity: str = "error",
) -> None:
    issues.append({"severity": severity, "code": code, "path": path, "message": message})


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _non_empty_string(
    obj: dict[str, Any], field: str, path: str, issues: list[dict[str, str]]
) -> str | None:
    value = obj.get(field)
    if not isinstance(value, str) or not value.strip():
        _issue(issues, "required_field", f"{path}.{field}", "must be a non-empty string")
        return None
    return value.strip()


def _positive_int_or_none(
    profile: dict[str, Any], field: str, issues: list[dict[str, str]]
) -> int | None:
    if field not in profile:
        _issue(issues, "required_field", f"profile.{field}", "field is required; use null for no limit")
        return None
    value = profile.get(field)
    if value is None:
        return None
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        _issue(issues, "invalid_profile", f"profile.{field}", "must be a positive integer or null")
        return None
    return value


def _positive_number(
    profile: dict[str, Any], field: str, issues: list[dict[str, str]]
) -> float | None:
    value = profile.get(field)
    if not _is_number(value) or value <= 0:
        _issue(issues, "invalid_profile", f"profile.{field}", "must be a positive number")
        return None
    return float(value)


def _png_dimensions(data: bytes) -> tuple[int, int]:
    signature = b"\x89PNG\r\n\x1a\n"
    if not data.startswith(signature):
        raise ImageHeaderError("invalid PNG signature")

    offset = len(signature)
    first = True
    width = height = 0
    saw_idat = False
    saw_iend = False
    while offset < len(data):
        if offset + 12 > len(data):
            raise ImageHeaderError("truncated PNG chunk")
        length = struct.unpack(">I", data[offset : offset + 4])[0]
        chunk_type = data[offset + 4 : offset + 8]
        payload_start = offset + 8
        payload_end = payload_start + length
        crc_end = payload_end + 4
        if crc_end > len(data):
            raise ImageHeaderError("truncated PNG chunk payload")
        payload = data[payload_start:payload_end]
        stored_crc = struct.unpack(">I", data[payload_end:crc_end])[0]
        actual_crc = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
        if stored_crc != actual_crc:
            raise ImageHeaderError("PNG chunk checksum mismatch")

        if first:
            if chunk_type != b"IHDR" or length != 13:
                raise ImageHeaderError("PNG must start with a 13-byte IHDR chunk")
            width, height = struct.unpack(">II", payload[:8])
            if width <= 0 or height <= 0:
                raise ImageHeaderError("PNG dimensions must be positive")
            first = False
        elif chunk_type == b"IHDR":
            raise ImageHeaderError("duplicate PNG IHDR chunk")

        if chunk_type == b"IDAT":
            saw_idat = True
        if chunk_type == b"IEND":
            if length != 0:
                raise ImageHeaderError("PNG IEND chunk must be empty")
            saw_iend = True
            offset = crc_end
            break
        offset = crc_end

    if not saw_idat or not saw_iend:
        raise ImageHeaderError("PNG requires IDAT and IEND chunks")
    if offset != len(data):
        raise ImageHeaderError("unexpected data after PNG IEND")
    return width, height


JPEG_SOF_MARKERS = {
    0xC0,
    0xC1,
    0xC2,
    0xC3,
    0xC5,
    0xC6,
    0xC7,
    0xC9,
    0xCA,
    0xCB,
    0xCD,
    0xCE,
    0xCF,
}


def _exif_orientation(payload: bytes) -> int | None:
    if not payload.startswith(b"Exif\x00\x00"):
        return None
    tiff = payload[6:]
    if len(tiff) < 8:
        raise ImageHeaderError("truncated JPEG EXIF TIFF header")
    if tiff[:2] == b"II":
        endian = "<"
    elif tiff[:2] == b"MM":
        endian = ">"
    else:
        raise ImageHeaderError("invalid JPEG EXIF byte order")
    if struct.unpack(endian + "H", tiff[2:4])[0] != 42:
        raise ImageHeaderError("invalid JPEG EXIF TIFF marker")
    ifd_offset = struct.unpack(endian + "I", tiff[4:8])[0]
    if ifd_offset + 2 > len(tiff):
        raise ImageHeaderError("invalid JPEG EXIF IFD offset")
    entry_count = struct.unpack(endian + "H", tiff[ifd_offset : ifd_offset + 2])[0]
    entries_start = ifd_offset + 2
    entries_end = entries_start + entry_count * 12
    if entries_end > len(tiff):
        raise ImageHeaderError("truncated JPEG EXIF IFD")
    for index in range(entry_count):
        entry = tiff[entries_start + index * 12 : entries_start + (index + 1) * 12]
        tag, value_type = struct.unpack(endian + "HH", entry[:4])
        count = struct.unpack(endian + "I", entry[4:8])[0]
        if tag == 0x0112:
            if value_type != 3 or count != 1:
                raise ImageHeaderError("invalid JPEG EXIF orientation field")
            orientation = struct.unpack(endian + "H", entry[8:10])[0]
            if orientation not in range(1, 9):
                raise ImageHeaderError("JPEG EXIF orientation must be between 1 and 8")
            return orientation
    return None


def _jpeg_dimensions(data: bytes) -> tuple[int, int, int]:
    if len(data) < 4 or not data.startswith(b"\xff\xd8"):
        raise ImageHeaderError("invalid JPEG signature")
    if not data.endswith(b"\xff\xd9"):
        raise ImageHeaderError("JPEG end marker is missing")

    offset = 2
    dimensions: tuple[int, int] | None = None
    orientation = 1
    saw_scan = False
    scan_payload_start: int | None = None
    while offset < len(data) - 2:
        if data[offset] != 0xFF:
            raise ImageHeaderError("invalid JPEG marker sequence")
        while offset < len(data) and data[offset] == 0xFF:
            offset += 1
        if offset >= len(data):
            break
        marker = data[offset]
        offset += 1
        if marker == 0xD9:
            break
        if marker == 0xDA:
            if offset + 2 > len(data):
                raise ImageHeaderError("truncated JPEG scan header")
            scan_header_length = struct.unpack(">H", data[offset : offset + 2])[0]
            if scan_header_length < 2 or offset + scan_header_length > len(data) - 2:
                raise ImageHeaderError("invalid JPEG scan header length")
            saw_scan = True
            scan_payload_start = offset + scan_header_length
            break
        if marker == 0x01 or 0xD0 <= marker <= 0xD7:
            continue
        if offset + 2 > len(data):
            raise ImageHeaderError("truncated JPEG segment length")
        segment_length = struct.unpack(">H", data[offset : offset + 2])[0]
        if segment_length < 2 or offset + segment_length > len(data):
            raise ImageHeaderError("invalid JPEG segment length")
        if marker in JPEG_SOF_MARKERS:
            if segment_length < 8:
                raise ImageHeaderError("truncated JPEG frame header")
            height = struct.unpack(">H", data[offset + 3 : offset + 5])[0]
            width = struct.unpack(">H", data[offset + 5 : offset + 7])[0]
            if width <= 0 or height <= 0:
                raise ImageHeaderError("JPEG dimensions must be positive")
            dimensions = (width, height)
        elif marker == 0xE1:
            found_orientation = _exif_orientation(data[offset + 2 : offset + segment_length])
            if found_orientation is not None:
                orientation = found_orientation
        offset += segment_length

    if dimensions is None:
        raise ImageHeaderError("JPEG frame dimensions were not found")
    if not saw_scan or scan_payload_start is None or scan_payload_start >= len(data) - 2:
        raise ImageHeaderError("JPEG scan payload is missing")
    return dimensions[0], dimensions[1], orientation


def _little_u24(data: bytes) -> int:
    return data[0] | (data[1] << 8) | (data[2] << 16)


def _webp_dimensions(data: bytes) -> tuple[int, int]:
    if len(data) < 20 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        raise ImageHeaderError("invalid WebP signature")
    declared_size = struct.unpack("<I", data[4:8])[0] + 8
    if declared_size != len(data):
        raise ImageHeaderError("WebP RIFF size does not match file size")

    offset = 12
    dimensions: tuple[int, int] | None = None
    while offset < len(data):
        if offset + 8 > len(data):
            raise ImageHeaderError("truncated WebP chunk header")
        chunk_type = data[offset : offset + 4]
        chunk_size = struct.unpack("<I", data[offset + 4 : offset + 8])[0]
        payload_start = offset + 8
        payload_end = payload_start + chunk_size
        padded_end = payload_end + (chunk_size % 2)
        if padded_end > len(data):
            raise ImageHeaderError("truncated WebP chunk payload")
        payload = data[payload_start:payload_end]

        if chunk_type == b"VP8X":
            if len(payload) != 10:
                raise ImageHeaderError("invalid WebP VP8X header")
            dimensions = (_little_u24(payload[4:7]) + 1, _little_u24(payload[7:10]) + 1)
        elif chunk_type == b"VP8L":
            if len(payload) < 5 or payload[0] != 0x2F:
                raise ImageHeaderError("invalid WebP VP8L header")
            packed = int.from_bytes(payload[1:5], "little")
            dimensions = ((packed & 0x3FFF) + 1, ((packed >> 14) & 0x3FFF) + 1)
        elif chunk_type == b"VP8 ":
            if len(payload) < 10 or payload[3:6] != b"\x9d\x01\x2a":
                raise ImageHeaderError("invalid WebP VP8 frame header")
            width = struct.unpack("<H", payload[6:8])[0] & 0x3FFF
            height = struct.unpack("<H", payload[8:10])[0] & 0x3FFF
            dimensions = (width, height)
        offset = padded_end

    if offset != len(data):
        raise ImageHeaderError("invalid WebP chunk alignment")
    if dimensions is None or dimensions[0] <= 0 or dimensions[1] <= 0:
        raise ImageHeaderError("WebP frame dimensions were not found")
    return dimensions


def inspect_image_details(data: bytes) -> dict[str, Any]:
    """Return detected format, stored dimensions and display dimensions."""
    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        width, height = _png_dimensions(data)
        return {
            "format": "png",
            "encoded_width_px": width,
            "encoded_height_px": height,
            "width_px": width,
            "height_px": height,
            "exif_orientation": None,
        }
    if data.startswith(b"\xff\xd8"):
        width, height, orientation = _jpeg_dimensions(data)
        effective_width, effective_height = (height, width) if orientation in {5, 6, 7, 8} else (width, height)
        return {
            "format": "jpeg",
            "encoded_width_px": width,
            "encoded_height_px": height,
            "width_px": effective_width,
            "height_px": effective_height,
            "exif_orientation": orientation,
        }
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        width, height = _webp_dimensions(data)
        return {
            "format": "webp",
            "encoded_width_px": width,
            "encoded_height_px": height,
            "width_px": width,
            "height_px": height,
            "exif_orientation": None,
        }
    raise ImageHeaderError("unsupported or unrecognized image signature")


def inspect_image(data: bytes) -> tuple[str, int, int]:
    """Return detected format and effective display dimensions."""
    details = inspect_image_details(data)
    return details["format"], details["width_px"], details["height_px"]


def _extension_format(path: Path) -> str | None:
    extension = path.suffix.lower().lstrip(".")
    if extension == "jpg":
        return "jpeg"
    if extension in SUPPORTED_FORMATS:
        return extension
    return None


def _linked_manifest_path(manifest: Any, base_dir: Path) -> Path | None:
    """Resolve a safe relative production-manifest link, if structurally usable."""
    if not isinstance(manifest, dict):
        return None
    value = manifest.get("production_manifest_file")
    if not isinstance(value, str) or not value.strip():
        return None
    relative_path = Path(value.strip())
    if relative_path.is_absolute():
        return None
    resolved = (base_dir / relative_path).resolve(strict=False)
    try:
        resolved.relative_to(base_dir.resolve(strict=False))
    except ValueError:
        return None
    return resolved


def validate_manifest(
    manifest: Any,
    *,
    base_dir: Path,
    manifest_path: str,
    production_manifest: Any = None,
) -> dict[str, Any]:
    """Validate manifest structure and all referenced final images."""
    issues: list[dict[str, str]] = []
    files: list[dict[str, Any]] = []
    duplicate_groups: list[dict[str, Any]] = []

    if not isinstance(manifest, dict):
        _issue(issues, "manifest_type", "$", "manifest must be a JSON object")
        return _report(manifest_path, None, None, None, files, duplicate_groups, issues, 0)

    if type(manifest.get("schema_version")) is not int or manifest.get("schema_version") != 1:
        _issue(issues, "schema_version", "schema_version", "must equal 1")
    if "product" in manifest:
        _issue(
            issues,
            "canonical_field_not_allowed",
            "product",
            "product truth belongs in intake and production manifests, not the media manifest",
        )
    run_id = _non_empty_string(manifest, "run_id", "$", issues)
    production_manifest_file = _non_empty_string(
        manifest, "production_manifest_file", "$", issues
    )
    linked_path = _linked_manifest_path(manifest, base_dir)
    if production_manifest_file is not None and linked_path is None:
        _issue(
            issues,
            "invalid_production_manifest_path",
            "production_manifest_file",
            "must be a relative path that stays inside the media manifest directory",
        )
    status = _non_empty_string(manifest, "status", "$", issues)
    if status is not None and status != "ready":
        _issue(issues, "manifest_not_ready", "status", "must be ready for technical preflight")

    profile = manifest.get("profile")
    allowed_formats: set[str] = set()
    limits: dict[str, Any] = {}
    duplicate_policy = "fail"
    allow_external_paths = False
    profile_summary: dict[str, Any] | None = None
    if not isinstance(profile, dict):
        _issue(issues, "required_field", "profile", "must be an object")
    else:
        profile_id = _non_empty_string(profile, "id", "profile", issues)
        basis = _non_empty_string(profile, "basis", "profile", issues)
        checked_at = _non_empty_string(profile, "checked_at", "profile", issues)
        if checked_at is not None:
            try:
                date.fromisoformat(checked_at)
            except ValueError:
                _issue(issues, "invalid_profile", "profile.checked_at", "must be YYYY-MM-DD")

        raw_formats = profile.get("allowed_formats")
        if not isinstance(raw_formats, list) or not raw_formats:
            _issue(issues, "invalid_profile", "profile.allowed_formats", "must be a non-empty array")
        else:
            for index, raw_format in enumerate(raw_formats):
                if not isinstance(raw_format, str):
                    _issue(
                        issues,
                        "invalid_profile",
                        f"profile.allowed_formats[{index}]",
                        "must be png, jpeg, jpg or webp",
                    )
                    continue
                normalized = "jpeg" if raw_format.lower() == "jpg" else raw_format.lower()
                if normalized not in SUPPORTED_FORMATS:
                    _issue(
                        issues,
                        "invalid_profile",
                        f"profile.allowed_formats[{index}]",
                        "validator supports only png, jpeg and webp",
                    )
                elif normalized in allowed_formats:
                    _issue(
                        issues,
                        "invalid_profile",
                        "profile.allowed_formats",
                        f"duplicate format after normalization: {normalized}",
                    )
                else:
                    allowed_formats.add(normalized)

        for field in ("min_width_px", "max_width_px", "min_height_px", "max_height_px", "max_bytes"):
            limits[field] = _positive_int_or_none(profile, field, issues)
        for field in ("min_aspect_ratio", "max_aspect_ratio"):
            limits[field] = _positive_number(profile, field, issues)

        duplicate_policy_value = profile.get("duplicate_content_policy")
        if duplicate_policy_value not in {"fail", "warn", "allow"}:
            _issue(
                issues,
                "invalid_profile",
                "profile.duplicate_content_policy",
                "must be fail, warn or allow",
            )
        else:
            duplicate_policy = duplicate_policy_value
        allow_external_value = profile.get("allow_external_paths")
        if not isinstance(allow_external_value, bool):
            _issue(
                issues,
                "invalid_profile",
                "profile.allow_external_paths",
                "must be true or false",
            )
        else:
            allow_external_paths = allow_external_value

        if (
            limits.get("min_width_px") is not None
            and limits.get("max_width_px") is not None
            and limits["min_width_px"] > limits["max_width_px"]
        ):
            _issue(issues, "invalid_profile", "profile", "minimum width exceeds maximum width")
        if (
            limits.get("min_height_px") is not None
            and limits.get("max_height_px") is not None
            and limits["min_height_px"] > limits["max_height_px"]
        ):
            _issue(issues, "invalid_profile", "profile", "minimum height exceeds maximum height")
        if (
            limits.get("min_aspect_ratio") is not None
            and limits.get("max_aspect_ratio") is not None
            and limits["min_aspect_ratio"] > limits["max_aspect_ratio"]
        ):
            _issue(issues, "invalid_profile", "profile", "minimum aspect ratio exceeds maximum")
        profile_summary = {
            "id": profile_id,
            "basis": basis,
            "checked_at": checked_at,
            "allowed_formats": sorted(allowed_formats),
            **limits,
            "duplicate_content_policy": duplicate_policy,
            "allow_external_paths": allow_external_paths,
        }

    slides = manifest.get("slides")
    if not isinstance(slides, list) or not slides:
        _issue(issues, "required_field", "slides", "must be a non-empty array")
        slides = []

    seen_ids: dict[str, int] = {}
    seen_orders: dict[int, int] = {}
    seen_outputs: dict[str, int] = {}
    digest_to_slides: dict[str, list[str]] = {}

    for index, slide in enumerate(slides):
        slide_path = f"slides[{index}]"
        if not isinstance(slide, dict):
            _issue(issues, "slide_type", slide_path, "must be an object")
            continue
        slide_id = _non_empty_string(slide, "id", slide_path, issues)
        if "role" in slide:
            _issue(
                issues,
                "canonical_field_not_allowed",
                f"{slide_path}.role",
                "slide role belongs in the linked production manifest",
            )
        slide_status = _non_empty_string(slide, "status", slide_path, issues)
        output = _non_empty_string(slide, "output", slide_path, issues)
        order = slide.get("order")
        if not isinstance(order, int) or isinstance(order, bool) or order <= 0:
            _issue(issues, "invalid_order", f"{slide_path}.order", "must be a positive integer")
            order = None

        if slide_id is not None:
            if slide_id in seen_ids:
                _issue(
                    issues,
                    "duplicate_slide_id",
                    f"{slide_path}.id",
                    f"duplicates slides[{seen_ids[slide_id]}].id",
                )
            else:
                seen_ids[slide_id] = index
        if order is not None:
            if order in seen_orders:
                _issue(
                    issues,
                    "duplicate_order",
                    f"{slide_path}.order",
                    f"duplicates slides[{seen_orders[order]}].order",
                )
            else:
                seen_orders[order] = index
        if slide_status is not None and slide_status != "ready":
            _issue(issues, "slide_not_ready", f"{slide_path}.status", "must be ready")
        if output is None:
            continue

        file_error_count_before = sum(issue["severity"] == "error" for issue in issues)
        image_path = Path(output).expanduser()
        if not image_path.is_absolute():
            image_path = base_dir / image_path
        normalized_path = str(image_path.resolve(strict=False))
        if normalized_path in seen_outputs:
            _issue(
                issues,
                "duplicate_output",
                f"{slide_path}.output",
                f"duplicates slides[{seen_outputs[normalized_path]}].output",
            )
        else:
            seen_outputs[normalized_path] = index

        file_result: dict[str, Any] = {
            "slide_id": slide_id,
            "output": output,
            "resolved_path": normalized_path,
            "status": "fail",
        }
        files.append(file_result)
        try:
            Path(normalized_path).relative_to(base_dir.resolve(strict=False))
            is_external = False
        except ValueError:
            is_external = True
        if is_external and not allow_external_paths:
            _issue(
                issues,
                "external_path_not_allowed",
                f"{slide_path}.output",
                "resolved output is outside the manifest directory",
            )
            continue
        if not image_path.is_file():
            _issue(issues, "missing_file", f"{slide_path}.output", "output is not a regular file")
            continue

        try:
            data = image_path.read_bytes()
        except OSError as exc:
            _issue(issues, "file_read_error", f"{slide_path}.output", str(exc))
            continue
        digest = hashlib.sha256(data).hexdigest()
        size_bytes = len(data)
        file_result.update({"bytes": size_bytes, "sha256": digest})
        digest_to_slides.setdefault(digest, []).append(slide_id or f"index:{index}")

        extension_format = _extension_format(image_path)
        if extension_format is None:
            _issue(
                issues,
                "unsupported_extension",
                f"{slide_path}.output",
                "extension must be .png, .jpg, .jpeg or .webp",
            )

        try:
            image_details = inspect_image_details(data)
        except ImageHeaderError as exc:
            _issue(issues, "invalid_image", f"{slide_path}.output", str(exc))
            continue

        detected_format = image_details["format"]
        width = image_details["width_px"]
        height = image_details["height_px"]
        aspect_ratio = width / height
        file_result.update(image_details)
        file_result["aspect_ratio"] = round(aspect_ratio, 6)
        if extension_format is not None and extension_format != detected_format:
            _issue(
                issues,
                "extension_signature_mismatch",
                f"{slide_path}.output",
                f"extension indicates {extension_format}, signature indicates {detected_format}",
            )
        if allowed_formats and detected_format not in allowed_formats:
            _issue(
                issues,
                "format_not_allowed",
                f"{slide_path}.output",
                f"{detected_format} is not allowed by the selected profile",
            )

        comparisons = (
            ("min_width_px", width, lambda actual, limit: actual < limit, "width is below profile minimum"),
            ("max_width_px", width, lambda actual, limit: actual > limit, "width exceeds profile maximum"),
            ("min_height_px", height, lambda actual, limit: actual < limit, "height is below profile minimum"),
            ("max_height_px", height, lambda actual, limit: actual > limit, "height exceeds profile maximum"),
            ("min_aspect_ratio", aspect_ratio, lambda actual, limit: actual < limit, "aspect ratio is below profile minimum"),
            ("max_aspect_ratio", aspect_ratio, lambda actual, limit: actual > limit, "aspect ratio exceeds profile maximum"),
            ("max_bytes", size_bytes, lambda actual, limit: actual > limit, "file size exceeds profile maximum"),
        )
        for field, actual, violates, message in comparisons:
            limit = limits.get(field)
            if limit is not None and violates(actual, limit):
                _issue(
                    issues,
                    "profile_constraint",
                    f"{slide_path}.output",
                    f"{message}: {actual} vs {field}={limit}",
                )
        if sum(issue["severity"] == "error" for issue in issues) == file_error_count_before:
            file_result["status"] = "pass"

    if slides and len(seen_orders) == len(slides):
        expected_orders = set(range(1, len(slides) + 1))
        actual_orders = set(seen_orders)
        if actual_orders != expected_orders:
            _issue(
                issues,
                "non_contiguous_order",
                "slides",
                f"orders must be contiguous from 1 to {len(slides)}; got {sorted(actual_orders)}",
            )

    for digest, slide_ids in digest_to_slides.items():
        if len(slide_ids) > 1:
            duplicate_groups.append({"sha256": digest, "slide_ids": slide_ids})
            if duplicate_policy != "allow":
                _issue(
                    issues,
                    "duplicate_content",
                    "slides",
                    f"identical image content is reused by: {', '.join(slide_ids)}",
                    severity="error" if duplicate_policy == "fail" else "warning",
                )
            if duplicate_policy == "fail":
                for file_result in files:
                    if file_result.get("sha256") == digest:
                        file_result["status"] = "fail"

    linked_summary: dict[str, Any] | None = None
    if production_manifest_file is not None:
        linked_summary = {
            "file": production_manifest_file,
            "resolved_path": str(linked_path) if linked_path is not None else None,
            "run_id": None,
        }
    if linked_path is not None:
        if not isinstance(production_manifest, dict):
            _issue(
                issues,
                "invalid_production_manifest",
                "production_manifest",
                "linked production manifest must be a JSON object",
            )
        else:
            if (
                type(production_manifest.get("schema_version")) is not int
                or production_manifest.get("schema_version") != 1
            ):
                _issue(
                    issues,
                    "production_schema_version",
                    "production_manifest.schema_version",
                    "must equal 1",
                )
            production_run_id = _non_empty_string(
                production_manifest, "run_id", "production_manifest", issues
            )
            if linked_summary is not None:
                linked_summary["run_id"] = production_run_id
            if run_id is not None and production_run_id is not None and run_id != production_run_id:
                _issue(
                    issues,
                    "run_id_mismatch",
                    "production_manifest.run_id",
                    "must exactly match media manifest run_id",
                )

            production_slides = production_manifest.get("slides")
            production_slide_ids: set[str] = set()
            production_slide_ids_valid = True
            if not isinstance(production_slides, list):
                _issue(
                    issues,
                    "invalid_production_slides",
                    "production_manifest.slides",
                    "must be an array",
                )
                production_slide_ids_valid = False
            else:
                for index, production_slide in enumerate(production_slides):
                    item_path = f"production_manifest.slides[{index}]"
                    if not isinstance(production_slide, dict):
                        _issue(issues, "invalid_production_slide", item_path, "must be an object")
                        production_slide_ids_valid = False
                        continue
                    production_slide_id = _non_empty_string(
                        production_slide, "id", item_path, issues
                    )
                    if production_slide_id is None:
                        production_slide_ids_valid = False
                    elif production_slide_id in production_slide_ids:
                        _issue(
                            issues,
                            "duplicate_production_slide_id",
                            f"{item_path}.id",
                            f"duplicate production slide ID: {production_slide_id}",
                        )
                        production_slide_ids_valid = False
                    else:
                        production_slide_ids.add(production_slide_id)

            if production_slide_ids_valid:
                media_slide_ids = set(seen_ids)
                if media_slide_ids != production_slide_ids:
                    missing = sorted(production_slide_ids - media_slide_ids)
                    extra = sorted(media_slide_ids - production_slide_ids)
                    _issue(
                        issues,
                        "slide_id_set_mismatch",
                        "slides",
                        f"must exactly match production slides; missing={missing}, extra={extra}",
                    )

    return _report(
        manifest_path,
        run_id,
        linked_summary,
        profile_summary,
        files,
        duplicate_groups,
        issues,
        len(slides),
    )


def _report(
    manifest_path: str,
    run_id: str | None,
    linked_production_manifest: dict[str, Any] | None,
    profile: dict[str, Any] | None,
    files: list[dict[str, Any]],
    duplicate_groups: list[dict[str, Any]],
    issues: list[dict[str, str]],
    slide_count: int,
) -> dict[str, Any]:
    error_count = sum(issue["severity"] == "error" for issue in issues)
    warning_count = sum(issue["severity"] == "warning" for issue in issues)
    return {
        "report_version": REPORT_VERSION,
        "validator": "preflight_media",
        "manifest_path": manifest_path,
        "run_id": run_id,
        "linked_production_manifest": linked_production_manifest,
        "status": "pass" if error_count == 0 else "fail",
        "media_gate_ready": error_count == 0,
        "profile": profile,
        "summary": {
            "slide_count": slide_count,
            "checked_file_count": len(files),
            "passed_file_count": sum(item.get("status") == "pass" for item in files),
            "error_count": error_count,
            "warning_count": warning_count,
            "duplicate_group_count": len(duplicate_groups),
        },
        "files": files,
        "duplicate_groups": duplicate_groups,
        "issues": issues,
        "not_checked": NOT_CHECKED,
        "boundary": BOUNDARY,
    }


def _cli_error(
    message: str,
    manifest_path: str | None = None,
    *,
    run_id: str | None = None,
    linked_production_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "report_version": REPORT_VERSION,
        "validator": "preflight_media",
        "manifest_path": manifest_path,
        "run_id": run_id,
        "linked_production_manifest": linked_production_manifest,
        "status": "error",
        "media_gate_ready": False,
        "summary": {"slide_count": 0, "checked_file_count": 0, "passed_file_count": 0, "error_count": 1, "warning_count": 0, "duplicate_group_count": 0},
        "files": [],
        "duplicate_groups": [],
        "issues": [{"severity": "error", "code": "input_error", "path": "$", "message": message}],
        "not_checked": NOT_CHECKED,
        "boundary": BOUNDARY,
    }


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 1 or args[0] in {"-h", "--help"}:
        report = _cli_error("usage: preflight_media.py MEDIA_MANIFEST.json")
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    manifest_file = Path(args[0]).expanduser()
    try:
        raw = manifest_file.read_text(encoding="utf-8")
        manifest = json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        report = _cli_error(f"cannot read manifest JSON: {exc}", str(manifest_file))
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return 2

    base_dir = manifest_file.resolve(strict=False).parent
    linked_path = _linked_manifest_path(manifest, base_dir)
    production_manifest = None
    if linked_path is not None:
        try:
            linked_raw = linked_path.read_text(encoding="utf-8")
            production_manifest = json.loads(linked_raw)
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            run_id = manifest.get("run_id") if isinstance(manifest, dict) else None
            declared_link = (
                manifest.get("production_manifest_file") if isinstance(manifest, dict) else None
            )
            report = _cli_error(
                f"cannot read linked production manifest JSON: {exc}",
                str(manifest_file),
                run_id=run_id if isinstance(run_id, str) else None,
                linked_production_manifest={
                    "file": declared_link,
                    "resolved_path": str(linked_path),
                    "run_id": None,
                },
            )
            print(json.dumps(report, ensure_ascii=False, indent=2))
            return 2

    report = validate_manifest(
        manifest,
        base_dir=base_dir,
        manifest_path=str(manifest_file),
        production_manifest=production_manifest,
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report["status"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
