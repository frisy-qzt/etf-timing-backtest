"""Count source nonpositive closes separately from dividend-adjusted prices."""

import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from prepare_etf import FREQUENCIES, ROOT, save_json


def main():
    results = {}
    for freq in FREQUENCIES:
        source = pq.ParquetFile(ROOT / freq / f"ETF_{freq}.parquet")
        counts = {}
        zeros = negatives = 0
        for batch in source.iter_batches(batch_size=500_000, columns=["code", "datetime", "close"]):
            table = pa.Table.from_batches([batch])
            zeros += int(pc.sum(pc.fill_null(pc.equal(table["close"], 0), False)).as_py() or 0)
            negatives += int(pc.sum(pc.fill_null(pc.less(table["close"], 0), False)).as_py() or 0)
            mask = pc.fill_null(pc.less_equal(table["close"], 0), False)
            if not pc.any(mask).as_py():
                continue
            data = table.filter(mask).to_pandas()
            data["date"] = data.datetime.dt.strftime("%Y-%m-%d")
            for (code, date), n in data.groupby(["code", "date"]).size().items():
                counts[(code, date)] = counts.get((code, date), 0) + int(n)
        pd.DataFrame(
            [{"code": c, "date": d, "rows": n} for (c, d), n in sorted(counts.items())],
            columns=["code", "date", "rows"],
        ).to_csv(ROOT / "reports" / f"{freq}_source_nonpositive_close.csv", index=False)
        results[freq] = {
            "zero_close_rows": zeros,
            "negative_close_rows": negatives,
            "code_dates": len(counts),
        }
        print(f"{freq}: source zero close rows {zeros:,}; negative {negatives:,}", flush=True)
    save_json(ROOT / "reports" / "source_price_quality.json", results)


if __name__ == "__main__":
    main()
