#!/usr/bin/env python3

import json
from pathlib import Path
import tempfile
import unittest

from PIL import Image

from bootstrap_runtime import fingerprint, runtime_python
from doctor import report
from render_infographic import render


class PortableRuntimeTests(unittest.TestCase):
    def test_fingerprint_and_platform_python_path(self):
        self.assertEqual(len(fingerprint()), 64)
        self.assertTrue(str(runtime_python()).endswith(("python", "python.exe")))

    def test_doctor_has_required_local_capabilities(self):
        result = report()
        self.assertEqual(result["status"], "ready")
        self.assertTrue(all(result["required_local_checks"].values()))

    def test_renderer_writes_exact_size_with_cyrillic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            spec = {
                "canvas": {"width": 480, "height": 640, "background": "#F3EDF8"},
                "layers": [
                    {"type": "rectangle", "box": [30, 30, 450, 190], "radius": 28, "fill": "#172A46"},
                    {"type": "text", "text": "РАСЧЁСКА ДЛЯ ЛОКОНОВ", "position": [55, 55], "size": 46,
                     "weight": 750, "max_width": 360, "fill": "#FFFFFF"},
                ],
                "output": "result.png",
            }
            spec_path = root / "spec.json"
            spec_path.write_text(json.dumps(spec, ensure_ascii=False), encoding="utf-8")
            output = render(spec_path)
            with Image.open(output) as rendered:
                self.assertEqual(rendered.size, (480, 640))


if __name__ == "__main__":
    unittest.main()
