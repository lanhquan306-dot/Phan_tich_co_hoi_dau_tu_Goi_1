"""Lightweight historical validation for the 60/40 opportunity score.

The purpose is academic validation of the rule set, not a trading simulator.
Important safeguards:
- Signals are evaluated on close of day t and executed at next trading day's open.
- Fundamental statements are truncated to the latest quarter assumed published
  by day t (quarter-end + publication lag) to reduce look-ahead bias.
- Historical P/E and P/B are deliberately omitted when reliable point-in-time
  valuation data are unavailable; the fundamental block is normalized over the
  other available criteria and the lower coverage is reported.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from typing import Any

import numpy as np
import pandas as pd

import chart_service as charts
import investment_analysis as ia
import stock_service as service
from utils import to_float


@dataclass
class BacktestSettings:
    years: int = 3
    rebalance_every: int = 20
    publication_lag_days: int = 45
    initial_capital: float = 100.0


def _quarter_end(year: int, quarter: int) -> date:
    return (pd.Timestamp(year=year, month=quarter * 3, day=1) + pd.offsets.MonthEnd(0)).date()


def _available_quarter(day: date, lag_days: int) -> tuple[int, int]:
    y, q = day.year, (day.month - 1) // 3 + 1
    while _quarter_end(y, q) + timedelta(days=lag_days) > day:
        y, q = (y, q - 1) if q > 1 else (y - 1, 4)
    return y, q


def _fundamental_asof(symbol: str, tables: dict, day: date, lag_days: int) -> dict[str, Any]:
    kind = ia.company_type(symbol)
    upto = _available_quarter(day, lag_days)
    income = service.truncate_quarters(tables.get("income_q", pd.DataFrame()), upto)
    balance = service.truncate_quarters(tables.get("balance_q", pd.DataFrame()), upto)
    revenue_phrases = ia.REVENUE_FINANCIAL if kind != "regular" else ia.REVENUE_REGULAR

    revenue_growth = ia._growth_yoy(income, revenue_phrases)
    profit_growth = ia._growth_yoy(income, service._NPAT_PARENT_PHRASES)
    if profit_growth is None:
        profit_growth = ia._growth_yoy(income, service._NPAT_TOTAL_PHRASES)
    roe = service._compute_roe_ttm(income, balance)
    roa = ia._compute_roa_ttm(income, balance)
    de = None if kind != "regular" else service._compute_debt_equity(balance)
    w = ia._weights(kind)

    criteria = {
        "revenue_growth": ia._criterion("Tăng trưởng doanh thu/thu nhập", revenue_growth, w["revenue_growth"],
                                        ia._growth_points(revenue_growth, w["revenue_growth"]), "%", group="growth"),
        "profit_growth": ia._criterion("Tăng trưởng LNST", profit_growth, w["profit_growth"],
                                       ia._growth_points(profit_growth, w["profit_growth"], profit=True), "%", group="growth"),
        "roe": ia._criterion("ROE TTM", roe, w["roe"], ia._roe_points(roe, w["roe"]), "%", group="profitability_leverage"),
        "roa": ia._criterion("ROA TTM", roa, w["roa"], ia._roa_points(roa, w["roa"], kind), "%", group="profitability_leverage"),
        # Point-in-time valuation is not reconstructed from current ratios.
        "pe": ia._criterion("P/E", None, w["pe"], None, "x", "Không dùng trong backtest nếu thiếu dữ liệu định giá lịch sử.", "valuation"),
        "pb": ia._criterion("P/B", None, w["pb"], None, "x", "Không dùng trong backtest nếu thiếu dữ liệu định giá lịch sử.", "valuation"),
    }
    if kind == "regular":
        criteria["de"] = ia._criterion("D/E", de, w["de"], ia._de_points(de, w["de"]), "x", group="profitability_leverage")
    return {
        "company_type": kind,
        "criteria": criteria,
        "latest_period": service._latest_period(income, balance),
    }


def _max_drawdown(values: list[float]) -> float:
    if not values:
        return 0.0
    arr = np.asarray(values, dtype=float)
    peak = np.maximum.accumulate(arr)
    dd = arr / peak - 1.0
    return float(dd.min() * 100)


def run_backtest(symbol: str, years: int = 3) -> dict[str, Any]:
    settings = BacktestSettings(years=max(1, min(int(years), 5)))
    s = symbol.upper().strip()

    hist, _, stale = service.get_history(s)
    hist = service.confirmed_bars(hist).copy()
    hist["time"] = pd.to_datetime(hist["time"])
    hist = hist.sort_values("time").reset_index(drop=True)
    if len(hist) < 90:
        raise service.DataUnavailable("Cần ít nhất khoảng 90 phiên để chạy backtest.")

    end = hist["time"].iloc[-1]
    start_cut = end - pd.DateOffset(years=settings.years)
    first_eval = max(60, int(hist["time"].searchsorted(start_cut)))
    if first_eval >= len(hist) - 2:
        raise service.DataUnavailable("Không đủ lịch sử giá trong khoảng backtest đã chọn.")

    try:
        tables, _, _ = service.get_fundamental_tables(s)
    except Exception:
        tables = {"income_q": pd.DataFrame(), "balance_q": pd.DataFrame(), "source": "?"}

    cash = settings.initial_capital
    shares = 0.0
    entry_price = None
    entry_date = None
    trades: list[dict[str, Any]] = []
    signal_counts = {"BUY": 0, "HOLD": 0, "SELL": 0, "NODATA": 0}
    equity_curve: list[float] = []
    observations: list[dict[str, Any]] = []

    eval_indices = set(range(first_eval, len(hist) - 1, settings.rebalance_every))
    for i in range(first_eval, len(hist)):
        close = to_float(hist.iloc[i]["close"]) or 0.0
        equity_curve.append(cash + shares * close)
        if i not in eval_indices:
            continue

        frame = charts.compute_indicators(hist.iloc[: i + 1])
        tech = ia._technical(frame)
        fund = _fundamental_asof(s, tables, pd.Timestamp(hist.iloc[i]["time"]).date(), settings.publication_lag_days)
        score = ia._score_all(fund, tech)
        signal = ia._signal_from_score(score, fund, tech)
        code = signal["code"]
        signal_counts[code] = signal_counts.get(code, 0) + 1
        observations.append({
            "date": pd.Timestamp(hist.iloc[i]["time"]).date().isoformat(),
            "score": score.get("score"),
            "fundamental_score": score["fundamental"].get("score"),
            "technical_score": score["technical"].get("score"),
            "signal": code,
            "coverage": score.get("coverage_pct"),
        })

        next_row = hist.iloc[i + 1]
        execution_price = to_float(next_row.get("open")) or to_float(next_row.get("close"))
        execution_date = pd.Timestamp(next_row["time"]).date().isoformat()
        if not execution_price or execution_price <= 0:
            continue

        if code == "BUY" and shares == 0 and cash > 0:
            shares = cash / execution_price
            cash = 0.0
            entry_price = execution_price
            entry_date = execution_date
        elif code == "SELL" and shares > 0:
            proceeds = shares * execution_price
            ret = (execution_price / entry_price - 1) * 100 if entry_price else None
            trades.append({
                "entry_date": entry_date,
                "entry_price": entry_price,
                "exit_date": execution_date,
                "exit_price": execution_price,
                "return_pct": ret,
            })
            cash = proceeds
            shares = 0.0
            entry_price = None
            entry_date = None

    # Liquidate for metric calculation at the last close.
    final_close = to_float(hist.iloc[-1]["close"]) or 0.0
    if shares > 0 and final_close > 0:
        ret = (final_close / entry_price - 1) * 100 if entry_price else None
        trades.append({
            "entry_date": entry_date,
            "entry_price": entry_price,
            "exit_date": pd.Timestamp(hist.iloc[-1]["time"]).date().isoformat(),
            "exit_price": final_close,
            "return_pct": ret,
            "forced_exit_at_end": True,
        })
        cash = shares * final_close
        shares = 0.0

    strategy_return = (cash / settings.initial_capital - 1) * 100
    start_price = to_float(hist.iloc[first_eval + 1].get("open")) or to_float(hist.iloc[first_eval + 1].get("close"))
    buy_hold_return = (final_close / start_price - 1) * 100 if start_price and final_close else None
    wins = [t for t in trades if to_float(t.get("return_pct")) is not None and t["return_pct"] > 0]

    return {
        "symbol": s,
        "years": settings.years,
        "start_date": pd.Timestamp(hist.iloc[first_eval]["time"]).date().isoformat(),
        "end_date": pd.Timestamp(hist.iloc[-1]["time"]).date().isoformat(),
        "strategy_return_pct": round(strategy_return, 2),
        "buy_hold_return_pct": None if buy_hold_return is None else round(buy_hold_return, 2),
        "max_drawdown_pct": round(_max_drawdown(equity_curve), 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "signal_counts": signal_counts,
        "trade_log": trades[-20:],
        "observations": observations[-60:],
        "data_stale": bool(stale),
        "method_note": (
            "Mô phỏng kiểm tra quy tắc: tín hiệu tại cuối phiên, khớp ở giá mở cửa phiên kế tiếp; "
            "BCTC chỉ dùng tới kỳ được giả định đã công bố (trễ 45 ngày). P/E và P/B lịch sử không được "
            "dùng nếu không có dữ liệu point-in-time, vì dùng tỷ số hiện tại sẽ gây look-ahead bias."
        ),
        "limitation": "Backtest đơn giản chưa tính phí giao dịch, thuế, trượt giá, cổ tức và thanh khoản thực tế; không chứng minh hiệu quả tương lai.",
    }
