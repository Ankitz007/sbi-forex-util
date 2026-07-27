# SBI Forex Rates — Scraper (Repo A)

A service that fetches the SBI forex rates PDF four times a day, parses it with `pdfplumber`, and commits the updated `sbi_rates.db` SQLite database back to this repository.

Another repo reads `sbi_rates.db` directly over HTTP using [sql.js-httpvfs](https://github.com/phiresky/sql.js-httpvfs) — no server required.

## How it works

1. GitHub Action runs on `cron: "30 0,6,12,18 * * *"` (4× daily, UTC)
2. `scraper.py` downloads the PDF from SBI's servers (primary + fallback URL)
3. Parses the date and rate table using `pdfplumber`
4. Upserts records into `sbi_rates.db` via stdlib `sqlite3`
5. Action commits and pushes the updated database file

## Local development

Requires [uv](https://docs.astral.sh/uv/).

```bash
# install dependencies
uv sync

# test against a local PDF
uv run python scraper.py test-1.pdf

# fetch live from SBI and update the db
uv run python scraper.py
```

### Backfilling historical data

The `data/pdf_files/` archive (gitignored) holds historical PDFs. To bulk-load
them into the database:

```bash
uv run python scripts/backfill.py 2024        # one year
uv run python scripts/backfill.py 2024 2025   # several years
```

This is a one-off helper — the scraper itself only ever handles the latest PDF.
Both the `BELOW_10` and `BETWEEN_10_20` transaction-range categories are stored.

## Database schema

```sql
CREATE TABLE forex_rates (
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
    date      TEXT NOT NULL,       -- ISO 8601: YYYY-MM-DD
    category  TEXT NOT NULL,       -- BELOW_10 | BETWEEN_10_20
    UNIQUE(ticker, date, category)
);
```
