"""Cash/share event ledger. Signals observed at close can execute only later."""

import math
from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class BacktestConfig:
    initial_cash: float = 1_000_000.0
    commission: float = 0.00005
    minimum_commission: float = 0.0
    slippage: float = 0.0001
    lot_size: int = 100
    start: str | None = None
    end: str = "2026-09-30"
    max_participation: float | None = None
    intraday_price: str | None = None

    def __post_init__(self):
        if self.intraday_price not in (None, "vwap", "high", "extreme"):
            raise ValueError("intraday_price must be vwap, high or extreme")
        if not np.isfinite(self.initial_cash) or self.initial_cash <= 0:
            raise ValueError("initial_cash must be finite and positive")
        if self.lot_size < 1 or int(self.lot_size) != self.lot_size:
            raise ValueError("lot_size must be a positive integer")
        if any(
            not np.isfinite(x) or x < 0
            for x in [self.commission, self.minimum_commission, self.slippage]
        ):
            raise ValueError("Costs must be finite and nonnegative")
        if self.slippage >= 1:
            raise ValueError("slippage must be below one")
        if self.max_participation is not None and not 0 < self.max_participation <= 1:
            raise ValueError("max_participation must be in (0,1]")


@dataclass
class BacktestResult:
    ledger: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    trades: pd.DataFrame
    actions: pd.DataFrame
    config: BacktestConfig


def window_start(ts: pd.Timestamp, frequency: str) -> pd.Timestamp:
    if frequency == "daily":
        return ts.normalize() + pd.Timedelta(hours=9, minutes=30)
    from .frequency import minute_count

    n = minute_count(frequency)
    clock = ts.hour * 60 + ts.minute
    base = 570 if 570 < clock <= 690 else 780 if 780 < clock <= 900 else None
    if base is None or (clock - base) % n and clock not in (690, 900):
        raise ValueError(f"Unexpected bar close {ts} for {frequency}")
    start = base + ((clock - base - 1) // n) * n
    return ts.normalize() + pd.Timedelta(minutes=start)


def validate_events(events: pd.DataFrame | None) -> list[dict]:
    if events is None or events.empty:
        return []
    result = []
    for i, raw in enumerate(events.to_dict("records")):
        e = {k: v for k, v in raw.items() if pd.notna(v)}
        if e.get("verified") not in (True, "true", "True", 1):
            raise ValueError(f"Unverified corporate action: {e}")
        if e.get("kind") not in ("split", "dividend"):
            raise ValueError("Unknown corporate action")
        for key in ["ex_date", "record_date", "pay_date", "suspension_date"]:
            if key in e:
                e[key] = pd.Timestamp(e[key]).normalize()
        if "ex_date" not in e:
            raise ValueError("Corporate action needs ex_date")
        if e["kind"] == "split" and (not np.isfinite(e.get("ratio", np.nan)) or e["ratio"] <= 0):
            raise ValueError("Invalid split ratio")
        if e["kind"] == "dividend":
            if not (
                e.get("record_date", pd.Timestamp.max)
                < e["ex_date"]
                <= e.get("pay_date", pd.Timestamp.min)
            ):
                raise ValueError("Invalid dividend chronology")
            if not np.isfinite(e.get("cash_per_share", np.nan)) or e["cash_per_share"] < 0:
                raise ValueError("Invalid dividend amount")
        e["id"] = str(e.get("id", i))
        result.append(e)
    return result


def run_backtest(
    bars: pd.DataFrame,
    targets: pd.Series,
    config: BacktestConfig | None = None,
    events: pd.DataFrame | None = None,
) -> BacktestResult:
    c = config or BacktestConfig()
    price_model = c.intraday_price or "vwap"
    if not bars.index.is_monotonic_increasing or bars.index.has_duplicates:
        raise ValueError("Bars must have sorted unique timestamps")
    if not targets.index.equals(bars.index):
        raise ValueError("Targets must exactly align with bars")
    if not targets.dropna().isin([0, 1]).all():
        raise ValueError("Only 0/1 targets are supported")
    es = validate_events(events)
    start = pd.Timestamp(c.start) if c.start else bars.index.min().normalize()
    end = pd.Timestamp(c.end) + pd.Timedelta(days=1)
    mask = (bars.index >= start) & (bars.index < end)
    d, tsignals = bars.loc[mask].copy(), targets.loc[mask]
    if d.empty:
        raise ValueError("Empty backtest interval")
    frequency = d.frequency.iloc[0]
    from .frequency import normalize_frequency

    normalize_frequency(frequency)
    if d.frequency.nunique() != 1:
        raise ValueError("One supported frequency required")
    defaults = {
        "valid": True,
        "mark_close": np.nan,
        "limit_up": np.nan,
        "limit_down": np.nan,
        "suspended": False,
    }
    for key, value in defaults.items():
        if key not in d:
            d[key] = value
    d["target"] = tsignals
    d["capacity_volume"] = (
        bars.volume.shift(1).reindex(d.index) if frequency == "daily" else d.volume
    )
    suspension_days = {e["suspension_date"] for e in es if "suspension_date" in e}
    cash, shares, locked, mark = c.initial_cash, 0, 0, np.nan
    pending, signal_time, current_day = 0, None, None
    receipts, entitlements = {}, {}
    ledger, orders, fills, trades, action_log = [], [], [], [], []
    current_trade = None
    ex_map, pay_map, record_map = {}, {}, {}
    for e in es:
        ex_map.setdefault(e["ex_date"], []).append(e)
        if e["kind"] == "dividend":
            pay_map.setdefault(e["pay_date"], []).append(e)
            record_map.setdefault(e["record_date"], []).append(e)

    def fee(notional):
        return max(c.minimum_commission, abs(notional) * c.commission) if notional else 0.0

    rows = list(d.itertuples())
    for i, row in enumerate(rows):
        ts, day = row.Index, row.Index.normalize()
        wstart = window_start(ts, frequency)
        timestamp = wstart if frequency == "daily" else ts
        close_time = day + pd.Timedelta(hours=15) if frequency == "daily" else ts
        if day != current_day:
            locked = 0
            current_day = day
            for e in ex_map.get(day, []):
                if e["kind"] == "split":
                    old_shares = shares
                    exact = shares * float(e["ratio"])
                    shares = (
                        math.ceil(exact - 1e-10)
                        if e.get("rounding") == "ceil"
                        else math.floor(exact + 1e-10)
                    )
                    if np.isfinite(mark):
                        mark /= float(e["ratio"])
                    if current_trade is not None:
                        current_trade["split_ratio"] *= float(e["ratio"])
                    action_log.append(
                        {
                            "date": day,
                            "kind": "split",
                            "shares_before": old_shares,
                            "shares_after": shares,
                            "cash": 0.0,
                            "event_id": e["id"],
                        }
                    )
                else:
                    entitled, trade_id = entitlements.get(e["id"], (0, None))
                    value = entitled * float(e["cash_per_share"])
                    receipts[e["id"]] = value
                    if trade_id is not None:
                        trades[trade_id]["dividends"] += value
                    if np.isfinite(mark):
                        mark -= float(e["cash_per_share"])
                    action_log.append(
                        {
                            "date": day,
                            "kind": "dividend_accrual",
                            "cash": value,
                            "event_id": e["id"],
                        }
                    )
            for e in pay_map.get(day, []):
                value = receipts.pop(e["id"], 0.0)
                cash += value
                action_log.append(
                    {"date": day, "kind": "dividend_payment", "cash": value, "event_id": e["id"]}
                )

        valid = bool(row.valid) and all(
            np.isfinite(x) and x > 0 for x in (row.open, row.close, row.high, row.low)
        )
        tradable = (
            valid
            and np.isfinite(row.volume)
            and row.volume > 0
            and np.isfinite(row.amount)
            and row.amount > 0
        )
        open_tradable = (
            np.isfinite(row.open)
            and row.open > 0
            and not row.suspended
            and day not in suspension_days
        )
        execution_allowed = (
            open_tradable
            if frequency == "daily"
            else tradable and not row.suspended and day not in suspension_days
        )
        # Orders are fixed by an earlier close; future window volume/price is execution data only.
        side = (
            "buy"
            if pending == 1 and shares == 0
            else "sell"
            if pending == 0 and shares > 0
            else None
        )
        if side is not None and signal_time is not None:
            reference = (
                row.open
                if frequency == "daily"
                else (
                    (row.high if side == "buy" else row.low)
                    if price_model == "extreme"
                    else row.high
                    if price_model == "high"
                    else row.amount / row.volume
                )
                if tradable
                else np.nan
            )
            reason = ""
            if not execution_allowed:
                reason = "missing_or_no_trade"
            elif (
                frequency != "daily"
                and price_model == "vwap"
                and not row.low - 0.001 <= reference <= row.high + 0.001
            ):
                reason = "invalid_vwap"
            elif side == "sell" and shares - locked <= 0:
                reason = "t_plus_one"
            elif (
                side == "buy"
                and np.isfinite(row.limit_up)
                and (
                    reference >= row.limit_up - 1e-9
                    if frequency == "daily"
                    else row.low >= row.limit_up - 1e-9
                )
            ):
                reason = "limit_up"
            elif (
                side == "sell"
                and np.isfinite(row.limit_down)
                and (
                    reference <= row.limit_down + 1e-9
                    if frequency == "daily"
                    else row.high <= row.limit_down + 1e-9
                )
            ):
                reason = "limit_down"
            if not reason:
                execution = reference * (1 + c.slippage if side == "buy" else 1 - c.slippage)
                if side == "buy":
                    q = (
                        math.floor(cash / (execution * (1 + c.commission)) / c.lot_size)
                        * c.lot_size
                    )
                    while q > 0 and q * execution + fee(q * execution) > cash + 1e-8:
                        q -= c.lot_size
                else:
                    q = shares - locked
                if q <= 0:
                    reason = "insufficient_cash_or_lot"
                elif c.max_participation is not None and (
                    not np.isfinite(row.capacity_volume)
                    or q > row.capacity_volume * c.max_participation
                ):
                    reason = "capacity_limit"
                else:
                    commission, notional = fee(q * execution), q * execution
                    if side == "buy":
                        cash -= notional + commission
                        shares += q
                        locked += q
                        current_trade = {
                            "trade_id": len(trades),
                            "entry_time": timestamp,
                            "exit_time": pd.NaT,
                            "entry_cost": notional + commission,
                            "exit_proceeds": 0.0,
                            "dividends": 0.0,
                            "initial_quantity": q,
                            "split_ratio": 1.0,
                            "status": "open",
                        }
                        trades.append(current_trade)
                    else:
                        cash += notional - commission
                        shares -= q
                        current_trade["exit_proceeds"] += notional - commission
                        if shares == 0:
                            current_trade["exit_time"] = timestamp
                            current_trade["status"] = "closed"
                            current_trade = None
                    fills.append(
                        {
                            "timestamp": timestamp,
                            "window_start": wstart,
                            "window_end": close_time if frequency != "daily" else wstart,
                            "signal_time": signal_time,
                            "side": side,
                            "quantity": q,
                            "reference_price": reference,
                            "execution_price": execution,
                            "notional": notional,
                            "commission": commission,
                            "slippage_cost": q * abs(execution - reference),
                            "participation": q / row.volume if row.volume > 0 else np.nan,
                        }
                    )
                    reason = "filled"
            orders.append(
                {
                    "timestamp": timestamp,
                    "signal_time": signal_time,
                    "target": pending,
                    "side": side,
                    "reason": reason,
                }
            )

        if tradable:
            mark = row.close
        elif np.isfinite(row.mark_close) and row.mark_close > 0:
            mark = row.mark_close
        elif not np.isfinite(mark) and valid:
            mark = row.close
        receivable = sum(receipts.values())
        equity = cash + (shares * mark if shares else 0.0) + receivable
        if cash < -1e-6 or shares < 0 or locked > shares or not np.isfinite(equity):
            raise AssertionError("Accounting invariant violated")
        if valid and np.isfinite(row.target):
            new_target = int(row.target)
            if new_target != pending:
                orders.append(
                    {
                        "timestamp": close_time,
                        "signal_time": close_time,
                        "target": new_target,
                        "side": "signal",
                        "reason": "target_changed",
                    }
                )
            pending, signal_time = new_target, close_time
        ledger.append(
            {
                "timestamp": close_time,
                "cash": cash,
                "shares": shares,
                "sellable": shares - locked,
                "receivable": receivable,
                "mark": mark,
                "equity": equity,
                "target": pending,
                "exposure": shares * mark / equity if shares else 0.0,
                "valid": valid,
                "tradable": tradable,
            }
        )
        if i == len(rows) - 1 or rows[i + 1].Index.normalize() != day:
            for e in record_map.get(day, []):
                entitlements[e["id"]] = (
                    shares,
                    current_trade["trade_id"] if current_trade else None,
                )

    if (pending == 1 and shares == 0) or (pending == 0 and shares > 0):
        orders.append(
            {
                "timestamp": ledger[-1]["timestamp"],
                "signal_time": signal_time,
                "target": pending,
                "side": "pending",
                "reason": "end_of_range",
            }
        )
    for trade in trades:
        remaining = shares * mark if trade["status"] == "open" else 0.0
        trade["pnl"] = trade["exit_proceeds"] + remaining + trade["dividends"] - trade["entry_cost"]
        trade["return"] = trade["pnl"] / trade["entry_cost"]
        exit_time = trade["exit_time"] if trade["status"] == "closed" else ledger[-1]["timestamp"]
        trade["holding_days"] = (exit_time - trade["entry_time"]).total_seconds() / 86400
    led = pd.DataFrame(ledger).set_index("timestamp")
    led["net_value"] = led.equity / c.initial_cash
    return BacktestResult(
        led,
        pd.DataFrame(orders, columns=["timestamp", "signal_time", "target", "side", "reason"]),
        pd.DataFrame(
            fills,
            columns=[
                "timestamp",
                "window_start",
                "window_end",
                "signal_time",
                "side",
                "quantity",
                "reference_price",
                "execution_price",
                "notional",
                "commission",
                "slippage_cost",
                "participation",
            ],
        ),
        pd.DataFrame(
            trades,
            columns=[
                "trade_id",
                "entry_time",
                "exit_time",
                "entry_cost",
                "exit_proceeds",
                "dividends",
                "initial_quantity",
                "split_ratio",
                "status",
                "pnl",
                "return",
                "holding_days",
            ],
        ),
        pd.DataFrame(action_log),
        c,
    )
