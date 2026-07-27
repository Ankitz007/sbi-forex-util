"""Print a Markdown health and change summary for sbi_rates.db."""

import argparse
import os
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "sbi_rates.db"
KEY_FIELDS = ("ticker", "date", "category")
VALUE_FIELDS = (
    "currency",
    "tt_buy",
    "tt_sell",
    "bill_buy",
    "bill_sell",
    "ftc_buy",
    "ftc_sell",
    "cn_buy",
    "cn_sell",
)


def load_records(path: Path) -> dict[tuple, tuple]:
    fields = KEY_FIELDS + VALUE_FIELDS
    with sqlite3.connect(path) as conn:
        rows = conn.execute(f"SELECT {', '.join(fields)} FROM forex_rates")
        return {
            tuple(row[: len(KEY_FIELDS)]): tuple(row[len(KEY_FIELDS) :]) for row in rows
        }


def database_changes(before: Path | None, current: Path) -> tuple[int, int, int]:
    if before is None or not before.exists():
        return 0, 0, 0
    old = load_records(before)
    new = load_records(current)
    common = old.keys() & new.keys()
    return (
        len(new.keys() - old.keys()),
        sum(old[key] != new[key] for key in common),
        len(old.keys() - new.keys()),
    )


def build_summary(
    db_path: Path,
    before_path: Path | None = None,
    fetch_outcome: str = "unknown",
) -> str:
    added, updated, removed = database_changes(before_path, db_path)
    changed = added + updated + removed

    with sqlite3.connect(db_path) as conn:
        integrity = conn.execute("PRAGMA integrity_check").fetchone()[0]
        total, dates, first_date, latest_date = conn.execute(
            "SELECT COUNT(*), COUNT(DISTINCT date), MIN(date), MAX(date) "
            "FROM forex_rates"
        ).fetchone()
        tickers = conn.execute(
            "SELECT COUNT(DISTINCT ticker) FROM forex_rates"
        ).fetchone()[0]
        duplicate_keys = conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT 1 FROM forex_rates GROUP BY ticker, date, category "
            "HAVING COUNT(*) > 1)"
        ).fetchone()[0]
        categories = conn.execute(
            "SELECT category, COUNT(*), COUNT(DISTINCT date) "
            "FROM forex_rates GROUP BY category ORDER BY category"
        ).fetchall()
        latest_categories = conn.execute(
            "SELECT category, COUNT(*) FROM forex_rates WHERE date = ? "
            "GROUP BY category ORDER BY category",
            (latest_date,),
        ).fetchall()

    outcome_icon = {"success": "✅", "failure": "❌"}.get(fetch_outcome, "ℹ️")
    integrity_icon = "✅" if integrity == "ok" else "❌"
    update_text = "Updated" if changed else "No data change"
    size_mib = db_path.stat().st_size / 1024 / 1024
    latest_rows = sum(count for _, count in latest_categories)

    lines = [
        "## SBI Forex Database Summary",
        "",
        "| Run | Result |",
        "|---|---:|",
        f"| Scraper | {outcome_icon} {fetch_outcome.title()} |",
        f"| Database | **{update_text}** |",
        f"| Integrity | {integrity_icon} `{integrity}` |",
        "",
        "### Changes in this run",
        "",
        "| Added | Updated | Removed |",
        "|---:|---:|---:|",
        f"| {added:,} | {updated:,} | {removed:,} |",
        "",
        "### Current database",
        "",
        "| Metric | Value |",
        "|---|---:|",
        f"| Rows | {total:,} |",
        f"| Effective dates | {dates:,} |",
        f"| Date range | {first_date} → {latest_date} |",
        f"| Currencies | {tickers:,} |",
        f"| Size | {size_mib:.2f} MiB |",
        f"| Duplicate keys | {duplicate_keys:,} |",
        "",
        f"### Latest effective date: {latest_date}",
        "",
        f"**{latest_rows:,} rows** across {len(latest_categories)} category/categories.",
        "",
        "| Category | Latest rows | Total rows | Covered dates |",
        "|---|---:|---:|---:|",
    ]
    latest_by_category = dict(latest_categories)
    for category, category_rows, category_dates in categories:
        lines.append(
            f"| `{category}` | {latest_by_category.get(category, 0):,} "
            f"| {category_rows:,} | {category_dates:,} |"
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--before", type=Path)
    parser.add_argument(
        "--fetch-outcome", default=os.environ.get("FETCH_OUTCOME", "unknown")
    )
    args = parser.parse_args()
    print(build_summary(args.db, args.before, args.fetch_outcome), end="")


if __name__ == "__main__":
    main()
