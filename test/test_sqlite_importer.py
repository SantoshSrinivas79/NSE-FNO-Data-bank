import csv
import io
import tempfile
import unittest
import zipfile
from pathlib import Path

from scripts import build_sqlite


LEGACY_HEADER = [
    "INSTRUMENT", "SYMBOL", "EXPIRY_DT", "STRIKE_PR", "OPTION_TYP", "OPEN",
    "HIGH", "LOW", "CLOSE", "SETTLE_PR", "CONTRACTS", "VAL_INLAKH",
    "OPEN_INT", "CHG_IN_OI", "TIMESTAMP",
]


def row_as_dict(values):
    return dict(zip(build_sqlite.INSERT_COLUMNS, values))


class SqliteImporterTests(unittest.TestCase):
    def test_normalizes_legacy_values_without_conflating_volume(self):
        row = dict(zip(LEGACY_HEADER, [
            "OPTIDX", "NIFTY", "25-Jul-2024", "24000", "CE", "10", "12", "8",
            "11", "11.5", "123", "4.25", "500", "25", "05-JUL-2024",
        ]))
        normalized = row_as_dict(build_sqlite.legacy_values(row, 7, 1))
        self.assertEqual(normalized["trade_date"], "2024-07-05")
        self.assertEqual(normalized["underlying_kind"], "INDEX")
        self.assertEqual(normalized["instrument_kind"], "OPTION")
        self.assertEqual(normalized["contracts"], 123)
        self.assertIsNone(normalized["traded_quantity"])
        self.assertEqual(normalized["traded_value_rupees"], 425_000)

    def test_normalizes_udiff_values(self):
        row = {
            "TradDt": "2024-07-08", "BizDt": "2024-07-08", "Sgmt": "FO", "Src": "NSE",
            "FinInstrmTp": "STO", "FinInstrmId": "67522", "TckrSymb": "ABFRL",
            "XpryDt": "2024-07-25", "FininstrmActlXpryDt": "2024-07-25",
            "StrkPric": "250", "OptnTp": "PE", "FinInstrmNm": "ABFRL24JUL250PE",
            "OpnPric": "1", "HghPric": "2", "LwPric": ".5", "ClsPric": "1.5",
            "LastPric": "1.4", "PrvsClsgPric": "1.2", "UndrlygPric": "322.25",
            "SttlmPric": "1.5", "OpnIntrst": "26000", "ChngInOpnIntrst": "100",
            "TtlTradgVol": "5200", "TtlTrfVal": "7800", "TtlNbOfTxsExctd": "12",
            "SsnId": "F1", "NewBrdLotQty": "2600",
        }
        normalized = row_as_dict(build_sqlite.udiff_values(row, 8, 1))
        self.assertEqual(normalized["underlying_kind"], "STOCK")
        self.assertEqual(normalized["instrument_kind"], "OPTION")
        self.assertIsNone(normalized["contracts"])
        self.assertEqual(normalized["traded_quantity"], 5200)
        self.assertEqual(normalized["traded_value_rupees"], 7800)

    def test_import_is_resumable_and_does_not_duplicate_rows(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            data_root = root / "data"
            archive_path = data_root / "2024" / "07" / "fo05JUL2024bhav.csv.zip"
            archive_path.parent.mkdir(parents=True)
            output = io.StringIO(newline="")
            writer = csv.DictWriter(output, fieldnames=LEGACY_HEADER)
            writer.writeheader()
            writer.writerow(dict(zip(LEGACY_HEADER, [
                "FUTIDX", "NIFTY", "25-Jul-2024", "0", "XX", "10", "12", "8",
                "11", "11.5", "123", "4.25", "500", "25", "05-JUL-2024",
            ])))
            writer.writerow(dict(zip(LEGACY_HEADER, [
                "OPTIDX", "NIFTY", "25-Jul-2024", "24000", "CE", "0", "0", "0",
                "0", "0", "0", "0", "0", "0", "05-JUL-2024",
            ])))
            with zipfile.ZipFile(archive_path, "w", zipfile.ZIP_DEFLATED) as zipped:
                zipped.writestr("fo05JUL2024bhav.csv", output.getvalue())

            connection = build_sqlite.connect(root / "test.db")
            try:
                self.assertEqual(
                    build_sqlite.import_archive(connection, data_root, archive_path),
                    ("imported", 1),
                )
                self.assertEqual(
                    build_sqlite.import_archive(connection, data_root, archive_path),
                    ("skipped", 1),
                )
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM bhavcopy").fetchone()[0], 1)
                self.assertEqual(connection.execute("SELECT COUNT(*) FROM source_files").fetchone()[0], 1)
                self.assertEqual(
                    connection.execute(
                        "SELECT source_row_count, row_count, zero_volume_rows FROM source_files"
                    ).fetchone(),
                    (2, 1, 1),
                )
            finally:
                connection.close()


if __name__ == "__main__":
    unittest.main()
