"""One-off helper: backfill historical rates from data/pdf_files/<year>/ into the db.

This is intentionally kept OUT of scraper.py — the scraper only ever handles the
single latest PDF. This script reuses scraper's parsing/upsert functions to bulk
load an archive.

Usage:
    uv run python scripts/backfill.py            # backfill every year in the archive
    uv run python scripts/backfill.py 2024 2025  # backfill specific years
"""

import datetime
import sqlite3
import sys
from concurrent.futures import ProcessPoolExecutor
from pathlib import Path

# Make the repo root importable so we can reuse scraper.py.
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import scraper  # noqa: E402

DATA_DIR = ROOT / "data" / "pdf_files"


def date_from_filename(path: Path) -> datetime.date | None:
    """Filenames look like 2024-09-06.pdf — used as a date fallback for PDFs
    whose text has no extractable date (e.g. scanned/image-only pages)."""
    try:
        return datetime.date.fromisoformat(path.stem)
    except ValueError:
        return None


def parse_one(path_str: str) -> list[dict]:
    """Worker: parse a single PDF into records (runs in a subprocess)."""
    import logging

    scraper.log.setLevel(logging.WARNING)  # quiet per-PDF INFO noise
    path = Path(path_str)
    try:
        _, records = scraper.parse_pdf(str(path), fallback_date=date_from_filename(path))
        return records
    except Exception as e:  # keep the batch going if one PDF is malformed
        print(f"ERROR parsing {path}: {e}", file=sys.stderr)
        return []


def backfill(years: list[str]) -> None:
    pdfs: list[Path] = []
    for year in years:
        year_dir = DATA_DIR / year
        if not year_dir.is_dir():
            print(f"WARNING: {year_dir} not found, skipping")
            continue
        pdfs.extend(sorted(year_dir.rglob("*.pdf")))

    if not pdfs:
        print("No PDFs found.")
        return

    print(f"Backfilling {len(pdfs)} PDFs from years {years}...")

    with sqlite3.connect(scraper.DB_PATH) as conn:
        scraper.init_db(conn)

        total_records = 0
        parsed_ok = 0
        empty = []

        # Parse in parallel (CPU-bound), write serially (SQLite single-writer).
        with ProcessPoolExecutor() as pool:
            for path, records in zip(pdfs, pool.map(parse_one, [str(p) for p in pdfs])):
                if records:
                    scraper.upsert(conn, records)
                    total_records += len(records)
                    parsed_ok += 1
                else:
                    empty.append(path)

    print(f"\nDone. {parsed_ok}/{len(pdfs)} PDFs parsed, {total_records} rows upserted.")
    if empty:
        print(f"{len(empty)} PDFs yielded no records:")
        for p in empty:
            print(f"  - {p.relative_to(ROOT)}")


if __name__ == "__main__":
    # Default: every year present in the archive.
    requested = sys.argv[1:] or sorted(
        p.name for p in DATA_DIR.iterdir() if p.is_dir()
    )
    backfill(requested)
