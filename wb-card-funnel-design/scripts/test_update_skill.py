#!/usr/bin/env python3

import json
from pathlib import Path
import tempfile
import unittest

from update_skill import validate_candidate, version_tuple


class UpdateSkillTests(unittest.TestCase):
    def test_semver(self):
        self.assertEqual(version_tuple("v1.2.3"), (1, 2, 3))
        self.assertGreater(version_tuple("0.3.0"), version_tuple("0.2.9"))
        with self.assertRaises(ValueError):
            version_tuple("main")

    def test_candidate_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / "scripts").mkdir()
            for name in ["SKILL.md", "scripts/bootstrap_runtime.py", "scripts/doctor.py"]:
                (root / name).write_text("ok", encoding="utf-8")
            (root / "VERSION").write_text("0.2.0\n", encoding="utf-8")
            (root / "release.json").write_text(json.dumps({"name": "wb-card-funnel-design"}), encoding="utf-8")
            validate_candidate(root, "v0.2.0")
            with self.assertRaises(RuntimeError):
                validate_candidate(root, "v0.3.0")


if __name__ == "__main__":
    unittest.main()
