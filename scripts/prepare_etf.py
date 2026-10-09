"""Build daily bars and exact-date adjusted ETF datasets without changing sources."""

import argparse
import json
import zipfile
from pathlib import Path

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

ROOT = Path(__file__).resolve().parents[1]
DAY_MS = 86_400_000
PRICES = ("open", "close", "high", "low", "pre_close", "limit_up", "limit_down")
FREQUENCIES = ("daily", "60min", "30min", "15min", "5min", "1min")
ADJUSTMENT_POLICY = "existing_etf_missing_date_factor_1_v1"


def save_json(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n")


def extract_factors():
    frames = {}
    for version, label in [("qfq", "前复权"), ("hfq", "后复权")]:
        archive = ROOT / "基金_复权因子" / f"ETF_复权因子_{label}.zip"
        dest = archive.parent / version
        dest.mkdir(exist_ok=True)
        parts = []
        with zipfile.ZipFile(archive) as z:
            for entry in z.infolist():
                # Archives contain flat CSVs; never allow paths outside destination.
                if not entry.filename.endswith(".csv"):
                    continue
                if Path(entry.filename).name != entry.filename:
                    raise ValueError(f"Unsafe archive member: {entry.filename}")
                data = z.read(entry)
                (dest / entry.filename).write_bytes(data)
                part = pd.read_csv(dest / entry.filename, encoding="utf-8-sig")
                part.columns = ["code", "date", "factor"]
                part["date"] = pd.to_datetime(part["date"].astype(str), format="%Y%m%d")
                if not (part["code"] == Path(entry.filename).stem).all():
                    raise ValueError(f"Code mismatch: {entry.filename}")
                parts.append(part)
        frame = pd.concat(parts, ignore_index=True)
        if frame.duplicated(["code", "date"]).any():
            raise ValueError(f"Duplicate factors: {version}")
        valid = np.isfinite(frame.factor)
        frame.loc[~valid].to_csv(dest / "invalid_factors.csv", index=False)
        invalid_rows = int((~valid).sum())
        frame = frame.loc[valid].copy()
        pq.write_table(
            pa.Table.from_pandas(frame, preserve_index=False),
            dest / "factors.parquet",
            compression="zstd",
        )
        save_json(
            dest / "manifest.json",
            {
                "source": str(archive),
                "files": len(parts),
                "rows": len(frame),
                "invalid_rows": invalid_rows,
                "negative_factor_rows": int((frame.factor < 0).sum()),
                "zero_factor_rows": int((frame.factor == 0).sum()),
                "min_date": str(frame.date.min()),
                "max_date": str(frame.date.max()),
            },
        )
        frames[version] = frame
        print(f"Factors {version}: {len(parts):,} CSVs, {len(frame):,} rows", flush=True)
    return frames


def build_daily():
    dest = ROOT / "daily"
    dest.mkdir(exist_ok=True)
    target = dest / "ETF_daily.parquet"
    temporary = target.with_suffix(".parquet.partial")
    files = sorted((ROOT / "ETF日线").glob("*/*.csv"))
    writer = None
    rows = 0
    sources = []
    min_date = None
    max_date = None
    with_dates = {}
    try:
        for f in files:
            frame = pd.read_csv(f, encoding="utf-8-sig", dtype={"code": str, "name": str})
            frame["datetime"] = pd.to_datetime(frame.datetime, format="%Y-%m-%d")
            if frame.empty:
                continue
            if not (frame.code == f.stem).all() or frame.duplicated(["code", "datetime"]).any():
                raise ValueError(f"Invalid daily keys: {f}")
            dates = set(frame.datetime)
            if with_dates.setdefault(f.stem, set()) & dates:
                raise ValueError(f"Overlapping daily sources: {f}")
            with_dates[f.stem].update(dates)
            fields = [
                pa.field(
                    c,
                    pa.timestamp("ms")
                    if c == "datetime"
                    else pa.string()
                    if c in ("code", "name")
                    else pa.int64()
                    if c == "total_shares"
                    else pa.float64(),
                )
                for c in frame.columns
            ]
            schema = pa.schema(fields)
            table = pa.Table.from_pandas(frame, schema=schema, preserve_index=False)
            if writer is None:
                writer = pq.ParquetWriter(temporary, schema, compression="zstd")
            writer.write_table(table)
            rows += len(frame)
            lo, hi = frame.datetime.min(), frame.datetime.max()
            min_date = lo if min_date is None else min(min_date, lo)
            max_date = hi if max_date is None else max(max_date, hi)
            sources.append({"source": str(f.relative_to(ROOT)), "rows": len(frame)})
        if writer is None:
            raise ValueError("No daily rows")
    finally:
        if writer is not None:
            writer.close()
    temporary.replace(target)
    manifest = {
        "frequency": "daily",
        "file": target.name,
        "adjustment": "none",
        "rows": rows,
        "symbols": len(with_dates),
        "min_datetime": str(min_date),
        "max_datetime": str(max_date),
        "schema": str(schema),
        "sources": sources,
    }
    save_json(dest / "manifest.json", manifest)
    print(f"Daily: {rows:,} rows, {len(with_dates):,} symbols", flush=True)


class FactorLookup:
    def __init__(self, frames):
        codes = sorted(set().union(*(set(f.code) for f in frames.values())))
        self.codes = pa.array(codes)
        self.scale = 100_000
        self.keys = {}
        self.values = {}
        self.version_codes = {}
        for v, f in frames.items():
            self.version_codes[v] = pa.array(sorted(set(f.code)))
            ids = pd.Categorical(f.code, categories=codes).codes.astype(np.int64)
            days = f.date.to_numpy(dtype="datetime64[D]").astype(np.int64)
            keys = ids * self.scale + days
            order = np.argsort(keys)
            self.keys[v] = keys[order]
            self.values[v] = f.factor.to_numpy()[order]

    def resolve(self, table, version):
        ids = (
            pc.fill_null(pc.index_in(table["code"], value_set=self.codes), -1)
            .to_numpy()
            .astype(np.int64)
        )
        days = table["datetime"].cast(pa.int64()).to_numpy() // DAY_MS
        key = ids * self.scale + days
        keys = self.keys[version]
        positions = np.searchsorted(keys, key)
        safe = np.minimum(positions, len(keys) - 1)
        found = (ids >= 0) & (positions < len(keys)) & (keys[safe] == key)
        known_code = pc.is_in(table["code"], value_set=self.version_codes[version]).to_numpy()
        defaulted = known_code & ~found
        values = np.where(found, self.values[version][safe], np.where(defaulted, 1.0, np.nan))
        return pa.array(values, mask=~(found | defaulted)), found, defaulted

    def match(self, table, version):
        factors, found, defaulted = self.resolve(table, version)
        return factors, found | defaulted


def adjust(frequency, lookup):
    source = ROOT / frequency / f"ETF_{frequency}.parquet"
    original = json.loads((source.parent / "manifest.json").read_text())
    parquet = pq.ParquetFile(source)
    writers = {}
    targets = {}
    missing = {"qfq": {}, "hfq": {}}
    total = 0
    try:
        for v in missing:
            folder = source.parent / v
            folder.mkdir(exist_ok=True)
            target = folder / source.name
            targets[v] = target
            writers[v] = pq.ParquetWriter(
                target.with_suffix(".parquet.partial"), parquet.schema_arrow, compression="zstd"
            )
        for batch in parquet.iter_batches(batch_size=250_000):
            table = pa.Table.from_batches([batch])
            for v in writers:
                factors, found, defaulted = lookup.resolve(table, v)
                adjusted = table
                for name in PRICES:
                    if name in table.column_names:
                        adjusted = adjusted.set_column(
                            table.schema.get_field_index(name),
                            table.schema.field(name),
                            pc.multiply(table[name], factors),
                        )
                if not found.all():
                    absent = table.select(["code", "datetime"]).filter(pa.array(~found)).to_pandas()
                    absent["date"] = absent.datetime.dt.strftime("%Y-%m-%d")
                    absent["reason"] = np.where(
                        defaulted[~found], "date_missing_use_1", "etf_missing_null"
                    )
                    for (code, date, reason), count in (
                        absent.groupby(["code", "date", "reason"]).size().items()
                    ):
                        key = (code, date, reason)
                        missing[v][key] = missing[v].get(key, 0) + int(count)
                writers[v].write_table(adjusted)
            total += len(table)
            if total % 5_000_000 < len(table):
                print(f"{frequency}: {total:,}/{parquet.metadata.num_rows:,}", flush=True)
    finally:
        for writer in writers.values():
            writer.close()
    if total != parquet.metadata.num_rows:
        raise ValueError("Row count mismatch")
    for v, target in targets.items():
        target.with_suffix(".parquet.partial").replace(target)
        counts = missing[v]
        details = pd.DataFrame(
            [
                {
                    "code": c,
                    "date": d,
                    "reason": reason,
                    "rows": n,
                    "effective_factor": 1.0 if reason == "date_missing_use_1" else np.nan,
                }
                for (c, d, reason), n in sorted(counts.items())
            ],
            columns=["code", "date", "reason", "rows", "effective_factor"],
        )
        details.to_csv(target.parent / "missing_factors.csv", index=False)
        defaults = details.loc[details.reason == "date_missing_use_1"]
        unresolved = details.loc[details.reason == "etf_missing_null"]
        defaults.to_csv(target.parent / "factor_1_dates.csv", index=False)
        unresolved.groupby("code", as_index=False)["rows"].sum().to_csv(
            target.parent / "etf_without_factors.csv", index=False
        )
        manifest = {
            k: original[k] for k in ("frequency", "rows", "min_datetime", "max_datetime", "schema")
        }
        manifest.update(
            {
                "file": target.name,
                "adjustment": v,
                "source": str(source.relative_to(ROOT)),
                "adjustment_policy": ADJUSTMENT_POLICY,
                "factor_source": f"基金_复权因子/{v}/factors.parquet",
                "formula": "adjusted_price = original_price * effective_factor(code, trading_date); existing ETF missing date -> 1; ETF absent from this version -> null",
                "adjusted_columns": [c for c in PRICES if c in parquet.schema_arrow.names],
                "missing_factor_policy": "missing date for existing ETF: factor 1; entire ETF absent: null adjusted prices; no forward fill or renormalization",
                "source_missing_factor_rows": int(details.rows.sum()),
                "source_missing_factor_code_dates": len(details),
                "default_factor_1_rows": int(defaults.rows.sum()),
                "default_factor_1_code_dates": len(defaults),
                "missing_factor_rows": int(unresolved.rows.sum()),
                "missing_factor_code_dates": len(unresolved),
                "etfs_without_factors": int(unresolved.code.nunique()),
                "unchanged_columns": [c for c in parquet.schema_arrow.names if c not in PRICES],
                "bytes": target.stat().st_size,
            }
        )
        save_json(target.parent / "manifest.json", manifest)
        print(
            f"{frequency} {v}: {total:,} rows, date fallback 1: {int(defaults.rows.sum()):,}; ETF absent: {int(unresolved.rows.sum()):,}",
            flush=True,
        )


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frequencies", nargs="+", choices=FREQUENCIES, default=FREQUENCIES)
    parser.add_argument(
        "--reuse-inputs",
        action="store_true",
        help="Reuse existing daily bars and extracted factors; regenerate adjusted versions only",
    )
    args = parser.parse_args()
    frames = (
        {
            v: pq.read_table(ROOT / "基金_复权因子" / v / "factors.parquet").to_pandas()
            for v in ("qfq", "hfq")
        }
        if args.reuse_inputs
        else extract_factors()
    )
    if "daily" in args.frequencies and not args.reuse_inputs:
        build_daily()
    lookup = FactorLookup(frames)
    for frequency in args.frequencies:
        adjust(frequency, lookup)


if __name__ == "__main__":
    main()
