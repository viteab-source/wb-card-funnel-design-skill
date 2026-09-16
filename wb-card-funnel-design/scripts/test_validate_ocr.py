import unittest

from validate_ocr import evaluate, inside, safe_output
from pathlib import Path


class OcrGateTests(unittest.TestCase):
    def test_typo_cannot_pass(self):
        result = evaluate("насадки", [{"text": "насалки", "confidence": 0.9}], None)
        self.assertEqual(result["status"], "review")

    def test_fallback_cannot_assert_pass_without_confidence(self):
        result = evaluate("ВТ", None, "ВТ")
        self.assertEqual(result["status"], "review")

    def test_missing_backend_blocks(self):
        self.assertEqual(evaluate("20 ВТ", None, None)["status"], "blocked")

    def test_vision_match_can_pass(self):
        self.assertEqual(evaluate("USB-C — USB-C", [{"text": "USB-C - USB-C", "confidence": 0.8}], None)["status"], "pass")

    def test_box_prevents_cross_zone_match(self):
        self.assertFalse(inside([700, 100, 800, 140], [0, 60, 500, 160]))

    def test_output_cannot_escape_run(self):
        with self.assertRaises(ValueError):
            safe_output(Path("/tmp/ocr-run"), "../elsewhere.png")


if __name__ == "__main__":
    unittest.main()
