from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from validate_design_gate import validate


def make_complete_run(root: Path) -> None:
    (root / "intake.json").write_text(json.dumps({"product": {"name": "Термокружка", "variant": "500 мл, графит"}, "commercial_messages": [{"status": "confirmed", "buyer_question": "Сколько держит напиток", "fact_refs": ["fact-01"]}]}), encoding="utf-8")
    (root / "product-passport.md").write_text("Паспорт товара. " * 12, encoding="utf-8")
    (root / "research-evidence.json").write_text(json.dumps({"status": "reviewed", "search_context": {"query": "термокружка 500 мл", "observed_at": "2026-09-14", "region": "Москва", "device": "mobile"}, "competitor_ids": ["100", "200"]}), encoding="utf-8")
    candidates = [{"competitor_id": str(number), "url": f"https://www.wildberries.ru/catalog/{number}/detail.aspx", "cover_file": f"competitors/wb-{number}/01.webp", "match_reason": "тот же тип товара", "user_decision": "approved", "user_feedback": "подходит"} for number in (100, 200)]
    (root / "competitor-shortlist.json").write_text(json.dumps({"status": "user_approved", "candidates": candidates, "approved_competitor_ids": ["100", "200"], "rejected_competitor_ids": [], "user_evidence": "Пользователь: подходят оба"}), encoding="utf-8")
    funnel_competitors = []
    for number in (100, 200):
        funnel_competitors.append({"competitor_id": str(number), "gallery_complete": True, "slide_count": 1, "slides": [{"position": 1, "file": "01.webp", "visible_text": ["Термокружка"], "role": "cover", "buyer_question": "что за товар", "scene_or_angle": "фронтально", "composition": "товар по центру", "claim_or_benefit": "категория", "proof": "фото", "notes": ""}]})
    (root / "competitor-funnel-analysis.json").write_text(json.dumps({"status": "reviewed", "approved_competitor_ids": ["100", "200"], "competitors": funnel_competitors, "cross_competitor_synthesis": {"question_bank": ["что за товар"]}}), encoding="utf-8")
    (root / "competitor-analysis.md").write_text("Разбор категории и свободной зоны. " * 8, encoding="utf-8")
    for number in (100, 200):
        folder = root / "competitors" / f"wb-{number}"
        folder.mkdir(parents=True)
        (folder / "source.json").write_text("{}", encoding="utf-8")
        (folder / "01.webp").write_bytes(b"image")
    (root / "visual-dna.json").write_text(json.dumps({"status": "reviewed", "product_anchor": {"asset_refs": ["photo-01"], "immutable_traits": [{"trait": "цвет"}]}, "art_direction": {"id": "category-fit", "approval_status": "autopilot_selected"}, "qa": {"art_direction_checked": True, "product_fidelity_checked": True}}), encoding="utf-8")
    prompts = root / "media" / "hero-concepts" / "prompts"
    prompts.mkdir(parents=True)
    for number, text in (("01", "крупный товар на камне"), ("02", "товар в руке у окна"), ("03", "товар на столе в офисе")):
        (prompts / f"{number}.md").write_text(text * 8, encoding="utf-8")


class DesignGateTests(unittest.TestCase):
    def test_empty_run_is_blocked_at_research(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            errors = validate(Path(tmp), "hero")["errors"]
            self.assertIn("missing_or_empty:research-evidence.json", errors)
            self.assertIn("hero_concepts:need_3_saved_prompts", errors)

    def test_hero_rejects_duplicate_prompts(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_complete_run(root)
            prompt = root / "media/hero-concepts/prompts/03.md"
            prompt.write_text((root / "media/hero-concepts/prompts/01.md").read_text(encoding="utf-8"), encoding="utf-8")
            self.assertIn("hero_concepts:prompts_must_be_distinct", validate(root, "hero")["errors"])

    def test_hero_blocks_without_explicit_competitor_approval(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_complete_run(root)
            shortlist = json.loads((root / "competitor-shortlist.json").read_text(encoding="utf-8"))
            shortlist["status"] = "pending_user_review"
            shortlist["user_evidence"] = None
            (root / "competitor-shortlist.json").write_text(json.dumps(shortlist), encoding="utf-8")
            errors = validate(root, "hero")["errors"]
            self.assertIn("competitor-shortlist.json:status_not_accepted", errors)
            self.assertIn("competitor-shortlist.json:user_evidence_required", errors)

    def test_hero_blocks_incomplete_approved_gallery(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_complete_run(root)
            funnel = json.loads((root / "competitor-funnel-analysis.json").read_text(encoding="utf-8"))
            funnel["competitors"][0]["gallery_complete"] = False
            (root / "competitor-funnel-analysis.json").write_text(json.dumps(funnel), encoding="utf-8")
            self.assertIn("competitor-funnel-analysis.json:gallery_not_complete:100", validate(root, "hero")["errors"])

    def test_gallery_requires_selection_from_prompt_set(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_complete_run(root)
            (root / "media/hero-concepts/contact-sheet.png").write_bytes(b"sheet")
            (root / "hero-selection.json").write_text(json.dumps({"status": "autopilot_selected", "selected_candidate": "99", "reviewed_candidates": ["01", "02", "03"]}), encoding="utf-8")
            self.assertIn("hero-selection.json:selected_candidate_not_in_prompts", validate(root, "gallery")["errors"])

    def test_delivery_requires_manual_qa(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_complete_run(root)
            (root / "media/hero-concepts/contact-sheet.png").write_bytes(b"sheet")
            (root / "hero-selection.json").write_text(json.dumps({"status": "user_approved", "selected_candidate": "02", "reviewed_candidates": ["01", "02", "03"]}), encoding="utf-8")
            (root / "qa-checklist.json").write_text(json.dumps({"status": "pass", "checks": [{"id": "product_fidelity", "status": "pass"}]}), encoding="utf-8")
            self.assertIn("qa-checklist.json:required_manual_checks_not_passed", validate(root, "delivery")["errors"])

    def test_complete_delivery_evidence_passes(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            make_complete_run(root)
            (root / "media/hero-concepts/contact-sheet.png").write_bytes(b"sheet")
            (root / "hero-selection.json").write_text(json.dumps({"status": "autopilot_selected", "selected_candidate": "02", "reviewed_candidates": ["01", "02", "03"]}), encoding="utf-8")
            checks = [{"id": item, "status": "pass"} for item in ("product_fidelity", "claims_and_implied_claims", "rights", "mobile_readability", "wb_rules", "series_review")]
            (root / "qa-checklist.json").write_text(json.dumps({"status": "pass", "checks": checks}), encoding="utf-8")
            self.assertEqual("pass", validate(root, "delivery")["status"])


if __name__ == "__main__":
    unittest.main()
