"""Fetch the SBI forex rates PDF, parse it, and upsert into sbi_rates.db.

Usage:
    uv run python scraper.py              # fetch live PDF from SBI and update the db
    uv run python scraper.py some.pdf     # parse a local PDF (useful for testing)

The parser is deliberately defensive: SBI's PDFs vary in title, page count,
"LACS"/"LAKHS" wording, and occasionally gain or lose table columns. We anchor
on the currency/ticker cell rather than trusting fixed column positions.
"""

import datetime
import logging
import re
import sqlite3
import sys
import tempfile
import time
from pathlib import Path
from typing import Optional

import pdfplumber
import requests

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
log = logging.getLogger(__name__)

DB_PATH = Path(__file__).parent / "sbi_rates.db"
PRIMARY_URL = "https://www.sbi.co.in/documents/16012/1400784/FOREX_CARD_RATES.pdf"
FALLBACK_URL = "https://bank.sbi/documents/16012/1400784/FOREX_CARD_RATES.pdf"
HEADERS = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

# The 8 rate columns, in the order they appear after the ticker cell.
RATE_FIELDS = (
    "tt_buy", "tt_sell", "bill_buy", "bill_sell",
    "ftc_buy", "ftc_sell", "cn_buy", "cn_sell",
)

DATE_RE = re.compile(r"(\d{2})[-/](\d{2})[-/](\d{4})")


# --------------------------------------------------------------------------- #
# Database
# --------------------------------------------------------------------------- #
def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS forex_rates (
            id        INTEGER PRIMARY KEY AUTOINCREMENT,
            currency  TEXT NOT NULL,
            ticker    TEXT NOT NULL,
            tt_buy    REAL,
            tt_sell   REAL,
            bill_buy  REAL,
            bill_sell REAL,
            ftc_buy   REAL,
            ftc_sell  REAL,
            cn_buy    REAL,
            cn_sell   REAL,
            date      TEXT NOT NULL,
            category  TEXT NOT NULL,
            UNIQUE(ticker, date, category)
        )
        """
    )
    conn.execute(
        "CREATE INDEX IF NOT EXISTS ix_forex_rates_date ON forex_rates(date)"
    )
    conn.commit()


def upsert(conn: sqlite3.Connection, records: list[dict]) -> int:
    """Idempotent upsert keyed on (ticker, date, category).

    Re-running with the same PDF leaves the row content identical, so the
    scraper is safe to run repeatedly (it runs 4x/day).
    """
    conn.executemany(
        """
        INSERT INTO forex_rates
            (currency, ticker, tt_buy, tt_sell, bill_buy, bill_sell,
             ftc_buy, ftc_sell, cn_buy, cn_sell, date, category)
        VALUES
            (:currency, :ticker, :tt_buy, :tt_sell, :bill_buy, :bill_sell,
             :ftc_buy, :ftc_sell, :cn_buy, :cn_sell, :date, :category)
        ON CONFLICT(ticker, date, category) DO UPDATE SET
            currency  = excluded.currency,
            tt_buy    = excluded.tt_buy,
            tt_sell   = excluded.tt_sell,
            bill_buy  = excluded.bill_buy,
            bill_sell = excluded.bill_sell,
            ftc_buy   = excluded.ftc_buy,
            ftc_sell  = excluded.ftc_sell,
            cn_buy    = excluded.cn_buy,
            cn_sell   = excluded.cn_sell
        """,
        records,
    )
    conn.commit()
    return len(records)


# --------------------------------------------------------------------------- #
# Parsing helpers
# --------------------------------------------------------------------------- #
def parse_date(text: str) -> Optional[datetime.date]:
    """Pull a DD-MM-YYYY (or DD/MM/YYYY) date out of a blob of text."""
    m = DATE_RE.search(text or "")
    if not m:
        return None
    try:
        day, month, year = map(int, m.groups())
        return datetime.date(year, month, day)
    except ValueError:
        return None


def categorize(*texts: Optional[str]) -> Optional[str]:
    """Classify a page into a transaction category from any of the given texts.

    Handles the "LACS"/"LAKHS" wording drift and the BELOW vs BETWEEN split.
    Returns None when no marker is present (e.g. a blank/continuation page).
    """
    blob = " ".join(t for t in texts if t).upper()
    if "BELOW" in blob:
        return "BELOW_10"
    if "BETWEEN" in blob:
        return "BETWEEN_10_20"
    return None


def to_float(value) -> Optional[float]:
    if value is None:
        return None
    s = str(value).strip()
    if s in ("", "-", "NA", "N/A", "0", "0.00", "0.0"):
        return None
    try:
        return float(s)
    except ValueError:
        return None


def _find_ticker_index(row: list) -> Optional[int]:
    """Locate the cell holding the currency pair, e.g. 'USD/INR'."""
    for i, cell in enumerate(row):
        if not cell:
            continue
        parts = str(cell).split("/")
        if len(parts) == 2:
            code = parts[0].strip()
            if code.isalpha() and 2 <= len(code) <= 5:
                return i
    return None


def parse_row(row: list) -> Optional[dict]:
    """Parse a currency row by anchoring on the ticker cell.

    Column positions drift (extra leading blanks, a trailing 'PC BUY', etc.),
    so we take the currency name from everything before the ticker and the 8
    rate values from the 8 cells immediately after it. Missing/extra trailing
    columns degrade to None rather than shifting the whole row.
    """
    idx = _find_ticker_index(row)
    if idx is None:
        return None

    ticker = str(row[idx]).split("/")[0].strip().upper()
    currency = " ".join(
        str(row[j]).replace("\n", " ").strip()
        for j in range(idx)
        if row[j]
    ).strip()
    if not currency or not ticker:
        return None

    tail = row[idx + 1:]
    rec: dict[str, object] = {"currency": currency, "ticker": ticker}
    for i, field in enumerate(RATE_FIELDS):
        rec[field] = to_float(tail[i]) if i < len(tail) else None
    return rec


def parse_pdf(
    path: str, fallback_date: Optional[datetime.date] = None
) -> tuple[Optional[datetime.date], list[dict]]:
    """Extract (date, records) from a PDF.

    The date comes from the PDF text; `fallback_date` (e.g. derived from a
    filename) is used when the PDF has no extractable date.
    """
    records: list[dict] = []
    pdf_date = fallback_date

    with pdfplumber.open(path) as pdf:
        # Date lives on the first page; prefer the PDF's own text.
        first_text = pdf.pages[0].extract_text() or ""
        found = parse_date(first_text)
        if found:
            pdf_date = found
        if pdf_date is None:
            log.error("No date found in PDF and no fallback given: %s", path)
            return None, []

        for i, page in enumerate(pdf.pages):
            text = page.extract_text() or ""
            table = page.extract_table()
            if not table:
                continue

            # Category may live in page text, or (for text-less PDFs) in cells.
            cell_blob = " ".join(
                str(c) for r in table[:4] for c in r if c
            )
            category = categorize(text, cell_blob)
            if category is None:
                log.warning("Page %d of %s: no category marker, skipping",
                            i + 1, path)
                continue

            for row in table:
                rec = parse_row(row)
                if rec is None:
                    continue
                rec["date"] = pdf_date.isoformat()
                rec["category"] = category
                records.append(rec)

    # Dedup on the unique key (two-column visual layouts duplicate rows).
    # Never silently discard conflicting duplicates: that indicates extraction
    # drift and must fail before potentially corrupting an existing DB row.
    seen: dict[tuple[str, str, str], dict] = {}
    deduped: list[dict] = []
    for r in records:
        key = (r["ticker"], r["date"], r["category"])
        previous = seen.get(key)
        if previous is None:
            seen[key] = r
            deduped.append(r)
        elif previous != r:
            raise ValueError(
                f"Conflicting duplicate row for {key} while parsing {path}"
            )

    log.info("Parsed %d records (%d after dedup) for %s",
             len(records), len(deduped), pdf_date)
    return pdf_date, deduped


# --------------------------------------------------------------------------- #
# Download
# --------------------------------------------------------------------------- #
def download_pdf(retries: int = 3, backoff: float = 2.0) -> Optional[bytes]:
    """Try both URLs a few times. SBI's servers time out often, so we retry
    with backoff and simply give up (returning None) rather than raising."""
    for attempt in range(1, retries + 1):
        for url in (PRIMARY_URL, FALLBACK_URL):
            try:
                r = requests.get(url, headers=HEADERS, timeout=30)
                r.raise_for_status()
                if r.content.startswith(b"%PDF"):
                    log.info("Downloaded PDF from %s (%d bytes)", url, len(r.content))
                    return r.content
                log.warning("Content from %s was not a PDF", url)
            except requests.RequestException as e:
                log.warning("Attempt %d: failed to fetch %s: %s", attempt, url, e)
        if attempt < retries:
            time.sleep(backoff * attempt)
    return None


# --------------------------------------------------------------------------- #
# Entrypoints
# --------------------------------------------------------------------------- #
def process(pdf_path: str, fallback_date: Optional[datetime.date] = None) -> int:
    """Parse a PDF and upsert its records. Returns the number of rows written."""
    with sqlite3.connect(DB_PATH) as conn:
        init_db(conn)
        _, records = parse_pdf(pdf_path, fallback_date=fallback_date)
        if not records:
            return 0
        return upsert(conn, records)


def main() -> int:
    if len(sys.argv) > 1:
        n = process(sys.argv[1])
        if n == 0:
            log.error("No records parsed from %s", sys.argv[1])
            return 1
        log.info("Wrote %d records to %s", n, DB_PATH)
        return 0

    pdf_bytes = download_pdf()
    if not pdf_bytes:
        # Transient upstream outage. Runs 4x/day, so a miss is fine — don't fail.
        log.warning("Could not download PDF (upstream timeout?). No-op this run.")
        return 0

    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
        f.write(pdf_bytes)
        tmp_path = f.name
    try:
        n = process(tmp_path)
        if n == 0:
            log.error("Downloaded a PDF but parsed no records.")
            return 1
        log.info("Wrote %d records to %s", n, DB_PATH)
        return 0
    finally:
        Path(tmp_path).unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main())
