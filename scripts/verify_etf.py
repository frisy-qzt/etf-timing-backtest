"""Verify all bars against sources and report continuity/source-factor anomalies."""

import argparse
import csv
import json

import numpy as np
import pandas as pd
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from prepare_etf import ADJUSTMENT_POLICY, FREQUENCIES, PRICES, ROOT, save_json


def data_fingerprint(freq):
    folder = ROOT / freq
    paths = [
        folder / f"ETF_{freq}.parquet",
        folder / "qfq" / f"ETF_{freq}.parquet",
        folder / "hfq" / f"ETF_{freq}.parquet",
        ROOT / "基金_复权因子/qfq/factors.parquet",
        ROOT / "基金_复权因子/hfq/factors.parquet",
    ]
    return {
        str(p.relative_to(ROOT)): {"bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns}
        for p in paths
    }


class IndependentLookup:
    """Match per-symbol date arrays, independently of the generator's encoded keys."""

    def __init__(self, frames):
        self.symbols = {}
        for version, frame in frames.items():
            self.symbols[version] = {
                code: (group.date.to_numpy(dtype="datetime64[D]"), group.factor.to_numpy())
                for code, group in frame.sort_values(["code", "date"]).groupby("code")
            }

    def resolve(self, table, version):
        codes = table["code"].to_numpy()
        days = table["datetime"].to_numpy().astype("datetime64[D]")
        result = np.full(len(table), np.nan)
        exact = np.zeros(len(table), dtype=bool)
        defaulted = np.zeros(len(table), dtype=bool)
        starts = np.r_[0, np.flatnonzero(codes[1:] != codes[:-1]) + 1]
        ends = np.r_[starts[1:], len(table)]
        for start, end in zip(starts, ends):
            if codes[start] not in self.symbols[version]:
                continue
            dates, values = self.symbols[version][codes[start]]
            idx = np.searchsorted(dates, days[start:end])
            safe = np.minimum(idx, len(dates) - 1)
            found = (idx < len(dates)) & (dates[safe] == days[start:end])
            result[start:end] = np.where(found, values[safe], 1.0)
            exact[start:end] = found
            defaulted[start:end] = ~found
        return pa.array(result, mask=~(exact | defaulted)), exact, defaulted

    def match(self, table, version):
        factor, exact, defaulted = self.resolve(table, version)
        return factor, exact | defaulted


def verify_daily_sources():
    source = pq.ParquetFile(ROOT / "daily/ETF_daily.parquet")
    rows = 0
    files = sorted((ROOT / "ETF日线").glob("*/*.csv"))
    group = 0
    for file in files:
        frame = pd.read_csv(file, encoding="utf-8-sig", dtype={"code": str, "name": str})
        frame["datetime"] = pd.to_datetime(frame.datetime, format="%Y-%m-%d")
        if frame.empty:
            continue
        # Each source file was written as one row group (all files are below 1M rows).
        table = source.read_row_group(group)
        if table.column_names != list(frame.columns) or len(table) != len(frame):
            raise AssertionError(f"Daily source count/columns mismatch: {file}")
        for c in frame.columns:
            expected = pa.array(frame[c], type=table.schema.field(c).type, from_pandas=True)
            if not expected.equals(table[c].combine_chunks()):
                raise AssertionError(f"Daily source values mismatch: {file} {c}")
        rows += len(frame)
        group += 1
    if group != source.num_row_groups or rows != source.metadata.num_rows:
        raise AssertionError("Daily source total mismatch")
    print(f"Verified daily original CSVs: {len(files):,} files, {rows:,} rows", flush=True)
    return {"csv_files": len(files), "rows": rows, "all_fields_match_sources": True}


def verify_frequency(freq, lookup):
    initial_fingerprint = data_fingerprint(freq)
    source = ROOT / freq / f"ETF_{freq}.parquet"
    original = pq.ParquetFile(source)
    results = {}
    endpoint_parts = []
    for version in ("qfq", "hfq"):
        path = source.parent / version / source.name
        output = pq.ParquetFile(path)
        if (
            output.schema_arrow != original.schema_arrow
            or output.metadata.num_rows != original.metadata.num_rows
        ):
            raise AssertionError(f"Schema/count mismatch: {freq} {version}")
        expected_rows = missing = 0
        source_missing = default_rows = 0
        previous = {}
        anomalies = 0
        reversal_rows = 0
        negative_reversal_rows = 0
        session_gap_rows = 0
        anomaly_path = ROOT / "reports" / f"{freq}_{version}_storage_order_screening.csv"
        report = anomaly_path.open("w", newline="")
        csv_output = csv.writer(report)
        csv_output.writerow(
            [
                "code",
                "datetime",
                "previous_datetime",
                "raw_close",
                "adjusted_close",
                "previous_adjusted_close",
                "change_pct",
                "non_increasing_time",
                "session_time_gap",
            ]
        )
        a = original.iter_batches(batch_size=250_000)
        b = output.iter_batches(batch_size=250_000)
        for raw, adj in zip(a, b, strict=True):
            t, u = pa.Table.from_batches([raw]), pa.Table.from_batches([adj])
            factor, exact, defaulted = lookup.resolve(t, version)
            valid = exact | defaulted
            for c in t.column_names:
                expected = pc.multiply(t[c], factor) if c in PRICES else t[c]
                if not expected.equals(u[c]):
                    raise AssertionError(f"Value mismatch: {freq} {version} {c} at {expected_rows}")
            codes = t["code"].to_numpy()
            times = t["datetime"].cast(pa.int64()).to_numpy()
            closes = u["close"].to_numpy()
            raw_closes = t["close"].to_numpy()
            if version == "qfq":
                endpoint_parts.append(day_endpoints(t.select(["code", "datetime", "close"])))
            prev_close = np.roll(closes, 1)
            prev_time = np.roll(times, 1)
            starts = np.r_[0, np.flatnonzero(codes[1:] != codes[:-1]) + 1]
            ends = np.r_[starts[1:] - 1, len(codes) - 1]
            for start, end in zip(starts, ends):
                prev_close[start], prev_time[start] = previous.get(codes[start], (np.nan, -1))
                previous[codes[start]] = (closes[end], times[end])
            with np.errstate(divide="ignore", invalid="ignore"):
                changes = (closes / prev_close - 1) * 100
            bad_time = (prev_time >= 0) & (times <= prev_time)
            gaps = np.zeros(len(times), dtype=bool)
            if freq != "daily":
                minute = (times % 86_400_000) // 60_000
                prior_minute = (prev_time % 86_400_000) // 60_000
                same_day = times // 86_400_000 == prev_time // 86_400_000
                morning = (
                    (minute >= 570)
                    & (minute <= 690)
                    & (prior_minute >= 570)
                    & (prior_minute <= 690)
                )
                afternoon = (
                    (minute >= 780)
                    & (minute <= 900)
                    & (prior_minute >= 780)
                    & (prior_minute <= 900)
                )
                gaps = same_day & (morning | afternoon) & (minute - prior_minute > int(freq[:-3]))
            jumps = (np.abs(changes) > 20) | bad_time | gaps
            for i in np.flatnonzero(jumps):
                csv_output.writerow(
                    [
                        codes[i],
                        str(np.datetime64(int(times[i]), "ms")),
                        str(np.datetime64(int(prev_time[i]), "ms")),
                        raw_closes[i],
                        closes[i],
                        prev_close[i],
                        changes[i],
                        bool(bad_time[i]),
                        bool(gaps[i]),
                    ]
                )
            anomalies += int(jumps.sum())
            inverted = pc.fill_null(pc.less(u["high"], u["low"]), False)
            reversal_rows += int(pc.sum(inverted).as_py() or 0)
            negative_reversal_rows += int(
                pc.sum(pc.and_(inverted, pc.fill_null(pc.less(factor, 0), False))).as_py() or 0
            )
            session_gap_rows += int(gaps.sum())
            expected_rows += len(t)
            missing += int((~valid).sum())
            source_missing += int((~exact).sum())
            default_rows += int(defaulted.sum())
        manifest = json.loads((path.parent / "manifest.json").read_text())
        if (
            expected_rows != original.metadata.num_rows
            or missing != manifest["missing_factor_rows"]
            or source_missing != manifest["source_missing_factor_rows"]
            or default_rows != manifest["default_factor_1_rows"]
        ):
            raise AssertionError(f"Manifest mismatch: {freq} {version}")
        if manifest.get("adjustment_policy") != ADJUSTMENT_POLICY:
            raise AssertionError(f"Adjustment policy mismatch: {freq} {version}")
        report.close()
        results[version] = {
            "rows_verified": expected_rows,
            "missing_factor_rows": missing,
            "adjustment_policy": ADJUSTMENT_POLICY,
            "source_missing_factor_rows": source_missing,
            "default_factor_1_rows": default_rows,
            "bar_continuity_flags": anomalies,
            "high_less_than_low_rows": reversal_rows,
            "negative_factor_high_low_reversals": negative_reversal_rows,
            "other_high_low_reversals": reversal_rows - negative_reversal_rows,
            "session_time_gap_rows": session_gap_rows,
            "storage_screening_file": anomaly_path.name,
            "all_prices_match": True,
            "all_other_columns_unchanged": True,
        }
        print(f"Verified {freq} {version}: {expected_rows:,} rows", flush=True)
    if initial_fingerprint != data_fingerprint(freq):
        raise AssertionError(f"Data changed during verification: {freq}")
    write_endpoints(freq, endpoint_parts)
    for values in results.values():
        values["data_fingerprint"] = initial_fingerprint
    return results


def day_endpoints(table):
    codes = table["code"].to_numpy()
    times = table["datetime"].cast(pa.int64()).to_numpy()
    days = times // 86_400_000
    same_group = (codes[1:] == codes[:-1]) & (days[1:] == days[:-1])
    if np.any(same_group & (times[1:] < times[:-1])):
        table = table.take(
            pc.sort_indices(table, sort_keys=[("code", "ascending"), ("datetime", "ascending")])
        )
        codes = table["code"].to_numpy()
        times = table["datetime"].cast(pa.int64()).to_numpy()
        days = times // 86_400_000
    starts = np.r_[0, np.flatnonzero((codes[1:] != codes[:-1]) | (days[1:] != days[:-1])) + 1]
    ends = np.r_[starts[1:] - 1, len(table) - 1]
    return pa.table(
        {
            "code": pc.take(table["code"], pa.array(starts)),
            "first_datetime": pc.take(table["datetime"], pa.array(starts)),
            "first_close": pc.take(table["close"], pa.array(starts)),
            "last_datetime": pc.take(table["datetime"], pa.array(ends)),
            "last_close": pc.take(table["close"], pa.array(ends)),
        }
    )


def write_endpoints(freq, parts):
    data = pa.concat_tables(parts).to_pandas()
    data["date"] = data.first_datetime.dt.normalize()
    first = data.sort_values("first_datetime").groupby(["code", "date"], sort=False).head(1)
    last = data.sort_values("last_datetime").groupby(["code", "date"], sort=False).tail(1)
    combined = first[["code", "date", "first_datetime", "first_close"]].merge(
        last[["code", "date", "last_datetime", "last_close"]],
        on=["code", "date"],
        validate="one_to_one",
    )
    combined = combined.sort_values(["code", "date"])
    pq.write_table(
        pa.Table.from_pandas(combined, preserve_index=False),
        ROOT / "reports" / f"{freq}_day_endpoints.parquet",
        compression="zstd",
    )


def finalize_chronological_continuity(freq, lookup, checks):
    endpoint_path = ROOT / "reports" / f"{freq}_day_endpoints.parquet"
    if not endpoint_path.exists():
        source = pq.ParquetFile(ROOT / freq / f"ETF_{freq}.parquet")
        parts = [
            day_endpoints(pa.Table.from_batches([batch]))
            for batch in source.iter_batches(
                batch_size=250_000, columns=["code", "datetime", "close"]
            )
        ]
        write_endpoints(freq, parts)
    data = pq.read_table(endpoint_path).to_pandas()
    for version in ("qfq", "hfq"):
        first = pa.Table.from_pandas(
            data[["code", "first_datetime"]].rename(columns={"first_datetime": "datetime"}),
            preserve_index=False,
        )
        last = pa.Table.from_pandas(
            data[["code", "last_datetime"]].rename(columns={"last_datetime": "datetime"}),
            preserve_index=False,
        )
        factor_first, _ = lookup.match(first, version)
        factor_last, _ = lookup.match(last, version)
        data["adjusted_first"] = data.first_close.to_numpy() * factor_first.to_numpy(
            zero_copy_only=False
        )
        data["adjusted_last"] = data.last_close.to_numpy() * factor_last.to_numpy(
            zero_copy_only=False
        )
        previous_close = data.groupby("code").adjusted_last.shift()
        previous_time = data.groupby("code").last_datetime.shift()
        with np.errstate(divide="ignore", invalid="ignore"):
            change = (data.adjusted_first / previous_close - 1) * 100
        mask = change.abs() > 20
        cross = pd.DataFrame(
            {
                "code": data.loc[mask, "code"],
                "datetime": data.loc[mask, "first_datetime"].astype(str),
                "previous_datetime": previous_time.loc[mask].astype(str),
                "raw_close": data.loc[mask, "first_close"],
                "adjusted_close": data.loc[mask, "adjusted_first"],
                "previous_adjusted_close": previous_close.loc[mask],
                "change_pct": change.loc[mask],
                "non_increasing_time": False,
                "session_time_gap": False,
            }
        )
        path = ROOT / "reports" / f"{freq}_{version}_bar_continuity.csv"
        storage_path = ROOT / "reports" / f"{freq}_{version}_storage_order_screening.csv"
        if path.exists() and not storage_path.exists():
            path.replace(storage_path)
        storage = pd.read_csv(storage_path)
        same_day = (
            pd.to_datetime(storage.datetime).dt.normalize()
            == pd.to_datetime(storage.previous_datetime).dt.normalize()
        )
        intraday = storage.loc[same_day]
        chronological = pd.concat([intraday, cross], ignore_index=True)
        chronological["datetime"] = pd.to_datetime(chronological.datetime, format="mixed")
        chronological = chronological.sort_values(["code", "datetime"])
        chronological.to_csv(path, index=False)
        checks[version]["storage_order_non_increasing_rows"] = int(
            storage.non_increasing_time.sum()
        )
        checks[version]["bar_continuity_flags"] = len(chronological)
        checks[version]["chronological_overnight_jump_rows"] = len(cross)
        print(
            f"Chronological audit {freq} {version}: {len(chronological):,} flags, storage order reversals {int(storage.non_increasing_time.sum()):,}",
            flush=True,
        )


def continuity_report(frames):
    dest = ROOT / "reports"
    dest.mkdir(exist_ok=True)
    raw = pq.read_table(
        ROOT / "daily/ETF_daily.parquet", columns=["code", "datetime", "close"]
    ).to_pandas()
    raw = raw.sort_values(["code", "datetime"])
    raw["raw_previous_close"] = raw.groupby("code").close.shift()
    raw["raw_change_pct"] = (raw.close / raw.raw_previous_close - 1) * 100
    summaries = {}
    for version, factors in frames.items():
        data = raw.merge(
            factors,
            left_on=["code", "datetime"],
            right_on=["code", "date"],
            how="left",
            validate="one_to_one",
        )
        data["factor_defaulted"] = data.factor.isna() & data.code.isin(factors.code.unique())
        data.loc[data.factor_defaulted, "factor"] = 1.0
        data["adjusted_close"] = data.close * data.factor
        data["previous_adjusted_close"] = data.groupby("code").adjusted_close.shift()
        data["previous_factor"] = data.groupby("code").factor.shift()
        data["factor_change_pct"] = (data.factor / data.previous_factor - 1) * 100
        data["adjusted_change_pct"] = (data.adjusted_close / data.previous_adjusted_close - 1) * 100
        jumps = data.loc[
            (data.adjusted_change_pct.abs() > 20) | (data.factor_change_pct.abs() > 5)
        ].copy()
        jumps["adjusted_jump_only"] = (jumps.adjusted_change_pct.abs() > 20) & (
            jumps.raw_change_pct.abs() <= 20
        )
        jumps["previous_factor_defaulted"] = (
            data.groupby("code").factor_defaulted.shift().loc[jumps.index]
        )
        jumps["touches_default_factor_1"] = (
            jumps.factor_defaulted | jumps.previous_factor_defaulted.fillna(False).astype(bool)
        )
        jumps.to_csv(dest / f"{version}_daily_continuity.csv", index=False)
        summaries[version] = {
            "adjusted_close_jumps_over_20pct": int((data.adjusted_change_pct.abs() > 20).sum()),
            "factor_changes_over_5pct": int((data.factor_change_pct.abs() > 5).sum()),
            "adjusted_jump_only_rows": int(jumps.adjusted_jump_only.sum()),
            "price_jumps_touching_default_1": int(
                ((jumps.adjusted_change_pct.abs() > 20) & jumps.touches_default_factor_1).sum()
            ),
            "worst_adjusted_abs_change_pct": float(
                data.loc[np.isfinite(data.adjusted_change_pct), "adjusted_change_pct"].abs().max()
            ),
            "zero_previous_adjusted_close_rows": int((data.previous_adjusted_close == 0).sum()),
            "report_rows": len(jumps),
        }
    joined = frames["qfq"].merge(frames["hfq"], on=["code", "date"], suffixes=("_qfq", "_hfq"))
    joined["ratio"] = joined.factor_hfq / joined.factor_qfq.replace(0, np.nan)
    ratios = joined.groupby("code").ratio.agg(["min", "max", "count"])
    ratios["relative_range"] = (ratios["max"] - ratios["min"]) / ratios[["min", "max"]].abs().max(
        axis=1
    )
    anomalous = ratios.loc[ratios.relative_range > 1e-6]
    anomalous.to_csv(dest / "qfq_hfq_ratio_inconsistency.csv")
    summaries["factor_ratio_inconsistent_symbols"] = len(anomalous)
    summaries["qfq_zero_denominator_rows"] = int((joined.factor_qfq == 0).sum())
    summaries["qfq_negative_factor_rows"] = int((frames["qfq"].factor < 0).sum())
    summaries["thresholds"] = {
        "daily_price_jump_pct": 20,
        "factor_jump_pct": 5,
        "hfq_qfq_relative_range": 1e-6,
    }
    summaries["interpretation"] = (
        "Screening only, not proof of a bad market tick. Missing dates for an existing ETF use factor 1; ETF absent from this version remains null. Negative and zero supplied factors are preserved. Ratios are diagnostic only."
    )
    save_json(dest / "continuity_summary.json", summaries)
    return summaries


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--frequencies", nargs="+", choices=FREQUENCIES, default=FREQUENCIES)
    parser.add_argument("--finalize-only", action="store_true")
    args = parser.parse_args()
    frames = {
        v: pq.read_table(ROOT / "基金_复权因子" / v / "factors.parquet").to_pandas()
        for v in ("qfq", "hfq")
    }
    lookup = IndependentLookup(frames)
    summary = continuity_report(frames)
    daily_sources = verify_daily_sources()
    progress_path = ROOT / "reports" / "verification_progress.json"
    checks = json.loads(progress_path.read_text()) if progress_path.exists() else {}
    checks = {
        freq: results
        for freq, results in checks.items()
        if all(
            values.get("adjustment_policy") == ADJUSTMENT_POLICY
            and values.get("data_fingerprint") == data_fingerprint(freq)
            for values in results.values()
        )
    }
    for freq in () if args.finalize_only else args.frequencies:
        checks[freq] = verify_frequency(freq, lookup)
        save_json(progress_path, checks)
    if set(checks) != set(FREQUENCIES):
        if args.finalize_only:
            raise AssertionError("Cannot finalize: missing or stale verification results")
        print(
            "Partial verification saved; run remaining frequencies before completion.", flush=True
        )
        return
    for freq in FREQUENCIES:
        finalize_chronological_continuity(freq, lookup, checks[freq])
    save_json(
        ROOT / "reports" / "verification.json",
        {"daily_source_verification": daily_sources, "frequencies": checks, "continuity": summary},
    )
    lines = [
        "# ETF 数据核验与连续性报告",
        "",
        "六个频率均保留所有源行。按代码和交易日期匹配各版本因子后直接相乘；ETF在该版本有因子但缺当日记录时统一用1，整个ETF无该版本因子时价格为空。不向前填充、不重新归一化；负因子与零因子保留源值。",
        "",
        f"日线原始 CSV 共 {daily_sources['csv_files']:,} 个、{daily_sources['rows']:,} 行，已逐字段核对。",
        "",
        "## 各频率全量核验",
        "",
        "|频率|版本|已核对行数|缺日期按1计算行数|整个ETF缺因子空价行数|连续性筛查记录|时段内时间缺口|负因子导致 high<low|其他 high<low|",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for freq, versions in checks.items():
        for version, values in versions.items():
            lines.append(
                f"|{freq}|{version}|{values['rows_verified']:,}|{values['default_factor_1_rows']:,}|{values['missing_factor_rows']:,}|"
                f"{values['bar_continuity_flags']:,}|{values['session_time_gap_rows']:,}|"
                f"{values['negative_factor_high_low_reversals']:,}|{values['other_high_low_reversals']:,}|"
            )
    lines += [
        "",
        "## 复权后价格跳变",
        "",
        "以下阈值用于筛查，不等同于坏行情结论。除权、分红、拆分、真实波动及接近零价格均可能产生大百分比。",
        "",
        "|版本|日收盘绝对变化超过20%|其中涉及缺日期按1|因子变化超过5%|仅复权价格超过20%而原价未超过|最大有限绝对变化%|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for version in ("qfq", "hfq"):
        s = summary[version]
        lines.append(
            f"|{version}|{s['adjusted_close_jumps_over_20pct']:,}|{s['price_jumps_touching_default_1']:,}|{s['factor_changes_over_5pct']:,}|"
            f"{s['adjusted_jump_only_rows']:,}|{s['worst_adjusted_abs_change_pct']:.6g}|"
        )
    lines += [
        "",
        "### 涉及缺日期按1的跳变示例",
        "",
        "当邻日原因子与1差异很大时，统一设1会改变价格尺度。以下按指定规则计算，不沿用前日因子；完整明细的 touches_default_factor_1 列标记此类记录。",
        "",
        "|版本|代码|日期|当日原收盘|前日有效因子|当日有效因子|复权收盘变化%|",
        "|---|---|---|---:|---:|---:|---:|",
    ]
    for version in ("qfq", "hfq"):
        jumps = pd.read_csv(ROOT / "reports" / f"{version}_daily_continuity.csv")
        examples = jumps.loc[
            jumps.touches_default_factor_1 & np.isfinite(jumps.adjusted_change_pct)
        ].copy()
        examples["size"] = examples.adjusted_change_pct.abs()
        for row in examples.sort_values("size", ascending=False).head(2).itertuples():
            lines.append(
                f"|{version}|{row.code}|{str(row.datetime)[:10]}|{row.close:.6g}|{row.previous_factor:.10g}|{row.factor:.10g}|{row.adjusted_change_pct:.6g}|"
            )
    lines += [
        "",
        "## 口径及阅读说明",
        "",
        "- qfq 包包含 145 条负因子，按用户说明保留使用；另外4条零因子也按源值计算，产生零价格。负因子导致 high/low 大小反转属于直接相乘结果，未擅自交换。",
        "- 前一复权收盘为0时百分比变化无定义（CSV 可能为 inf）；最大变化统计只包含有限数值。",
        "- hfq 源因子包最晚为2026-09-29。2026-09-30对于包内已有ETF按缺日期规则使用因子1，因此该日复权价格等于原价；整个ETF无因子的仍为空。",
        "- pct_chg、amplitude 保留源值，未按复权价格重新计算；不能据此判断复权后连续性。",
        "- 日线 pre_close、limit_up、limit_down 使用当日因子；nav、acc_nav、discount_rate、total_assets、total_shares 保留源值。",
        "- 各频率 *_bar_continuity.csv 筛查相邻有效收盘价变化超过20%、时间重复/倒序，以及同一上午或下午时段内大于频率的时间间隔。缺失复权价格不会跨越补算；停牌等也可能造成时间缺口。未引入外部交易日历，未判定跨日缺交易日。",
        "- 分钟源文件部分日期按存储顺序倒序。*_storage_order_screening.csv 保留存储顺序筛查，*_bar_continuity.csv 为日内及按代码/日期排序后的跨日价格筛查；跨日比较前一交易记录日末价和下一记录日首价，不把日期乱序当价格不连续。*_day_endpoints.parquet 保留每日首末记录，供复核。",
        "- qfq_daily_continuity.csv、hfq_daily_continuity.csv 另列日价格/因子跳变。",
        "- 前后复权比率变化仅作口径诊断，现金分红等不同计算口径可能使比率变化，不作为错误判定。零分母单列计数。",
        "- missing_factors.csv 按代码、日期、reason区分 date_missing_use_1 和 etf_missing_null；factor_1_dates.csv 单列按1计算日期，etf_without_factors.csv 列出整个ETF无因子的代码及行数。",
        "- 缺日期设为1是用户指定的计算口径，并非沿用前日因子；它可能在相邻日期引入价格跳变。日线跳变CSV的 touches_default_factor_1 列标记当前或前日涉及默认1的记录。缺失分类详见 因子缺失分类.md。",
        "- 全量价格逐字段核验使用独立匹配逻辑；所有非价格字段与不复权源完全一致。",
        "",
    ]
    quality_path = ROOT / "reports/source_price_quality.json"
    if quality_path.exists():
        quality = json.loads(quality_path.read_text())
        lines += [
            "## 原行情非正收盘价",
            "",
            "下列为不复权源行情自身的零价/负价，未删除或修正，与负前复权因子分开统计。由零价跳出的百分比变化无定义。明细见各频率 *_source_nonpositive_close.csv。",
            "",
            "|频率|原收盘价为零行数|原收盘价为负行数|",
            "|---|---:|---:|",
        ]
        for freq, counts in quality.items():
            lines.append(
                f"|{freq}|{counts['zero_close_rows']:,}|{counts['negative_close_rows']:,}|"
            )
        lines.append("")
    (ROOT / "reports" / "数据核验报告.md").write_text("\n".join(lines))


if __name__ == "__main__":
    main()
