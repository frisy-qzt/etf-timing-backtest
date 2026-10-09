import datetime as dt
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.parquet as pq

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
import prepare_etf
from verify_etf import IndependentLookup


class FactorPolicyTests(unittest.TestCase):
    def setUp(self):
        self.frames = {
            "qfq": pd.DataFrame(
                {
                    "code": ["510050.SH"] * 3,
                    "date": pd.to_datetime(["2026-01-01", "2026-01-03", "2026-01-04"]),
                    "factor": [0.75, -0.5, 0.0],
                }
            ),
            "hfq": pd.DataFrame(
                {"code": ["510300.SH"], "date": pd.to_datetime(["2026-01-01"]), "factor": [2.0]}
            ),
        }
        self.codes = ["510050.SH"] * 4 + ["510300.SH", "999999.SH", "510050.SH", "510050.SH"]
        self.times = [dt.datetime(2026, 1, d, 9, 30) for d in [1, 2, 3, 4, 1, 1]] + [
            dt.datetime(2025, 12, 31, 15),
            dt.datetime(2026, 1, 5, 15),
        ]
        self.table = pa.table(
            {"code": self.codes, "datetime": pa.array(self.times, type=pa.timestamp("ms"))}
        )

    def test_exact_negative_zero_and_date_fallback(self):
        # Interior, pre-coverage, and post-coverage missing dates all use 1.
        expected = [0.75, 1.0, -0.5, 0.0, None, None, 1.0, 1.0]
        for cls in (prepare_etf.FactorLookup, IndependentLookup):
            with self.subTest(matcher=cls.__name__):
                values, exact, fallback = cls(self.frames).resolve(self.table, "qfq")
                self.assertEqual(values.to_pylist(), expected)
                np.testing.assert_array_equal(
                    exact, [True, False, True, True, False, False, False, False]
                )
                np.testing.assert_array_equal(
                    fallback, [False, True, False, False, False, False, True, True]
                )

    def test_presence_is_specific_to_adjustment_version(self):
        for cls in (prepare_etf.FactorLookup, IndependentLookup):
            with self.subTest(matcher=cls.__name__):
                result, valid = cls(self.frames).match(self.table, "hfq")
                self.assertEqual(
                    result.to_pylist(), [None, None, None, None, 2.0, None, None, None]
                )
                self.assertEqual(int(valid.sum()), 1)

    def test_intraday_times_resolve_same_trading_date(self):
        table = pa.table(
            {
                "code": ["510050.SH"] * 3,
                "datetime": pa.array(
                    [
                        dt.datetime(2026, 1, 1, 0),
                        dt.datetime(2026, 1, 1, 23, 59),
                        dt.datetime(2026, 1, 2, 0),
                    ],
                    type=pa.timestamp("ms"),
                ),
            }
        )
        for cls in (prepare_etf.FactorLookup, IndependentLookup):
            self.assertEqual(cls(self.frames).match(table, "qfq")[0].to_pylist(), [0.75, 0.75, 1.0])

    def test_written_prices_and_missing_classification(self):
        raw = self.table
        for name in ("open", "close", "high", "low", "pre_close", "limit_up", "limit_down"):
            raw = raw.append_column(name, pa.array([20.0] * len(raw)))
        raw = raw.append_column("volume", pa.array([100] * len(raw), type=pa.int64()))
        raw = raw.append_column("amount", pa.array([2000] * len(raw), type=pa.int64()))
        raw = raw.append_column("nav", pa.array([20.0] * len(raw)))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / "daily"
            folder.mkdir()
            pq.write_table(raw, folder / "ETF_daily.parquet")
            (folder / "manifest.json").write_text(
                json.dumps(
                    {
                        "frequency": "daily",
                        "rows": len(raw),
                        "min_datetime": "2025-12-31",
                        "max_datetime": "2026-01-05",
                        "schema": str(raw.schema),
                    }
                )
            )
            with patch.object(prepare_etf, "ROOT", root):
                prepare_etf.adjust("daily", prepare_etf.FactorLookup(self.frames))
            adjusted = pq.read_table(folder / "qfq/ETF_daily.parquet")
            for name in prepare_etf.PRICES:
                self.assertEqual(
                    adjusted[name].to_pylist(), [15.0, 20.0, -10.0, 0.0, None, None, 20.0, 20.0]
                )
            for name in ("code", "datetime", "volume", "amount", "nav"):
                self.assertTrue(adjusted[name].equals(raw[name]))
            manifest = json.loads((folder / "qfq/manifest.json").read_text())
            self.assertEqual(manifest["default_factor_1_rows"], 3)
            self.assertEqual(manifest["missing_factor_rows"], 2)
            self.assertEqual(manifest["source_missing_factor_rows"], 5)
            missing = pd.read_csv(folder / "qfq/missing_factors.csv")
            self.assertEqual(
                set(missing.loc[missing.reason == "etf_missing_null", "code"]),
                {"510300.SH", "999999.SH"},
            )
            self.assertEqual(
                set(missing.loc[missing.reason == "date_missing_use_1", "effective_factor"]), {1.0}
            )


if __name__ == "__main__":
    unittest.main()
