#!/usr/bin/env python3

import contextlib
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import validate_claims


def make_intake(category="Одежда", variant="чёрный / M"):
    return {
        "schema_version": 1,
        "run_id": "run-001",
        "marketplace": "WB",
        "product": {"name": "Тестовый товар", "category": category, "variant": variant},
        "facts": [],
        "assets": [],
        "readiness": {
            "design": {"status": "ready", "reason": "Факты и фото проверены"},
            "generation": {"status": "ready", "reason": "Файл собран"},
        },
    }


def make_manifest(variant="чёрный / M"):
    return {
        "schema_version": 1,
        "run_id": "run-001",
        "intake_file": "intake.json",
        "target": {"variant": variant, "variant_scope": "single_variant"},
        "readiness_status": "production-ready",
        "claims": [],
        "slides": [],
    }


class ClaimsValidationTests(unittest.TestCase):
    def test_excluded_fact_is_allowed_and_not_reported_as_unused(self):
        intake = make_intake()
        intake["facts"] = [
            {
                "id": "fact-retired-brand",
                "statement": "Тестовое название исключено и не должно попасть в карточку.",
                "source": "user-decision",
                "variant": "чёрный / M",
                "scope": "brand",
                "status": "excluded",
            }
        ]
        manifest = make_manifest()
        manifest["readiness_status"] = "concept-ready"

        report = validate_claims.validate_documents(intake, manifest)

        self.assertEqual("pass", report["status"])
        self.assertNotIn("unused_fact", {item["code"] for item in report["warnings"]})

    def test_clothing_explicit_and_implied_claims_pass(self):
        intake = make_intake()
        intake["facts"] = [
            {
                "id": "fact-material",
                "statement": "Состав: 100% хлопок",
                "source": "label-front.jpg",
                "variant": "чёрный / M",
                "scope": "product",
                "status": "confirmed",
            },
            {
                "id": "fact-fit",
                "statement": "Посадка oversize",
                "source": "measurement-protocol.xlsx",
                "variant": "чёрный / M",
                "scope": "fit",
                "status": "confirmed",
            },
        ]
        intake["assets"] = [
            {
                "id": "photo-front",
                "file": "front.png",
                "role": "front",
                "variant": "чёрный / M",
                "quality": "accepted",
                "rights_status": "user_confirmed",
            }
        ]
        manifest = make_manifest()
        manifest["claims"] = [
            {
                "id": "claim-material",
                "kind": "explicit",
                "text": "100% хлопок",
                "scope": "product",
                "variant": "чёрный / M",
                "fact_refs": ["fact-material"],
                "status": "approved",
            },
            {
                "id": "claim-fit",
                "kind": "implied",
                "text": "Силуэт на модели показывает oversize-посадку",
                "scope": "fit",
                "variant": "чёрный / M",
                "fact_refs": ["fact-fit"],
                "status": "approved",
            },
        ]
        manifest["slides"] = [
            {
                "id": "slide-cover",
                "role": "cover",
                "variant": "чёрный / M",
                "claim_refs": ["claim-material", "claim-fit"],
                "asset_refs": ["photo-front"],
                "status": "approved",
            }
        ]

        report = validate_claims.validate_documents(intake, manifest)

        self.assertEqual("pass", report["status"])
        self.assertTrue(report["claims_gate_ready"])
        self.assertEqual(0, report["summary"]["errors"])
        self.assertEqual(
            [
                "semantic_evidence_sufficiency",
                "rights_reality",
                "pixel_product_truth",
                "ocr_text_accuracy",
                "current_wb_compliance",
            ],
            [item["id"] for item in report["manual_checks_required"]],
        )

    def test_electronics_blocks_unknown_fact_scope_and_variant(self):
        intake = make_intake(category="Электроника", variant="EU / 220V")
        intake["facts"] = [
            {
                "id": "fact-waterproof",
                "statement": "Защита IP68",
                "source": "supplier-message.txt",
                "variant": "US / 110V",
                "scope": "compatibility",
                "status": "unknown",
            }
        ]
        intake["assets"] = [
            {
                "id": "photo-eu",
                "file": "eu-device.png",
                "role": "front",
                "variant": "EU / 220V",
                "quality": "accepted",
                "rights_status": "user_confirmed",
            }
        ]
        manifest = make_manifest(variant="EU / 220V")
        manifest["claims"] = [
            {
                "id": "claim-waterproof",
                "kind": "implied",
                "text": "Сцена под водой подразумевает полную влагозащиту",
                "scope": "water_resistance",
                "variant": "EU / 220V",
                "fact_refs": ["fact-waterproof"],
                "status": "approved",
            }
        ]
        manifest["slides"] = [
            {
                "id": "slide-water",
                "role": "benefit",
                "claim_refs": ["claim-waterproof"],
                "asset_refs": ["photo-eu"],
                "status": "approved",
            }
        ]

        report = validate_claims.validate_documents(intake, manifest)
        codes = {item["code"] for item in report["errors"]}

        self.assertEqual("fail", report["status"])
        self.assertIn("fact_not_confirmed", codes)
        self.assertIn("fact_scope_mismatch", codes)
        self.assertIn("fact_variant_mismatch", codes)
        self.assertIn("truth", report["boundary"])

    def test_general_goods_blocks_broken_refs_orphan_and_readiness(self):
        intake = make_intake(category="Товары для дома", variant="2 литра")
        intake["facts"] = [
            {
                "id": "fact-volume",
                "statement": "Объём 2 л",
                "source": "measurement-sheet.pdf",
                "variant": "2 литра",
                "scope": "product",
                "status": "confirmed",
            }
        ]
        intake["assets"] = [
            {
                "id": "photo-main",
                "file": "main.jpg",
                "role": "front",
                "variant": "2 литра",
                "quality": "accepted",
                "rights_status": "user_confirmed",
            }
        ]
        manifest = make_manifest(variant="2 литра")
        manifest["claims"] = [
            {
                "id": "claim-volume",
                "kind": "explicit",
                "text": "2 литра",
                "scope": "product",
                "fact_refs": ["fact-volume"],
                "status": "reviewed",
            },
            {
                "id": "claim-orphan",
                "kind": "explicit",
                "text": "Можно мыть в посудомоечной машине",
                "scope": "usage",
                "fact_refs": ["fact-missing"],
                "status": "draft",
            },
        ]
        manifest["slides"] = [
            {
                "id": "slide-cover",
                "role": "cover",
                "claim_refs": ["claim-volume"],
                "asset_refs": ["asset-missing"],
                "status": "reviewed",
            }
        ]

        report = validate_claims.validate_documents(intake, manifest)
        codes = {item["code"] for item in report["errors"]}

        self.assertEqual("fail", report["status"])
        self.assertIn("claim_not_ready", codes)
        self.assertIn("slide_not_ready", codes)
        self.assertIn("missing_fact_ref", codes)
        self.assertIn("missing_asset_ref", codes)
        self.assertIn("orphan_claim", codes)

    def test_null_variant_is_not_universal_but_explicit_all_variants_is(self):
        intake = make_intake(category="Товары для дома", variant="500 мл")
        intake["facts"] = [
            {
                "id": "fact-size",
                "statement": "Высота 20 см",
                "source": "measurement.pdf",
                "variant": None,
                "scope": "product",
                "status": "confirmed",
            }
        ]
        intake["assets"] = [
            {
                "id": "photo-main",
                "file": "main.jpg",
                "role": "front",
                "variant": None,
                "quality": "accepted",
                "rights_status": "user_confirmed",
            }
        ]
        manifest = make_manifest(variant="500 мл")
        manifest["claims"] = [
            {
                "id": "claim-size",
                "kind": "explicit",
                "text": "Высота 20 см",
                "scope": "product",
                "fact_refs": ["fact-size"],
                "status": "approved",
            }
        ]
        manifest["slides"] = [
            {
                "id": "slide-size",
                "role": "dimensions",
                "claim_refs": ["claim-size"],
                "asset_refs": ["photo-main"],
                "status": "approved",
            }
        ]

        report = validate_claims.validate_documents(intake, manifest)
        codes = {item["code"] for item in report["errors"]}
        self.assertIn("fact_variant_mismatch", codes)
        self.assertIn("asset_variant_mismatch", codes)

        intake["facts"][0]["variant_scope"] = "all_variants"
        intake["assets"][0]["variant_scope"] = "all_variants"
        report = validate_claims.validate_documents(intake, manifest)
        self.assertEqual("pass", report["status"])

    def test_production_blocks_unconfirmed_asset_rights(self):
        intake = make_intake(category="Одежда", variant="белый / L")
        intake["assets"] = [
            {
                "id": "photo-main",
                "file": "main.jpg",
                "role": "front",
                "variant": "белый / L",
                "quality": "accepted",
                "rights_status": "unknown",
            }
        ]
        manifest = make_manifest(variant="белый / L")
        manifest["slides"] = [
            {
                "id": "slide-cover",
                "role": "cover",
                "claim_refs": [],
                "asset_refs": ["photo-main"],
                "status": "approved",
            }
        ]

        report = validate_claims.validate_documents(intake, manifest)
        self.assertIn("asset_rights_not_confirmed", {item["code"] for item in report["errors"]})

    def test_cli_codes_and_machine_readable_output(self):
        intake = make_intake()
        manifest = make_manifest()
        manifest["readiness_status"] = "draft-brief"
        with tempfile.TemporaryDirectory() as tmp:
            intake_path = Path(tmp, "intake.json")
            manifest_path = Path(tmp, "manifest.json")
            intake_path.write_text(json.dumps(intake), encoding="utf-8")
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = validate_claims.main([str(intake_path), str(manifest_path)])
            report = json.loads(stdout.getvalue())
            self.assertEqual(0, code)
            self.assertEqual("pass", report["status"])

            invalid_manifest = dict(manifest)
            invalid_manifest["schema_version"] = 99
            manifest_path.write_text(json.dumps(invalid_manifest), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = validate_claims.main([str(intake_path), str(manifest_path)])
            report = json.loads(stdout.getvalue())
            self.assertEqual(1, code)
            self.assertEqual("fail", report["status"])

            manifest_path.write_text("{", encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = validate_claims.main([str(intake_path), str(manifest_path)])
            report = json.loads(stdout.getvalue())
            self.assertEqual(2, code)
            self.assertEqual("error", report["status"])

        stdout = io.StringIO()
        with contextlib.redirect_stdout(stdout):
            code = validate_claims.main([])
        self.assertEqual(2, code)
        usage_report = json.loads(stdout.getvalue())
        self.assertEqual("error", usage_report["status"])
        self.assertIn("manual_checks_required", usage_report)

    def test_cli_resolves_external_relative_intake_and_rejects_mismatch(self):
        intake = make_intake()
        manifest = make_manifest()
        manifest["readiness_status"] = "draft-brief"

        with tempfile.TemporaryDirectory() as tmp:
            source_dir = Path(tmp, "source")
            job_dir = Path(tmp, "job")
            source_dir.mkdir()
            job_dir.mkdir()
            intake_path = source_dir / "intake.json"
            manifest_path = job_dir / "production-manifest.json"
            intake_path.write_text(json.dumps(intake), encoding="utf-8")

            manifest["intake_file"] = "../source/intake.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = validate_claims.main([str(intake_path), str(manifest_path)])
            self.assertEqual(0, code)
            self.assertEqual("pass", json.loads(stdout.getvalue())["status"])

            manifest["intake_file"] = "../other/intake.json"
            manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
            stdout = io.StringIO()
            with contextlib.redirect_stdout(stdout):
                code = validate_claims.main([str(intake_path), str(manifest_path)])
            report = json.loads(stdout.getvalue())
            self.assertEqual(1, code)
            self.assertEqual("fail", report["status"])
            self.assertIn("intake_path_mismatch", {item["code"] for item in report["errors"]})


if __name__ == "__main__":
    unittest.main()
