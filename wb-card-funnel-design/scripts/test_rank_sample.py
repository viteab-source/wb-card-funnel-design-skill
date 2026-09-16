#!/usr/bin/env python3

from __future__ import annotations

import unittest
from datetime import date, datetime, timedelta, timezone

from rank_sample import DataError, analyze


def row(
    rank: int,
    *,
    sales: int | None = None,
    reviews: int | None = None,
    sponsored: bool | None = False,
    image: bool = True,
) -> dict:
    return {
        "rank": rank,
        "nm_id": str(1000 + rank),
        "query": "кроссовки мужские осенние",
        "observed_at": (
            datetime(2026, 9, 3, 10, 0, tzinfo=timezone.utc) + timedelta(seconds=rank)
        ).isoformat(),
        "period_start": date(2026, 6, 5),
        "period_end": date(2026, 9, 2),
        "source_url": f"https://www.wildberries.ru/catalog/{1000 + rank}/detail.aspx",
        "sponsored": sponsored,
        "main_image_ref": f"cover-{rank}.jpg" if image else "",
        "review_count": rank * 10,
        "reviews_90d": reviews,
        "reviews_90d_coverage": "complete" if reviews is not None else "unknown",
        "mpstats_sales_90d_est": sales,
        "mpstats_revenue_90d_est": (sales or 0) * 100,
    }


def metadata(status: str = "ready", block_reason: str | None = None) -> dict:
    return {
        "sample_id": "20260903-1300-krossovki",
        "query": "кроссовки мужские осенние",
        "observed_started_at": "2026-09-03T13:00:00+03:00",
        "observed_finished_at": "2026-09-03T13:10:00+03:00",
        "timezone": "Europe/Moscow",
        "region": "Москва",
        "device": "desktop",
        "viewport_width": 1440,
        "auth_mode": "guest",
        "sort": "popular",
        "filters": [],
        "period_start": "2026-06-05",
        "period_end": "2026-09-02",
        "sample_status": status,
        "block_reason": block_reason,
        "collection_source": "wb_browser",
    }


class AnalyzeTests(unittest.TestCase):
    def test_prefers_complete_mpstats_sales(self) -> None:
        rows = [row(i, sales=i) for i in range(1, 101)]
        result = analyze(rows)
        self.assertTrue(result["sample_complete"])
        self.assertEqual(result["ranking_basis"], "mpstats_sales_estimate_90d")
        self.assertEqual(result["top5"][0]["nm_id"], "1100")

    def test_uses_complete_review_proxy(self) -> None:
        rows = [row(i, reviews=i) for i in range(1, 6)]
        result = analyze(rows)
        self.assertEqual(result["ranking_basis"], "reviews_90d_activity_proxy")
        self.assertEqual(result["top5"][0]["nm_id"], "1005")

    def test_rejects_duplicate_nm_id(self) -> None:
        rows = [row(1, sales=1), row(2, sales=2)]
        rows[1]["nm_id"] = rows[0]["nm_id"]
        with self.assertRaises(DataError):
            analyze(rows)

    def test_distinguishes_structural_and_research_completeness(self) -> None:
        rows = [row(i, sales=i) for i in range(1, 101)]
        rows[0]["sponsored"] = None
        rows[1]["main_image_ref"] = ""
        result = analyze(rows, metadata())
        self.assertTrue(result["sample_complete"])
        self.assertFalse(result["research_complete"])
        self.assertEqual(result["sponsored_unknown_count"], 1)
        self.assertEqual(result["coverage"]["main_images"], 0.99)

    def test_allows_blocked_empty_sample_without_ranking(self) -> None:
        result = analyze([], metadata("blocked", "WB HTTP 498"))
        self.assertEqual(result["sample_count"], 0)
        self.assertEqual(result["ranking_basis"], "insufficient_data")
        self.assertEqual(result["top5"], [])


if __name__ == "__main__":
    unittest.main()
