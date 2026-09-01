# Local SQLite analytics

`scripts/build_sqlite.py` creates `nse_fno.db`, a normalized and indexed local query layer over the original daily NSE archives. The database is reproducible, resumable, and intentionally excluded from Git.

## Build and update

```bash
# Import every archive and create indexes
npm run db:build

# Show coverage, row counts, filtering, and database size
npm run db:status

# After downloading another day, run the same build command again.
# Unchanged archives are recognized by SHA-256 and skipped.
npm run download
npm run db:build
```

Each source archive is committed in its own SQLite transaction. An interrupted build can therefore resume safely. `source_files` records the archive path, SHA-256, source and retained row counts, zero-volume exclusions, format, and import time.

To build another database or omit indexes during a bulk experiment:

```bash
python3 scripts/build_sqlite.py --db scratch.sqlite
python3 scripts/build_sqlite.py --db scratch.sqlite --no-indexes
```

## Activity filter

The analytics table retains rows with executed activity:

- Legacy bhavcopy: `CONTRACTS > 0`
- UDiFF bhavcopy: `TtlTradgVol > 0`

Rows are not filtered on price, open interest, or traded value. The raw ZIPs remain the complete source of truth, including zero-volume listings.

## Tables and views

- `bhavcopy`: normalized active F&O observations.
- `source_files`: import manifest and audit counts.
- `options`: view over `bhavcopy` where `instrument_kind = 'OPTION'`.
- `futures`: view over `bhavcopy` where `instrument_kind = 'FUTURE'`.
- `metadata`: database schema version.

Common normalized fields include `trade_date`, `symbol`, `underlying_kind`, `instrument_kind`, `expiry_date`, `strike_price`, `option_type`, OHLC and settlement prices, open interest, and traded value in rupees.

### Volume semantics

The two NSE formats do not expose the same activity unit:

- `contracts` contains legacy `CONTRACTS` and is null for UDiFF rows.
- `traded_quantity` contains UDiFF `TtlTradgVol` and is null for legacy rows.
- `traded_value_rupees` converts legacy `VAL_INLAKH` to rupees and uses UDiFF `TtlTrfVal` directly.

Keeping contract counts and traded quantity separate prevents invalid comparisons across the 8 July 2024 format change.

## Example queries

Open the database:

```bash
sqlite3 -header -column nse_fno.db
```

Most-active NIFTY options on a date:

```sql
SELECT expiry_date, strike_price, option_type, close_price, traded_quantity
FROM options
WHERE trade_date = '2026-08-31' AND symbol = 'NIFTY'
ORDER BY traded_quantity DESC
LIMIT 20;
```

History for one option contract:

```sql
SELECT trade_date, close_price, settlement_price, open_interest,
       contracts, traded_quantity
FROM options
WHERE symbol = 'NIFTY'
  AND expiry_date = '2026-09-01'
  AND option_type = 'CE'
  AND strike_price = 25000
ORDER BY trade_date;
```

Daily futures activity:

```sql
SELECT trade_date, symbol, expiry_date, close_price, open_interest,
       contracts, traded_quantity
FROM futures
WHERE trade_date = '2026-08-31'
ORDER BY COALESCE(traded_quantity, contracts) DESC
LIMIT 20;
```

Audit the filter and source coverage:

```sql
SELECT COUNT(*) AS archives,
       SUM(source_row_count) AS source_rows,
       SUM(row_count) AS retained_rows,
       SUM(zero_volume_rows) AS excluded_rows,
       MIN(trade_date) AS first_date,
       MAX(trade_date) AS last_date
FROM source_files;
```

## Indexes

The importer creates indexes for date scans, full contract history, and source-file maintenance. The contract index begins with `symbol`, followed by instrument kind, expiry, option type, strike, and trade date. This makes common single-contract history queries effectively immediate on the full local dataset.
