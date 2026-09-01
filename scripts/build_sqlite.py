#!/usr/bin/env python3
"""Build a resumable local SQLite analytics database from NSE F&O bhavcopies."""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import sqlite3
import sys
import time
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator, Mapping, Optional, Sequence


SCHEMA_VERSION = 1
INSERT_COLUMNS = (
    "source_file_id", "source_row", "trade_date", "business_date", "segment",
    "source", "native_instrument_type", "underlying_kind", "instrument_kind",
    "instrument_id", "isin", "symbol", "series", "expiry_date",
    "actual_expiry_date", "strike_price", "option_type", "instrument_name",
    "open_price", "high_price", "low_price", "close_price", "last_price",
    "previous_close_price", "underlying_price", "settlement_price", "open_interest",
    "change_in_open_interest", "contracts", "traded_quantity", "traded_value_rupees",
    "trade_count", "session_id", "market_lot", "remarks",
)
INSERT_SQL = (
    f"INSERT INTO bhavcopy ({', '.join(INSERT_COLUMNS)}) "
    f"VALUES ({', '.join('?' for _ in INSERT_COLUMNS)})"
)

LEGACY_REQUIRED = {
    "INSTRUMENT", "SYMBOL", "EXPIRY_DT", "STRIKE_PR", "OPTION_TYP", "OPEN",
    "HIGH", "LOW", "CLOSE", "SETTLE_PR", "CONTRACTS", "VAL_INLAKH",
    "OPEN_INT", "CHG_IN_OI", "TIMESTAMP",
}
UDIFF_REQUIRED = {
    "TradDt", "Sgmt", "FinInstrmTp", "TckrSymb", "XpryDt", "StrkPric", "OptnTp",
    "OpnPric", "HghPric", "LwPric", "ClsPric", "SttlmPric", "OpnIntrst",
    "ChngInOpnIntrst", "TtlTradgVol", "TtlTrfVal",
}

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS metadata (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS source_files (
    id INTEGER PRIMARY KEY,
    relative_path TEXT NOT NULL UNIQUE,
    archive_sha256 TEXT NOT NULL,
    archive_bytes INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    source_format TEXT NOT NULL CHECK (source_format IN ('legacy', 'udiff')),
    source_row_count INTEGER NOT NULL,
    row_count INTEGER NOT NULL,
    zero_volume_rows INTEGER NOT NULL,
    imported_at TEXT NOT NULL
) STRICT;

CREATE TABLE IF NOT EXISTS bhavcopy (
    id INTEGER PRIMARY KEY,
    source_file_id INTEGER NOT NULL REFERENCES source_files(id) ON DELETE CASCADE,
    source_row INTEGER NOT NULL,
    trade_date TEXT NOT NULL,
    business_date TEXT,
    segment TEXT NOT NULL,
    source TEXT,
    native_instrument_type TEXT NOT NULL,
    underlying_kind TEXT NOT NULL CHECK (underlying_kind IN ('INDEX', 'STOCK', 'OTHER')),
    instrument_kind TEXT NOT NULL CHECK (instrument_kind IN ('FUTURE', 'OPTION', 'OTHER')),
    instrument_id INTEGER,
    isin TEXT,
    symbol TEXT NOT NULL,
    series TEXT,
    expiry_date TEXT,
    actual_expiry_date TEXT,
    strike_price REAL,
    option_type TEXT,
    instrument_name TEXT,
    open_price REAL,
    high_price REAL,
    low_price REAL,
    close_price REAL,
    last_price REAL,
    previous_close_price REAL,
    underlying_price REAL,
    settlement_price REAL,
    open_interest INTEGER,
    change_in_open_interest INTEGER,
    contracts INTEGER,
    traded_quantity INTEGER,
    traded_value_rupees REAL,
    trade_count INTEGER,
    session_id TEXT,
    market_lot INTEGER,
    remarks TEXT
) STRICT;

CREATE VIEW IF NOT EXISTS options AS
SELECT * FROM bhavcopy WHERE instrument_kind = 'OPTION';

CREATE VIEW IF NOT EXISTS futures AS
SELECT * FROM bhavcopy WHERE instrument_kind = 'FUTURE';
"""

INDEX_SQL = (
    "CREATE INDEX IF NOT EXISTS idx_bhavcopy_trade_date ON bhavcopy(trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_bhavcopy_contract ON bhavcopy("
    "symbol, instrument_kind, expiry_date, option_type, strike_price, trade_date)",
    "CREATE INDEX IF NOT EXISTS idx_bhavcopy_source_file ON bhavcopy(source_file_id)",
)

TYPE_MAP = {
    "FUTIDX": ("INDEX", "FUTURE"),
    "FUTSTK": ("STOCK", "FUTURE"),
    "OPTIDX": ("INDEX", "OPTION"),
    "OPTSTK": ("STOCK", "OPTION"),
    "IDF": ("INDEX", "FUTURE"),
    "STF": ("STOCK", "FUTURE"),
    "IDO": ("INDEX", "OPTION"),
    "STO": ("STOCK", "OPTION"),
}


def text(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    return value or None


def number(value: Optional[str]) -> Optional[float]:
    value = text(value)
    if value is None or value in {"-", "--"}:
        return None
    return float(value.replace(",", ""))


def integer(value: Optional[str]) -> Optional[int]:
    parsed = number(value)
    return None if parsed is None else int(parsed)


def iso_date(value: Optional[str], legacy: bool = False) -> Optional[str]:
    value = text(value)
    if value is None:
        return None
    if not legacy:
        return value[:10]
    return datetime.strptime(value.title(), "%d-%b-%Y").date().isoformat()


def instrument_kinds(native_type: str) -> tuple[str, str]:
    return TYPE_MAP.get(native_type, ("OTHER", "OTHER"))


def legacy_values(row: Mapping[str, str], file_id: int, row_number: int) -> tuple:
    native = text(row.get("INSTRUMENT")) or "UNKNOWN"
    underlying_kind, instrument_kind = instrument_kinds(native)
    trade_value_lakh = number(row.get("VAL_INLAKH"))
    return (
        file_id, row_number, iso_date(row.get("TIMESTAMP"), True), None, "FO", "NSE",
        native, underlying_kind, instrument_kind, None, None,
        text(row.get("SYMBOL")) or "UNKNOWN", None, iso_date(row.get("EXPIRY_DT"), True),
        None, number(row.get("STRIKE_PR")), text(row.get("OPTION_TYP")), None,
        number(row.get("OPEN")), number(row.get("HIGH")), number(row.get("LOW")),
        number(row.get("CLOSE")), None, None, None, number(row.get("SETTLE_PR")),
        integer(row.get("OPEN_INT")), integer(row.get("CHG_IN_OI")),
        integer(row.get("CONTRACTS")), None,
        None if trade_value_lakh is None else trade_value_lakh * 100_000,
        None, None, None, None,
    )


def udiff_values(row: Mapping[str, str], file_id: int, row_number: int) -> tuple:
    native = text(row.get("FinInstrmTp")) or "UNKNOWN"
    underlying_kind, instrument_kind = instrument_kinds(native)
    return (
        file_id, row_number, iso_date(row.get("TradDt")), iso_date(row.get("BizDt")),
        text(row.get("Sgmt")) or "FO", text(row.get("Src")), native, underlying_kind,
        instrument_kind, integer(row.get("FinInstrmId")), text(row.get("ISIN")),
        text(row.get("TckrSymb")) or "UNKNOWN", text(row.get("SctySrs")),
        iso_date(row.get("XpryDt")), iso_date(row.get("FininstrmActlXpryDt")),
        number(row.get("StrkPric")), text(row.get("OptnTp")), text(row.get("FinInstrmNm")),
        number(row.get("OpnPric")), number(row.get("HghPric")), number(row.get("LwPric")),
        number(row.get("ClsPric")), number(row.get("LastPric")), number(row.get("PrvsClsgPric")),
        number(row.get("UndrlygPric")), number(row.get("SttlmPric")),
        integer(row.get("OpnIntrst")), integer(row.get("ChngInOpnIntrst")), None,
        integer(row.get("TtlTradgVol")), number(row.get("TtlTrfVal")),
        integer(row.get("TtlNbOfTxsExctd")), text(row.get("SsnId")),
        integer(row.get("NewBrdLotQty")), text(row.get("Rmks")),
    )


def archive_rows(archive: bytes, file_id: int) -> tuple[str, Iterator[tuple], dict[str, int]]:
    zipped = zipfile.ZipFile(io.BytesIO(archive))
    members = [name for name in zipped.namelist() if name.lower().endswith(".csv")]
    if len(members) != 1:
        raise ValueError(f"expected exactly one CSV member, found {len(members)}")
    stream = io.TextIOWrapper(zipped.open(members[0]), encoding="utf-8-sig", newline="")
    reader = csv.DictReader(stream)
    headers = set(reader.fieldnames or ())
    if LEGACY_REQUIRED <= headers:
        source_format, converter = "legacy", legacy_values
    elif UDIFF_REQUIRED <= headers:
        source_format, converter = "udiff", udiff_values
    else:
        raise ValueError("CSV does not match the supported legacy or UDiFF schema")

    stats = {"source_rows": 0, "zero_volume_rows": 0}
    volume_index = INSERT_COLUMNS.index("contracts" if source_format == "legacy" else "traded_quantity")

    def rows() -> Iterator[tuple]:
        expected_date: Optional[str] = None
        try:
            for row_number, row in enumerate(reader, 1):
                stats["source_rows"] += 1
                values = converter(row, file_id, row_number)
                trade_date = values[2]
                if not trade_date:
                    raise ValueError(f"row {row_number} has no trade date")
                if expected_date is None:
                    expected_date = trade_date
                elif trade_date != expected_date:
                    raise ValueError(f"row {row_number} trade date differs from the archive")
                volume = values[volume_index]
                if volume is None or volume <= 0:
                    stats["zero_volume_rows"] += 1
                    continue
                yield values
        finally:
            stream.close()
            zipped.close()

    return source_format, rows(), stats


def batched(rows: Iterable[tuple], size: int = 5_000) -> Iterator[list[tuple]]:
    batch: list[tuple] = []
    for row in rows:
        batch.append(row)
        if len(batch) >= size:
            yield batch
            batch = []
    if batch:
        yield batch


def connect(database: Path) -> sqlite3.Connection:
    connection = sqlite3.connect(database, timeout=120)
    connection.execute("PRAGMA foreign_keys = ON")
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = NORMAL")
    connection.execute("PRAGMA temp_store = MEMORY")
    connection.execute("PRAGMA cache_size = -262144")
    connection.executescript(CREATE_SQL)
    connection.execute(
        "INSERT INTO metadata(key, value) VALUES('schema_version', ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (str(SCHEMA_VERSION),),
    )
    connection.commit()
    return connection


def import_archive(connection: sqlite3.Connection, data_root: Path, archive_path: Path) -> tuple[str, int]:
    relative_path = archive_path.relative_to(data_root.parent).as_posix()
    archive = archive_path.read_bytes()
    digest = hashlib.sha256(archive).hexdigest()
    existing = connection.execute(
        "SELECT id, archive_sha256, row_count FROM source_files WHERE relative_path = ?",
        (relative_path,),
    ).fetchone()
    if existing and existing[1] == digest:
        return "skipped", existing[2]

    now = datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        connection.execute("BEGIN")
        if existing:
            file_id = existing[0]
            connection.execute("DELETE FROM bhavcopy WHERE source_file_id = ?", (file_id,))
            connection.execute("DELETE FROM source_files WHERE id = ?", (file_id,))
        cursor = connection.execute(
            "INSERT INTO source_files(relative_path, archive_sha256, archive_bytes, trade_date, "
            "source_format, source_row_count, row_count, zero_volume_rows, imported_at) "
            "VALUES(?, ?, ?, '', 'legacy', 0, 0, 0, ?)",
            (relative_path, digest, len(archive), now),
        )
        file_id = cursor.lastrowid
        source_format, rows, stats = archive_rows(archive, file_id)
        row_count = 0
        trade_date: Optional[str] = None
        for batch in batched(rows):
            if trade_date is None:
                trade_date = batch[0][2]
            connection.executemany(INSERT_SQL, batch)
            row_count += len(batch)
        if not row_count or not trade_date:
            raise ValueError("CSV contains no data rows")
        connection.execute(
            "UPDATE source_files SET trade_date=?, source_format=?, source_row_count=?, "
            "row_count=?, zero_volume_rows=? WHERE id=?",
            (
                trade_date, source_format, stats["source_rows"], row_count,
                stats["zero_volume_rows"], file_id,
            ),
        )
        connection.commit()
        return "updated" if existing else "imported", row_count
    except Exception:
        connection.rollback()
        raise


def create_indexes(connection: sqlite3.Connection) -> None:
    for statement in INDEX_SQL:
        connection.execute(statement)
    connection.execute("ANALYZE")
    connection.execute("PRAGMA optimize")
    connection.commit()


def print_status(connection: sqlite3.Connection, database: Path) -> None:
    files, source_rows, rows, filtered, first, last = connection.execute(
        "SELECT COUNT(*), COALESCE(SUM(source_row_count), 0), COALESCE(SUM(row_count), 0), "
        "COALESCE(SUM(zero_volume_rows), 0), MIN(trade_date), MAX(trade_date) FROM source_files"
    ).fetchone()
    size = database.stat().st_size if database.exists() else 0
    print(f"Database: {database}")
    print(f"Archives: {files:,}")
    print(f"Active rows retained: {rows:,}")
    print(f"Zero-volume rows excluded: {filtered:,} of {source_rows:,}")
    print(f"Coverage: {first or '-'} through {last or '-'}")
    print(f"Size: {size / (1024 ** 3):.2f} GiB")


def parse_args(argv: Optional[Sequence[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--data", type=Path, default=Path("data"), help="bhavcopy data root")
    parser.add_argument("--db", type=Path, default=Path("nse_fno.db"), help="SQLite output path")
    parser.add_argument("--status", action="store_true", help="show database coverage without importing")
    parser.add_argument("--no-indexes", action="store_true", help="skip analytics index creation")
    parser.add_argument("--limit", type=int, help="import at most this many archives (for testing)")
    parser.add_argument("--verbose", action="store_true", help="print every imported archive")
    return parser.parse_args(argv)


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = parse_args(argv)
    if args.status and not args.db.exists():
        print(f"Database does not exist: {args.db}", file=sys.stderr)
        return 1
    args.db.parent.mkdir(parents=True, exist_ok=True)
    connection = connect(args.db)
    try:
        if args.status:
            print_status(connection, args.db)
            return 0
        files = sorted(args.data.rglob("*.zip"))
        if args.limit is not None:
            files = files[: args.limit]
        if not files:
            raise SystemExit(f"No ZIP archives found under {args.data}")
        started = time.monotonic()
        imported = skipped = total_rows = 0
        for position, archive_path in enumerate(files, 1):
            action, rows = import_archive(connection, args.data, archive_path)
            total_rows += rows
            if action == "skipped":
                skipped += 1
            else:
                imported += 1
            if args.verbose or position % 25 == 0 or position == len(files):
                elapsed = time.monotonic() - started
                print(
                    f"[{position:,}/{len(files):,}] {action}: {archive_path} "
                    f"({rows:,} rows, {elapsed:.0f}s)",
                    flush=True,
                )
        if not args.no_indexes:
            print("Creating/refreshing analytics indexes...", flush=True)
            create_indexes(connection)
        print(f"Processed {len(files):,} archives: {imported:,} changed, {skipped:,} unchanged.")
        print_status(connection, args.db)
        return 0
    finally:
        connection.close()


if __name__ == "__main__":
    raise SystemExit(main())
