"""Reconcile one ETF's 60-minute turnover against daily source units."""

import argparse
from pathlib import Path

import pandas as pd
import pyarrow.dataset as ds

ROOT = Path(__file__).resolve().parents[1]


def reconcile(daily, minute, daily_amount_scale=1000.0):
    if (
        daily_amount_scale <= 0
        or not pd.notna(daily_amount_scale)
        or daily_amount_scale == float("inf")
    ):
        raise ValueError("Daily amount scale must be finite and positive")
    if daily.datetime.isna().any() or minute.datetime.isna().any():
        raise ValueError("Missing timestamps")
    if daily.datetime.duplicated().any():
        raise ValueError("Duplicate daily timestamps")
    if minute.datetime.duplicated().any():
        raise ValueError("Duplicate minute timestamps")
    daily = daily.set_index("datetime")[["amount", "volume"]].rename(
        columns={"amount": "amount_daily_source", "volume": "volume_daily_hands"}
    )
    minute = minute.groupby(minute.datetime.dt.normalize()).agg(
        bars=("datetime", "size"),
        amount_60min_yuan=("amount", lambda x: x.sum(min_count=len(x))),
        volume_60min_hands=("volume", lambda x: x.sum(min_count=len(x))),
    )
    result = daily.join(minute, how="outer").sort_index()
    result.index.name = "date"
    result["amount_daily_yuan"] = result.amount_daily_source * daily_amount_scale
    result["amount_diff_yuan"] = result.amount_60min_yuan - result.amount_daily_yuan
    result["amount_diff_pct"] = (
        result.amount_diff_yuan / result.amount_daily_yuan.replace(0, float("nan")) * 100
    )
    result["volume_diff_hands"] = result.volume_60min_hands - result.volume_daily_hands
    result["volume_diff_pct"] = (
        result.volume_diff_hands / result.volume_daily_hands.replace(0, float("nan")) * 100
    )
    return result


def same_turnover(left, right):
    left, right = left.reset_index(drop=True), right.reset_index(drop=True)
    # CSV parsing and Parquet may use different timestamp resolutions.
    return (
        left.datetime.astype("datetime64[ms]").equals(right.datetime.astype("datetime64[ms]"))
        and left.amount.equals(right.amount)
        and left.volume.equals(right.volume)
    )


def load(frequency, code, version=""):
    path = ROOT / frequency / version / f"ETF_{frequency}.parquet"
    return (
        ds.dataset(path)
        .to_table(
            filter=ds.field("code") == code,
            columns=["datetime", "code", "amount", "volume", "low", "high"],
        )
        .to_pandas()
        .sort_values("datetime")
        .reset_index(drop=True)
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--code", default="512100.SH", choices=["512100.SH"])
    parser.add_argument(
        "--daily-amount-scale",
        type=float,
        default=1000.0,
        choices=[1000.0],
        help="Daily source amount to yuan; this audit is restricted to the verified 512100.SH units",
    )
    args = parser.parse_args()
    if not args.code or any(c not in "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZ." for c in args.code):
        parser.error("Code must contain only uppercase letters, digits and dots")
    daily, minute = load("daily", args.code), load("60min", args.code)
    if daily.empty or minute.empty:
        raise ValueError(f"No daily or 60min data for {args.code}")
    result = reconcile(daily, minute, args.daily_amount_scale)
    folder = ROOT / "reports"
    folder.mkdir(exist_ok=True)
    output = folder / f"{args.code}_turnover_reconciliation.csv"
    result.to_csv(output, float_format="%.10f")
    sources = sorted((ROOT / "ETF日线").glob(f"*/{args.code}.csv"))
    source_note = "未找到原始日线 CSV，未验证日线转换。"
    if sources:
        raw = pd.concat(
            [pd.read_csv(p, encoding="utf-8-sig", parse_dates=["datetime"]) for p in sources]
        )
        raw = raw.sort_values("datetime").reset_index(drop=True)
        exact = same_turnover(daily, raw)
        source_note = f"日线 Parquet 与原始 CSV 的日期、成交额和成交量逐行相同：{exact}。"
    adjustment_checks = []
    for frequency, frame in [("daily", daily), ("60min", minute)]:
        for version in ["qfq", "hfq"]:
            adjusted = load(frequency, args.code, version)
            exact = same_turnover(frame, adjusted)
            adjustment_checks.append(f"{frequency}/{version} 量、额和日期与不复权一致：{exact}")
    paired = result.dropna(subset=["amount_daily_yuan", "amount_60min_yuan"])
    missing_minute = result.index[result.bars.isna()].strftime("%Y-%m-%d").tolist()
    missing_daily = result.index[result.amount_daily_source.isna()].strftime("%Y-%m-%d").tolist()
    ratios = paired.amount_60min_yuan / paired.amount_daily_source.replace(0, float("nan"))
    worst = paired.loc[paired.amount_diff_pct.abs().nlargest(12).index]
    lines = [
        f"# {args.code} 成交额核查",
        "",
        "## 结论与口径",
        "",
        f"本次换算参数：日线原始 amount × {args.daily_amount_scale:g} = 元；60min amount 按元比较。",
        "对 512100.SH，成交额比值与 amount/volume 隐含成交均价共同支持：日线为千元，分钟为元；两者 volume 均为手（100份/手）。这一结论是本地数据交叉核验结果，当前无源供应商字段说明。其他代码的单位需要另行核实。",
        "因此原始成交额不能直接跨频率求和比较。读取日线后换算成元即可，无需改写原始文件。复权价格不用于成交均价校验。",
        "",
        f"日线 {len(daily):,} 行，60min {len(minute):,} 行；共同交易日 {len(paired):,} 天。",
        f"共同日期 bar 数分布：{paired.bars.value_counts().astype(int).to_dict()}；两类数据均无重复时间戳。",
        f"非零日期 amount(60min合计)/amount(日线原始) 中位数 {ratios.median():.6f}。",
        f"缺 60min 的日线日期：{missing_minute}；缺日线的分钟日期：{missing_daily}。",
        "",
        "## 统一单位后的残差",
        "",
        "差额 = 60min 合计 − 日线 × 换算参数；百分比以换算后的日线为分母。零成交额日期不计算百分比。",
        f"绝对差异超过 0.01%：{int((paired.amount_diff_pct.abs() > 0.01).sum())} 天；超过 0.1%：{int((paired.amount_diff_pct.abs() > 0.1).sum())} 天。阈值用于筛查，不是交易所容差标准。",
        "",
        "|日期|日线换算（元）|60min合计（元）|差额（元）|差异%|成交量差（手）|",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for date, row in worst.iterrows():
        lines.append(
            f"|{date:%Y-%m-%d}|{row.amount_daily_yuan:,.2f}|{row.amount_60min_yuan:,.0f}|{row.amount_diff_yuan:,.2f}|{row.amount_diff_pct:.6f}|{row.volume_diff_hands:,.2f}|"
        )
    if pd.Timestamp("2026-01-05") in result.index:
        row = result.loc[pd.Timestamp("2026-01-05")]
        lines += [
            "",
            f"2026-01-05 示例：日线原值 {row.amount_daily_source:,.4f}，换算后 {row.amount_daily_yuan:,.2f} 元；分钟合计 {row.amount_60min_yuan:,.0f} 元；差额 {row.amount_diff_yuan:,.2f} 元（{row.amount_diff_pct:.9f}%）。",
            f"单位交叉验证：该日日线换算金额/(volume×100) = {row.amount_daily_yuan / (row.volume_daily_hands * 100):.6f} 元/份；分钟对应均价 {row.amount_60min_yuan / (row.volume_60min_hands * 100):.6f} 元/份。日线原始价格区间 {daily.loc[daily.datetime == pd.Timestamp('2026-01-05'), 'low'].iloc[0]:.3f}～{daily.loc[daily.datetime == pd.Timestamp('2026-01-05'), 'high'].iloc[0]:.3f} 元。",
        ]
    lines += [
        "",
        "## 来源与证据边界",
        "",
        source_note,
        *adjustment_checks,
        "scripts/prepare_etf.py 的复权逻辑只乘价格字段，amount、volume 原样保留。",
        "60min/manifest.json 引用的 ETF_60min_2005-now 原始 ZIP 目录当前不存在，无法逐行验证分钟 ZIP 到 Parquet 的转换。",
        "历史部分日期成交量也不一致，部分日期量一致而额不一致；不能将全部残差归为单位或整数舍入，亦不能据此认定日线或分钟哪方正确。需要供应商原始逐笔/分钟数据或权威日成交数据才能定位具体遗漏或统计口径。",
        "不按日线强行缩放分钟金额，不更改原 CSV、Parquet 或复权因子。",
        "",
        f"完整逐日对账：{output.name}",
        "",
        "复现命令：",
        "",
        f"`python scripts/audit_turnover.py --code {args.code} --daily-amount-scale {args.daily_amount_scale:g}`",
        "",
    ]
    report = folder / f"{args.code}_成交额核查.md"
    report.write_text("\n".join(lines))
    print(f"Written {output.relative_to(ROOT)} and {report.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
