"""Calendar-time CAGR; risk statistics consistently use daily closing equity."""

import numpy as np
import pandas as pd

from .engine import BacktestResult


def daily_equity(result: BacktestResult) -> pd.Series:
    return result.ledger.equity.groupby(result.ledger.index.normalize()).last()


def daily_returns(result: BacktestResult) -> pd.Series:
    equity = daily_equity(result)
    previous = equity.shift(1)
    previous.iloc[0] = result.config.initial_cash
    return equity / previous - 1


def drawdown(result: BacktestResult) -> pd.Series:
    equity = daily_equity(result)
    peak = equity.cummax().clip(lower=result.config.initial_cash)
    return equity / peak - 1


def period_returns(result: BacktestResult, period="M") -> pd.Series:
    r = daily_returns(result)
    return (1 + r).groupby(r.index.to_period(period)).prod() - 1


def performance(result: BacktestResult) -> dict:
    eq, ret, dd = daily_equity(result), daily_returns(result), drawdown(result)
    years = max((eq.index[-1] - eq.index[0]).days + 1, 1) / 365.25
    total = eq.iloc[-1] / result.config.initial_cash - 1
    annual = (1 + total) ** (1 / years) - 1 if years >= 1 / 365.25 else np.nan
    sd = ret.std(ddof=1)
    downside = np.sqrt(np.mean(np.minimum(ret, 0) ** 2))
    closed = (
        result.trades[result.trades.status == "closed"]
        if not result.trades.empty
        else result.trades
    )
    win = closed.loc[closed.pnl > 0, "pnl"] if not closed.empty else pd.Series(dtype=float)
    loss = closed.loc[closed.pnl < 0, "pnl"] if not closed.empty else pd.Series(dtype=float)
    duration, longest = 0, 0
    for x in dd:
        duration = duration + 1 if x < -1e-12 else 0
        longest = max(longest, duration)
    trough = dd.idxmin()
    recovery = eq.loc[trough:][dd.loc[trough:] >= -1e-12]
    rec_days = (
        (recovery.index[0] - trough).days
        if len(recovery) and dd.min() < 0
        else (0 if dd.min() == 0 else np.nan)
    )
    fills = result.fills
    commission = fills.commission.sum() if len(fills) else 0.0
    slip = fills.slippage_cost.sum() if len(fills) else 0.0
    return {
        "start": str(eq.index[0].date()),
        "end": str(eq.index[-1].date()),
        "trading_days": len(eq),
        "total_return": total,
        "annual_return": annual,
        "volatility": sd * np.sqrt(252),
        "sharpe": ret.mean() / sd * np.sqrt(252) if sd > 0 else np.nan,
        "sortino": ret.mean() / downside * np.sqrt(252) if downside > 0 else np.nan,
        "max_drawdown": dd.min(),
        "calmar": annual / abs(dd.min()) if dd.min() < 0 else np.nan,
        "longest_underwater_sessions": longest,
        "trough_to_recovery_calendar_days": rec_days,
        "drawdown_unrecovered": bool(dd.iloc[-1] < -1e-12),
        "closed_trades": len(closed),
        "win_rate": len(win) / len(closed) if len(closed) else np.nan,
        "payoff_ratio": win.mean() / abs(loss.mean()) if len(win) and len(loss) else np.nan,
        "profit_factor": win.sum() / abs(loss.sum()) if len(loss) else np.nan,
        "average_holding_days": closed.holding_days.mean() if len(closed) else np.nan,
        "annual_turnover": fills.notional.sum() / eq.mean() / years if len(fills) else 0.0,
        "average_exposure": result.ledger.exposure.mean(),
        "commission_yuan": commission,
        "slippage_yuan": slip,
        "explicit_cost_over_initial": (commission + slip) / result.config.initial_cash,
        "max_participation": fills.participation.max() if len(fills) else 0.0,
        "final_equity": eq.iloc[-1],
    }
