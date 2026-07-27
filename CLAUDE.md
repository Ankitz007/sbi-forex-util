# CLAUDE.md

## What this repo is

**Repo A** in a two-repo design. Its only job: fetch SBI's daily forex-rates
PDF, parse it, and commit `sbi_rates.db` (SQLite) back to this repo. A separate
React app (**Repo B**) reads `sbi_rates.db` directly over HTTP via
`sql.js-httpvfs` — there is **no server, no hosted database, no Vercel**. Do not
reintroduce any of those.

The committed `sbi_rates.db` *is* the API.

## Layout

- `scraper.py` — the entire production logic. Download → parse → upsert. One file.
- `scripts/backfill.py` — one-off helper to bulk-load the historical archive in
  `data/pdf_files/`. Keep bulk/archive logic HERE, never in `scraper.py`.
- `.github/workflows/fetch-forex-rates.yml` — runs `scraper.py` 4×/day, commits the db.
- `data/` — historical PDF archive (~143M, gitignored). Source for backfills only.
- `test-1.pdf`, `test-2.pdf` — committed sample PDFs for quick parser checks.

## Running

Uses [uv](https://docs.astral.sh/uv/) (not pip). No `requirements.txt`.

```bash
uv sync
uv run python scraper.py test-1.pdf   # parse a local PDF
uv run python scraper.py              # fetch live + update db
uv run python scripts/backfill.py 2024 2025   # bulk load archive years
```

## Parser invariants (learned from the real PDFs — don't regress these)

SBI's PDFs are inconsistent. The parser must tolerate all of the following,
seen across the 752-PDF archive:

- **Title drift**: "SBI FOREX CARD RATES" vs "STATE BANK OF INDIA - FOREX CARD
  RATES". Never key on the title.
- **Wording drift**: "LACS" vs "LAKHS", varied spacing. Category detection keys
  only on the substrings `BELOW` / `BETWEEN`.
- **Two categories**: `BELOW_10` and `BETWEEN_10_20`. Older PDFs put them on
  separate pages (2+ pages); newer ones may have one. Iterate **all** pages and
  categorize each. Keep both categories.
- **Column drift**: rows may have a trailing extra column (e.g. "PC BUY"), so the
  parser anchors on the ticker cell (`XXX/INR`): currency = cells before it, the
  8 rate values = the 8 cells after it. Never hard-code column indices.
- **Duplicated rows**: two-column visual layouts make pdfplumber emit each row
  twice. Dedup on `(ticker, date, category)`.
- **Image-only PDFs**: a few (e.g. 2024-09-06/07/08) have no extractable text —
  skip gracefully, don't crash. `backfill.py` passes the filename date as a
  fallback for PDFs whose text lacks a date.

## Behavioural rules

- **Never fail on upstream timeout.** SBI's URLs time out often; the cron runs
  4×/day precisely so a miss is harmless. `scraper.py main()` returns 0 (no-op)
  when the download fails after retries. Only genuine parse/DB errors return 1.
- **Upserts are idempotent.** `ON CONFLICT(ticker, date, category) DO UPDATE`.
  Re-running the same PDF (or the whole backfill) never duplicates rows.
- `sbi_rates.db` is committed; `data/pdf_files/` is not.
