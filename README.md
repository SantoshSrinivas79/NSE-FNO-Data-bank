[![Get F&O Bhavcopy](https://github.com/SantoshSrinivas79/NSE-FNO-Data-bank/actions/workflows/autoDownload.yml/badge.svg)](https://github.com/SantoshSrinivas79/NSE-FNO-Data-bank/actions/workflows/autoDownload.yml)

# NSE F&O Data Bank

This repository downloads, validates, and stores the National Stock Exchange of India equity-derivatives (F&O) bhavcopy. A GitHub Action checks for a new report every NSE weekday and commits only a ZIP whose CSV structure, segment, and trade date are valid.

## Data source and formats

NSE changed the official F&O bhavcopy on 8 July 2024:

- Through 5 July 2024: `https://archives.nseindia.com/content/historical/DERIVATIVES/YYYY/MON/foDDMONYYYYbhav.csv.zip`
- From 8 July 2024: `https://archives.nseindia.com/content/fo/BhavCopy_NSE_FO_0_0_0_YYYYMMDD_F_0000.csv.zip`

The downloader falls back to NSE's `nsearchives.nseindia.com` host if the primary archive host is unavailable.

The new format is NSE's **F&O-UDiFF Common Bhavcopy Final**, which replaced the discontinued legacy CSV. See NSE's [official derivatives reports page](https://www.nseindia.com/all-reports-derivatives).

Files are kept compressed under `data/YYYY/MM/`. The repository stores the original NSE ZIP without transforming market values.

## Download

Node.js 20 or newer is required. There are no package dependencies.

```bash
# Today in Asia/Kolkata
npm run download

# One date
node index.js --date 2026-08-31

# Inclusive historical backfill
node index.js --from 2024-07-01 --to 2024-07-10
```

Weekends and dates for which NSE returns no file are skipped. Downloads are retried, written atomically, and accepted only after ZIP and CSV validation.

## Validation

```bash
npm test
```

For UDiFF, the downloader checks the requested trade date, `FO` segment, and core contract/OHLC/volume/open-interest columns. For legacy files, it checks the legacy schema and `TIMESTAMP`.

## Data notice

The MIT license covers the code in this repository. NSE data remains subject to NSE's applicable terms, policies, and rights. This project is not affiliated with or endorsed by NSE.
