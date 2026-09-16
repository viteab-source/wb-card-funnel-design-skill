#!/usr/bin/env python3

from __future__ import annotations

import contextlib
import io
import json
import struct
import tempfile
import unittest
import zlib
from pathlib import Path

from preflight_media import main, validate_manifest


def _png_chunk(chunk_type: bytes, payload: bytes) -> bytes:
    checksum = zlib.crc32(chunk_type + payload) & 0xFFFFFFFF
    return struct.pack(">I", len(payload)) + chunk_type + payload + struct.pack(">I", checksum)


def png(width: int, height: int) -> bytes:
    header = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)
    pixels = b"".join(b"\x00" + (b"\x80" * width) for _ in range(height))
    return (
        b"\x89PNG\r\n\x1a\n"
        + _png_chunk(b"IHDR", header)
        + _png_chunk(b"IDAT", zlib.compress(pixels))
        + _png_chunk(b"IEND", b"")
    )


def jpeg(width: int, height: int, *, orientation: int | None = None) -> bytes:
    app0 = b"\xff\xe0" + struct.pack(">H", 16) + (b"\x00" * 14)
    app1 = b""
    if orientation is not None:
        entry = struct.pack("<HHI", 0x0112, 3, 1) + struct.pack("<H", orientation) + b"\x00\x00"
        tiff = b"II" + struct.pack("<H", 42) + struct.pack("<I", 8) + struct.pack("<H", 1) + entry + struct.pack("<I", 0)
        exif = b"Exif\x00\x00" + tiff
        app1 = b"\xff\xe1" + struct.pack(">H", len(exif) + 2) + exif
    components = b"\x01\x11\x00\x02\x11\x00\x03\x11\x00"
    sof0_payload = b"\x08" + struct.pack(">HHB", height, width, 3) + components
    sof0 = b"\xff\xc0" + struct.pack(">H", len(sof0_payload) + 2) + sof0_payload
    sos_payload = b"\x03\x01\x00\x02\x00\x03\x00\x00\x3f\x00"
    sos = b"\xff\xda" + struct.pack(">H", len(sos_payload) + 2) + sos_payload
    return b"\xff\xd8" + app0 + app1 + sof0 + sos + b"\x00\xff\xd9"


def webp(width: int, height: int) -> bytes:
    payload = (
        b"\x00\x00\x00\x00"
        + (width - 1).to_bytes(3, "little")
        + (height - 1).to_bytes(3, "little")
    )
    chunk = b"VP8X" + struct.pack("<I", len(payload)) + payload
    return b"RIFF" + struct.pack("<I", 4 + len(chunk)) + b"WEBP" + chunk


def profile(**overrides: object) -> dict:
    result = {
        "id": "test-profile",
        "basis": "Test-only internal profile, not an official WB rule",
        "checked_at": "2026-09-06",
        "allowed_formats": ["png", "jpeg", "webp"],
        "min_width_px": 90,
        "max_width_px": 900,
        "min_height_px": 120,
        "max_height_px": 1200,
        "min_aspect_ratio": 0.74,
        "max_aspect_ratio": 0.76,
        "max_bytes": 100000,
        "duplicate_content_policy": "fail",
        "allow_external_paths": False,
    }
    result.update(overrides)
    return result


def production_manifest(slide_ids: list[str], *, run_id: str = "test-run") -> dict:
    return {
        "schema_version": 1,
        "run_id": run_id,
        "slides": [{"id": slide_id, "role": "canonical-only"} for slide_id in slide_ids],
    }


def manifest(output: str, *, selected_profile: dict | None = None) -> dict:
    return {
        "schema_version": 1,
        "run_id": "test-run",
        "production_manifest_file": "production-manifest.json",
        "status": "ready",
        "profile": selected_profile or profile(),
        "slides": [
            {"id": "cover", "order": 1, "status": "ready", "output": output}
        ],
    }


class PreflightMediaTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)

    def tearDown(self) -> None:
        self.temp.cleanup()

    def validate(self, data: dict, linked: dict | None = None) -> dict:
        if linked is None:
            slide_ids = []
            for slide in data.get("slides", []):
                if isinstance(slide, dict) and isinstance(slide.get("id"), str):
                    slide_id = slide["id"].strip()
                    if slide_id and slide_id not in slide_ids:
                        slide_ids.append(slide_id)
            linked = production_manifest(slide_ids, run_id=data.get("run_id", "test-run"))
        return validate_manifest(
            data,
            base_dir=self.base,
            manifest_path="manifest.json",
            production_manifest=linked,
        )

    def test_clothing_png_passes_technical_preflight(self) -> None:
        (self.base / "dress.png").write_bytes(png(300, 400))
        report = self.validate(manifest("dress.png"))
        self.assertEqual(report["status"], "pass")
        self.assertTrue(report["media_gate_ready"])
        self.assertEqual(report["files"][0]["format"], "png")
        self.assertEqual(report["run_id"], "test-run")
        self.assertEqual(report["linked_production_manifest"]["run_id"], "test-run")
        self.assertIn("product_and_variant_visual_fidelity", report["not_checked"])
        self.assertIn("does not assign production-ready", report["boundary"])

    def test_electronics_webp_passes_technical_preflight(self) -> None:
        (self.base / "charger.webp").write_bytes(webp(300, 400))
        report = self.validate(manifest("charger.webp"))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["files"][0]["width_px"], 300)

    def test_household_goods_jpeg_passes_technical_preflight(self) -> None:
        (self.base / "container.jpg").write_bytes(jpeg(300, 400))
        report = self.validate(manifest("container.jpg"))
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["files"][0]["format"], "jpeg")

    def test_jpeg_exif_orientation_uses_effective_dimensions(self) -> None:
        for orientation in (6, 8):
            with self.subTest(orientation=orientation):
                filename = f"rotated-{orientation}.jpg"
                (self.base / filename).write_bytes(jpeg(400, 300, orientation=orientation))
                report = self.validate(manifest(filename))
                self.assertEqual(report["status"], "pass")
                self.assertEqual(report["files"][0]["encoded_width_px"], 400)
                self.assertEqual(report["files"][0]["width_px"], 300)
                self.assertEqual(report["files"][0]["height_px"], 400)
                self.assertEqual(report["files"][0]["exif_orientation"], orientation)

    def test_rejects_duplicate_ids_orders_outputs_and_content(self) -> None:
        image = png(300, 400)
        (self.base / "one.png").write_bytes(image)
        (self.base / "two.png").write_bytes(image)
        data = manifest("one.png")
        data["slides"].append(
            {"id": "cover", "order": 1, "status": "ready", "output": "one.png"}
        )
        data["slides"].append(
            {"id": "detail", "order": 2, "status": "ready", "output": "two.png"}
        )
        report = self.validate(data)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertEqual(report["status"], "fail")
        self.assertTrue(
            {"duplicate_slide_id", "duplicate_order", "duplicate_output", "duplicate_content"}.issubset(codes)
        )
        self.assertEqual(report["summary"]["duplicate_group_count"], 1)

    def test_rejects_non_contiguous_gallery_order(self) -> None:
        (self.base / "one.png").write_bytes(png(300, 400))
        (self.base / "two.png").write_bytes(png(303, 404))
        data = manifest("one.png")
        data["slides"].append(
            {"id": "detail", "order": 3, "status": "ready", "output": "two.png"}
        )
        report = self.validate(data)
        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["media_gate_ready"])
        self.assertIn("non_contiguous_order", {item["code"] for item in report["issues"]})

    def test_duplicate_content_can_be_warning_by_profile_policy(self) -> None:
        image = png(300, 400)
        (self.base / "one.png").write_bytes(image)
        (self.base / "two.png").write_bytes(image)
        data = manifest(
            "one.png",
            selected_profile=profile(duplicate_content_policy="warn"),
        )
        data["slides"].append(
            {"id": "detail", "order": 2, "status": "ready", "output": "two.png"}
        )
        report = self.validate(data)
        self.assertEqual(report["status"], "pass")
        self.assertEqual(report["summary"]["warning_count"], 1)

    def test_external_path_requires_explicit_profile_opt_in(self) -> None:
        with tempfile.TemporaryDirectory() as external_temp:
            external = Path(external_temp) / "outside.png"
            external.write_bytes(png(300, 400))
            report = self.validate(manifest(str(external)))
            self.assertEqual(report["status"], "fail")
            self.assertIn("external_path_not_allowed", {item["code"] for item in report["issues"]})

            allowed = manifest(
                str(external),
                selected_profile=profile(allow_external_paths=True),
            )
            self.assertEqual(self.validate(allowed)["status"], "pass")

    def test_rejects_missing_not_ready_and_profile_violations(self) -> None:
        (self.base / "small.png").write_bytes(png(50, 50))
        data = manifest("small.png", selected_profile=profile(max_bytes=10))
        data["status"] = "draft"
        data["slides"][0]["status"] = "blocked"
        report = self.validate(data)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertTrue({"manifest_not_ready", "slide_not_ready", "profile_constraint"}.issubset(codes))
        self.assertGreaterEqual(sum(issue["code"] == "profile_constraint" for issue in report["issues"]), 4)

    def test_rejects_extension_signature_mismatch_and_corruption(self) -> None:
        (self.base / "wrong.jpg").write_bytes(png(300, 400))
        (self.base / "broken.webp").write_bytes(b"RIFF\x00\x00\x00\x00WEBP")
        data = manifest("wrong.jpg")
        data["slides"].append(
            {"id": "detail", "order": 2, "status": "ready", "output": "broken.webp"}
        )
        report = self.validate(data)
        codes = {issue["code"] for issue in report["issues"]}
        self.assertIn("extension_signature_mismatch", codes)
        self.assertIn("invalid_image", codes)

    def test_rejects_broken_png_jpeg_and_webp_structure(self) -> None:
        broken_files = {
            "broken.png": png(300, 400)[:-12],
            "broken.jpg": jpeg(300, 400)[:-2],
            "broken.webp": webp(300, 400)[:-1],
        }
        for filename, content in broken_files.items():
            with self.subTest(filename=filename):
                (self.base / filename).write_bytes(content)
                report = self.validate(manifest(filename))
                self.assertEqual(report["status"], "fail")
                self.assertIn("invalid_image", {item["code"] for item in report["issues"]})

    def test_rejects_missing_required_fields(self) -> None:
        report = self.validate({"schema_version": 1})
        self.assertEqual(report["status"], "fail")
        self.assertGreater(report["summary"]["error_count"], 0)

    def test_rejects_linked_run_id_and_slide_set_mismatch(self) -> None:
        (self.base / "cover.png").write_bytes(png(300, 400))
        data = manifest("cover.png")
        linked = production_manifest(["other-slide"], run_id="another-run")
        report = self.validate(data, linked)
        codes = {item["code"] for item in report["issues"]}
        self.assertEqual(report["status"], "fail")
        self.assertIn("run_id_mismatch", codes)
        self.assertIn("slide_id_set_mismatch", codes)

    def test_rejects_canonical_product_and_role_copies(self) -> None:
        (self.base / "cover.png").write_bytes(png(300, 400))
        data = manifest("cover.png")
        data["product"] = {"name": "duplicated"}
        data["slides"][0]["role"] = "duplicated"
        report = self.validate(data)
        self.assertEqual(report["status"], "fail")
        self.assertEqual(
            sum(item["code"] == "canonical_field_not_allowed" for item in report["issues"]),
            2,
        )

    def test_rejects_external_production_manifest_link(self) -> None:
        (self.base / "cover.png").write_bytes(png(300, 400))
        data = manifest("cover.png")
        data["production_manifest_file"] = "../production-manifest.json"
        report = self.validate(data)
        self.assertEqual(report["status"], "fail")
        self.assertIn(
            "invalid_production_manifest_path",
            {item["code"] for item in report["issues"]},
        )

    def test_cli_returns_documented_codes_and_json(self) -> None:
        image_path = self.base / "cover.png"
        image_path.write_bytes(png(300, 400))
        manifest_path = self.base / "media.json"
        linked_path = self.base / "production-manifest.json"
        linked_path.write_text(json.dumps(production_manifest(["cover"])), encoding="utf-8")
        manifest_path.write_text(json.dumps(manifest("cover.png")), encoding="utf-8")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            success_code = main([str(manifest_path)])
        self.assertEqual(success_code, 0)
        self.assertEqual(json.loads(output.getvalue())["status"], "pass")

        manifest_path.write_text("{bad json", encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            parse_code = main([str(manifest_path)])
        self.assertEqual(parse_code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")

        manifest_path.write_text(json.dumps({"schema_version": 1}), encoding="utf-8")
        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            validation_code = main([str(manifest_path)])
        self.assertEqual(validation_code, 1)
        self.assertEqual(json.loads(output.getvalue())["status"], "fail")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            usage_code = main([])
        self.assertEqual(usage_code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")

    def test_cli_unreadable_linked_manifest_is_input_error(self) -> None:
        (self.base / "cover.png").write_bytes(png(300, 400))
        data = manifest("cover.png")
        data["production_manifest_file"] = "missing-production.json"
        manifest_path = self.base / "media.json"
        manifest_path.write_text(json.dumps(data), encoding="utf-8")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main([str(manifest_path)])
        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 2)
        self.assertEqual(report["status"], "error")
        self.assertEqual(report["linked_production_manifest"]["file"], "missing-production.json")


    def test_cli_malformed_linked_json_is_input_error(self) -> None:
        (self.base / "cover.png").write_bytes(png(300, 400))
        (self.base / "production-manifest.json").write_text("{bad json", encoding="utf-8")
        manifest_path = self.base / "media.json"
        manifest_path.write_text(json.dumps(manifest("cover.png")), encoding="utf-8")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main([str(manifest_path)])
        self.assertEqual(exit_code, 2)
        self.assertEqual(json.loads(output.getvalue())["status"], "error")

    def test_cli_link_mismatch_is_validation_failure(self) -> None:
        (self.base / "cover.png").write_bytes(png(300, 400))
        linked_path = self.base / "production-manifest.json"
        linked_path.write_text(
            json.dumps(production_manifest(["other-slide"], run_id="other-run")),
            encoding="utf-8",
        )
        manifest_path = self.base / "media.json"
        manifest_path.write_text(json.dumps(manifest("cover.png")), encoding="utf-8")

        output = io.StringIO()
        with contextlib.redirect_stdout(output):
            exit_code = main([str(manifest_path)])
        report = json.loads(output.getvalue())
        self.assertEqual(exit_code, 1)
        self.assertEqual(report["status"], "fail")
        codes = {item["code"] for item in report["issues"]}
        self.assertIn("run_id_mismatch", codes)
        self.assertIn("slide_id_set_mismatch", codes)


if __name__ == "__main__":
    unittest.main()
