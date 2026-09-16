#!/usr/bin/env python3
"""Validate a WB search sample and select a defensible top five."""

from __future__ import annotations

import argparse
import csv
import json
from datetime import date, datetime
from pathlib import Path
from typing import Any


REQUIRED_COLUMNS = {
    "rank",
    "nm_id",
    "query",
    "observed_at",
    "period_start",
    "period_end",
    "source_url",
    "sponsored",
    "main_image_ref",
}

REQUIRED_METADATA = {
    "sample_id",
    "query",
    "observed_started_at",
    "observed_finished_at",
    "timezone",
    "region",
    "device",
    "viewport_width",
    "auth_mode",
    "sort",
    "filters",
    "period_start",
    "period_end",
    "sample_status",
    "block_reason",
    "collection_source",
}


class DataError(ValueError):
    """Raised when the sample cannot be validated safely."""


def _integer(value: str, field: str, row_number: int, *, optional: bool = False) -> int | None:
    value = value.strip()
    if optional and not value:
        return None
    try:
        parsed = int(value)
    except ValueError as exc:
        raise DataError(f"row {row_number}: {field} must be an integer") from exc
    if parsed < 0:
        raise DataError(f"row {row_number}: {field} must be non-negative")
    return parsed


def _sponsored(value: str, row_number: int) -> bool | None:
    normalized = value.strip().lower()
    if normalized not in {"true", "false", "unknown"}:
        raise DataError(f"row {row_number}: sponsored must be true, false or unknown")
    if normalized == "unknown":
        return None
    return normalized == "true"


def _iso_datetime(value: str, row_number: int) -> str:
    normalized = value.strip()
    if not normalized:
        raise DataError(f"row {row_number}: observed_at is empty")
    try:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
    except ValueError as exc:
        raise DataError(f"row {row_number}: observed_at must be ISO 8601") from exc
    if parsed.utcoffset() is None:
        raise DataError(f"row {row_number}: observed_at must include a timezone offset")
    return normalized


def _iso_date(value: str, field: str, row_number: int) -> date:
    try:
        return date.fromisoformat(value.strip())
    except ValueError as exc:
        raise DataError(f"row {row_number}: {field} must be YYYY-MM-DD") from exc


def load_rows(path: Path) -> list[dict[str, Any]]:
    with path.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle)
        columns = set(reader.fieldnames or [])
        missing = sorted(REQUIRED_COLUMNS - columns)
        if missing:
            raise DataError(f"missing required columns: {', '.join(missing)}")

        rows: list[dict[str, Any]] = []
        for row_number, raw in enumerate(reader, start=2):
            query = (raw.get("query") or "").strip()
            nm_id = (raw.get("nm_id") or "").strip()
            source_url = (raw.get("source_url") or "").strip()
            if not query or not nm_id or not source_url:
                raise DataError(f"row {row_number}: query, nm_id and source_url are required")

            period_start = _iso_date(raw.get("period_start") or "", "period_start", row_number)
            period_end = _iso_date(raw.get("period_end") or "", "period_end", row_number)
            if period_end < period_start:
                raise DataError(f"row {row_number}: period_end is before period_start")

            reviews_coverage = (raw.get("reviews_90d_coverage") or "unknown").strip().lower()
            if reviews_coverage not in {"complete", "partial", "unknown"}:
                raise DataError(
                    f"row {row_number}: reviews_90d_coverage must be complete, partial or unknown"
                )
            if not nm_id.isdigit():
                raise DataError(f"row {row_number}: nm_id must contain digits only")

            rows.append(
                {
                    "rank": _integer(raw.get("rank") or "", "rank", row_number),
                    "nm_id": nm_id,
                    "query": query,
                    "observed_at": _iso_datetime(raw.get("observed_at") or "", row_number),
                    "period_start": period_start,
                    "period_end": period_end,
                    "source_url": source_url,
                    "sponsored": _sponsored(raw.get("sponsored") or "", row_number),
                    "main_image_ref": (raw.get("main_image_ref") or "").strip(),
                    "review_count": _integer(
                        raw.get("review_count") or "", "review_count", row_number, optional=True
                    ),
                    "reviews_90d": _integer(
                        raw.get("reviews_90d") or "", "reviews_90d", row_number, optional=True
                    ),
                    "reviews_90d_coverage": reviews_coverage,
                    "mpstats_sales_90d_est": _integer(
                        raw.get("mpstats_sales_90d_est") or "",
                        "mpstats_sales_90d_est",
                        row_number,
                        optional=True,
                    ),
                    "mpstats_revenue_90d_est": _integer(
                        raw.get("mpstats_revenue_90d_est") or "",
                        "mpstats_revenue_90d_est",
                        row_number,
                        optional=True,
                    ),
                }
            )

    if len(rows) > 100:
        raise DataError("sample contains more than 100 rows")
    return rows


def load_metadata(path: Path) -> dict[str, Any]:
    try:
        metadata = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DataError(f"invalid metadata JSON: {exc}") from exc
    if not isinstance(metadata, dict):
        raise DataError("metadata must be a JSON object")
    missing = sorted(REQUIRED_METADATA - set(metadata))
    if missing:
        raise DataError(f"missing metadata fields: {', '.join(missing)}")

    for field in (
        "sample_id",
        "query",
        "observed_started_at",
        "observed_finished_at",
        "timezone",
        "region",
        "device",
        "auth_mode",
        "sort",
        "collection_source",
    ):
        if not isinstance(metadata[field], str) or not metadata[field].strip():
            raise DataError(f"metadata field {field} must be a non-empty string")
    for field in ("period_start", "period_end", "sample_status"):
        if not isinstance(metadata[field], str) or not metadata[field].strip():
            raise DataError(f"metadata field {field} must be a non-empty string")
    if not isinstance(metadata["filters"], list):
        raise DataError("metadata field filters must be an array")
    if not isinstance(metadata["viewport_width"], int) or metadata["viewport_width"] <= 0:
        raise DataError("metadata field viewport_width must be a positive integer")
    if metadata["sample_status"] not in {"ready", "partial", "blocked"}:
        raise DataError("metadata field sample_status must be ready, partial or blocked")
    if metadata["block_reason"] is not None and not isinstance(metadata["block_reason"], str):
        raise DataError("metadata field block_reason must be a string or null")
    if metadata["sample_status"] in {"partial", "blocked"} and not (
        isinstance(metadata["block_reason"], str) and metadata["block_reason"].strip()
    ):
        raise DataError("partial or blocked metadata requires a non-empty block_reason")

    _iso_datetime(metadata["observed_started_at"], 0)
    _iso_datetime(metadata["observed_finished_at"], 0)
    period_start = _iso_date(metadata["period_start"], "period_start", 0)
    period_end = _iso_date(metadata["period_end"], "period_end", 0)
    if period_end < period_start:
        raise DataError("metadata period_end is before period_start")
    return metadata


def analyze(
    rows: list[dict[str, Any]], metadata: dict[str, Any] | None = None
) -> dict[str, Any]:
    if not rows:
        if metadata is None or metadata["sample_status"] not in {"partial", "blocked"}:
            raise DataError("empty sample requires partial or blocked metadata")
        period_start = date.fromisoformat(metadata["period_start"])
        period_end = date.fromisoformat(metadata["period_end"])
        warning = f"collection {metadata['sample_status']}: {metadata['block_reason'] or 'no reason provided'}"
        return {
            "sample_id": metadata["sample_id"],
            "query": metadata["query"],
            "sample_status": metadata["sample_status"],
            "sample_count": 0,
            "sample_complete": False,
            "research_complete": False,
            "region": metadata["region"],
            "device": metadata["device"],
            "viewport_width": metadata["viewport_width"],
            "auth_mode": metadata["auth_mode"],
            "sort": metadata["sort"],
            "filters": metadata["filters"],
            "collection_source": metadata["collection_source"],
            "observed_at_min": metadata["observed_started_at"],
            "observed_at_max": metadata["observed_finished_at"],
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
            "period_days": (period_end - period_start).days + 1,
            "sponsored_count": 0,
            "sponsored_unknown_count": 0,
            "coverage": {"main_images": 0.0, "mpstats_sales": 0.0, "reviews_90d_complete": 0.0},
            "ranking_basis": "insufficient_data",
            "warnings": [warning, "top five withheld because the sample is empty"],
            "top5": [],
        }

    ranks = [row["rank"] for row in rows]
    expected_ranks = list(range(1, len(rows) + 1))
    if sorted(ranks) != expected_ranks:
        raise DataError("ranks must be unique and contiguous from 1 to sample size")

    nm_ids = [row["nm_id"] for row in rows]
    if len(nm_ids) != len(set(nm_ids)):
        raise DataError("nm_id values must be unique")

    queries = {row["query"] for row in rows}
    periods = {(row["period_start"], row["period_end"]) for row in rows}
    if len(queries) != 1:
        raise DataError("all rows must use the same query")
    if len(periods) != 1:
        raise DataError("all rows must use the same analytical period")

    if metadata is not None:
        if metadata["query"] != next(iter(queries)):
            raise DataError("metadata query does not match CSV query")
        csv_period = next(iter(periods))
        metadata_period = (
            date.fromisoformat(metadata["period_start"]),
            date.fromisoformat(metadata["period_end"]),
        )
        if csv_period != metadata_period:
            raise DataError("metadata period does not match CSV period")
        if metadata["sample_status"] == "ready" and len(rows) != 100:
            raise DataError("ready metadata requires exactly 100 rows")

    period_start, period_end = next(iter(periods))
    period_days = (period_end - period_start).days + 1
    sales_covered = sum(row["mpstats_sales_90d_est"] is not None for row in rows)
    reviews_covered = sum(
        row["reviews_90d"] is not None and row["reviews_90d_coverage"] == "complete"
        for row in rows
    )

    warnings: list[str] = []
    if len(rows) != 100:
        warnings.append(f"partial sample: {len(rows)} of 100 unique cards")
    if period_days != 90:
        warnings.append(f"analytical period is {period_days} days, not 90")
    image_covered = sum(bool(row["main_image_ref"]) for row in rows)
    if image_covered != len(rows):
        warnings.append("some rows have no main_image_ref")
    sponsored_count = sum(row["sponsored"] is True for row in rows)
    sponsored_unknown_count = sum(row["sponsored"] is None for row in rows)
    if sponsored_unknown_count:
        warnings.append("some rows have unknown sponsored status")

    structural_complete = len(rows) == 100
    research_complete = (
        structural_complete
        and image_covered == len(rows)
        and sponsored_unknown_count == 0
        and (sales_covered == len(rows) or reviews_covered == len(rows))
        and (metadata is None or metadata["sample_status"] == "ready")
    )

    if sales_covered == len(rows):
        ranking_basis = "mpstats_sales_estimate_90d"
        ranked = sorted(
            rows,
            key=lambda row: (
                -(row["mpstats_sales_90d_est"] or 0),
                -(row["mpstats_revenue_90d_est"] or 0),
                -(row["review_count"] or 0),
                row["rank"],
                row["nm_id"],
            ),
        )
    elif reviews_covered == len(rows):
        ranking_basis = "reviews_90d_activity_proxy"
        warnings.append("review activity is a proxy and must not be called sales")
        ranked = sorted(
            rows,
            key=lambda row: (
                -(row["reviews_90d"] or 0),
                -(row["review_count"] or 0),
                row["rank"],
                row["nm_id"],
            ),
        )
    else:
        ranking_basis = "insufficient_data"
        warnings.append("top five withheld because metric coverage is incomplete")
        ranked = []

    top_five = []
    for row in ranked[:5]:
        top_five.append(
            {
                "rank": row["rank"],
                "nm_id": row["nm_id"],
                "source_url": row["source_url"],
                "sponsored": row["sponsored"],
                "main_image_ref": row["main_image_ref"],
                "review_count": row["review_count"],
                "reviews_90d": row["reviews_90d"],
                "mpstats_sales_90d_est": row["mpstats_sales_90d_est"],
                "mpstats_revenue_90d_est": row["mpstats_revenue_90d_est"],
            }
        )

    return {
        "sample_id": metadata["sample_id"] if metadata else None,
        "query": next(iter(queries)),
        "sample_status": metadata["sample_status"] if metadata else "unknown",
        "sample_count": len(rows),
        "sample_complete": structural_complete,
        "research_complete": research_complete,
        "region": metadata["region"] if metadata else "unknown",
        "device": metadata["device"] if metadata else "unknown",
        "viewport_width": metadata["viewport_width"] if metadata else None,
        "auth_mode": metadata["auth_mode"] if metadata else "unknown",
        "sort": metadata["sort"] if metadata else "unknown",
        "filters": metadata["filters"] if metadata else [],
        "collection_source": metadata["collection_source"] if metadata else "unknown",
        "observed_at_min": min(
            rows,
            key=lambda row: datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")),
        )["observed_at"],
        "observed_at_max": max(
            rows,
            key=lambda row: datetime.fromisoformat(row["observed_at"].replace("Z", "+00:00")),
        )["observed_at"],
        "period_start": period_start.isoformat(),
        "period_end": period_end.isoformat(),
        "period_days": period_days,
        "sponsored_count": sponsored_count,
        "sponsored_unknown_count": sponsored_unknown_count,
        "coverage": {
            "main_images": image_covered / len(rows),
            "mpstats_sales": sales_covered / len(rows),
            "reviews_90d_complete": reviews_covered / len(rows),
        },
        "ranking_basis": ranking_basis,
        "warnings": warnings,
        "top5": top_five,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--in", dest="input_path", type=Path, required=True)
    parser.add_argument("--meta", dest="metadata_path", type=Path, required=True)
    parser.add_argument("--out", dest="output_path", type=Path, required=True)
    args = parser.parse_args()

    if args.output_path.exists():
        raise SystemExit(f"refusing to overwrite existing file: {args.output_path}")

    try:
        result = analyze(load_rows(args.input_path), load_metadata(args.metadata_path))
    except (OSError, DataError) as exc:
        raise SystemExit(f"error: {exc}") from exc

    args.output_path.parent.mkdir(parents=True, exist_ok=True)
    args.output_path.write_text(
        json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "sample_count": result["sample_count"],
                "sample_complete": result["sample_complete"],
                "research_complete": result["research_complete"],
                "ranking_basis": result["ranking_basis"],
                "top5_count": len(result["top5"]),
                "out": str(args.output_path),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
