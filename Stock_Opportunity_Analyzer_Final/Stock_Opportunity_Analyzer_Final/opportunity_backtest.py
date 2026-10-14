"""Lightweight historical validation and simplified long-only simulation for the 60/40 score.

The purpose is academic validation of the rule set, not a full brokerage simulator.
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


SUPPORTED_BACKTEST_YEARS = (1, 3)


@dataclass
class BacktestSettings:
    years: int = 3
    rebalance_every: int = 20
    publication_lag_days: int = 45
    initial_capital: float = 100_000_000.0
    buy_fee_pct: float = 0.001
    sell_fee_pct: float = 0.001
    sell_tax_pct: float = 0.001
    slippage_pct: float = 0.001
    lot_size: int = 100


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


def _settings_for_years(years: int) -> BacktestSettings:
    if isinstance(years, bool):
        raise ValueError("Chỉ hỗ trợ backtest 1 năm hoặc 3 năm.")
    try:
        selected_years = int(years)
    except (TypeError, ValueError) as exc:
        raise ValueError("Chỉ hỗ trợ backtest 1 năm hoặc 3 năm.") from exc
    if (
        isinstance(years, (float, np.floating)) and not float(years).is_integer()
        or selected_years not in SUPPORTED_BACKTEST_YEARS
    ):
        raise ValueError("Chỉ hỗ trợ backtest 1 năm hoặc 3 năm.")
    return BacktestSettings(years=selected_years)


def _buy_position(cash: float, market_price: float, settings: BacktestSettings) -> dict[str, float | int]:
    execution_price = market_price * (1 + settings.slippage_pct)
    unit_cost = execution_price * (1 + settings.buy_fee_pct)
    quantity = int(cash // (unit_cost * settings.lot_size)) * settings.lot_size
    gross_value = quantity * execution_price
    commission = gross_value * settings.buy_fee_pct
    return {
        "quantity": quantity,
        "execution_price": execution_price,
        "gross_value": gross_value,
        "commission": commission,
        "slippage_cost": quantity * (execution_price - market_price),
        "cash_after": cash - gross_value - commission,
    }


def _sell_position(quantity: int, market_price: float, settings: BacktestSettings) -> dict[str, float]:
    execution_price = market_price * (1 - settings.slippage_pct)
    gross_value = quantity * execution_price
    commission = gross_value * settings.sell_fee_pct
    sell_tax = gross_value * settings.sell_tax_pct
    return {
        "execution_price": execution_price,
        "gross_value": gross_value,
        "commission": commission,
        "sell_tax": sell_tax,
        "slippage_cost": quantity * (market_price - execution_price),
        "net_proceeds": gross_value - commission - sell_tax,
    }


def run_backtest(symbol: str, years: int = 3) -> dict[str, Any]:
    settings = _settings_for_years(years)
    s = symbol.upper().strip()

    hist, _, stale = service.get_history(s)
    hist = service.confirmed_bars(hist).copy()
    hist["time"] = pd.to_datetime(hist["time"])
    hist = hist.sort_values("time").reset_index(drop=True)
    if len(hist) < 90:
        raise service.DataUnavailable("Cần ít nhất khoảng 90 phiên để chạy backtest.")

    end = hist["time"].iloc[-1]
    start_cut = end - pd.DateOffset(years=settings.years)
    first_eval = int(hist["time"].searchsorted(start_cut))
    if first_eval < 60:
        raise service.DataUnavailable(
            f"Không đủ dữ liệu để kiểm định trọn {settings.years} năm cùng giai đoạn khởi tạo chỉ báo."
        )
    if first_eval >= len(hist) - 2:
        raise service.DataUnavailable("Không đủ lịch sử giá trong khoảng backtest đã chọn.")

    try:
        tables, _, _ = service.get_fundamental_tables(s)
    except Exception:
        tables = {"income_q": pd.DataFrame(), "balance_q": pd.DataFrame(), "source": "?"}

    cash = settings.initial_capital
    shares = 0
    position = None
    trades: list[dict[str, Any]] = []
    signal_counts = {"BUY": 0, "HOLD": 0, "SELL": 0, "NODATA": 0}
    equity_curve: list[float] = []
    observations: list[dict[str, Any]] = []
    total_commission = 0.0
    total_sell_tax = 0.0
    total_slippage_cost = 0.0
    skipped_orders = 0

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
        market_price = to_float(next_row.get("open"))
        execution_date = pd.Timestamp(next_row["time"]).date().isoformat()
        if not market_price or market_price <= 0:
            skipped_orders += 1
            continue

        if code == "BUY" and shares == 0 and cash > 0:
            purchase = _buy_position(cash, market_price, settings)
            shares = int(purchase["quantity"])
            if shares == 0:
                skipped_orders += 1
                continue
            cash = float(purchase["cash_after"])
            total_commission += float(purchase["commission"])
            total_slippage_cost += float(purchase["slippage_cost"])
            position = {
                "entry_date": execution_date,
                "entry_price": float(purchase["execution_price"]),
                "entry_cost": float(purchase["gross_value"]) + float(purchase["commission"]),
                "buy_commission": float(purchase["commission"]),
                "entry_slippage_cost": float(purchase["slippage_cost"]),
            }
        elif code == "SELL" and shares > 0:
            sale = _sell_position(shares, market_price, settings)
            proceeds = float(sale["net_proceeds"])
            total_commission += float(sale["commission"])
            total_sell_tax += float(sale["sell_tax"])
            total_slippage_cost += float(sale["slippage_cost"])
            realized_return = (proceeds / position["entry_cost"] - 1) * 100
            trades.append({
                "entry_date": position["entry_date"],
                "entry_price": round(position["entry_price"], 2),
                "exit_date": execution_date,
                "exit_price": round(float(sale["execution_price"]), 2),
                "return_pct": round(realized_return, 2),
                "buy_commission": round(position["buy_commission"], 2),
                "sell_commission": round(float(sale["commission"]), 2),
                "sell_tax": round(float(sale["sell_tax"]), 2),
                "total_costs": round(
                    position["buy_commission"] + float(sale["commission"]) + float(sale["sell_tax"])
                    + position["entry_slippage_cost"] + float(sale["slippage_cost"]),
                    2,
                ),
            })
            cash += proceeds
            shares = 0
            position = None

    # Liquidate for metric calculation at the last close.
    final_close = to_float(hist.iloc[-1]["close"]) or 0.0
    if shares > 0 and final_close > 0:
        sale = _sell_position(shares, final_close, settings)
        proceeds = float(sale["net_proceeds"])
        total_commission += float(sale["commission"])
        total_sell_tax += float(sale["sell_tax"])
        total_slippage_cost += float(sale["slippage_cost"])
        realized_return = (proceeds / position["entry_cost"] - 1) * 100
        trades.append({
            "entry_date": position["entry_date"],
            "entry_price": round(position["entry_price"], 2),
            "exit_date": pd.Timestamp(hist.iloc[-1]["time"]).date().isoformat(),
            "exit_price": round(float(sale["execution_price"]), 2),
            "return_pct": round(realized_return, 2),
            "buy_commission": round(position["buy_commission"], 2),
            "sell_commission": round(float(sale["commission"]), 2),
            "sell_tax": round(float(sale["sell_tax"]), 2),
            "total_costs": round(
                position["buy_commission"] + float(sale["commission"]) + float(sale["sell_tax"])
                + position["entry_slippage_cost"] + float(sale["slippage_cost"]),
                2,
            ),
            "forced_exit_at_end": True,
        })
        cash += proceeds
        shares = 0

    strategy_return = (cash / settings.initial_capital - 1) * 100
    buy_hold_start_price = to_float(hist.iloc[first_eval + 1].get("open"))
    if buy_hold_start_price and buy_hold_start_price > 0 and final_close > 0:
        buy_hold = _buy_position(settings.initial_capital, buy_hold_start_price, settings)
        buy_hold_shares = int(buy_hold["quantity"])
        buy_hold_cash = float(buy_hold["cash_after"])
        if buy_hold_shares:
            buy_hold_cash += float(_sell_position(buy_hold_shares, final_close, settings)["net_proceeds"])
        buy_hold_return = (buy_hold_cash / settings.initial_capital - 1) * 100
    else:
        buy_hold_return = None
    wins = [t for t in trades if to_float(t.get("return_pct")) is not None and t["return_pct"] > 0]
    period_days = (pd.Timestamp(hist.iloc[-1]["time"]) - pd.Timestamp(hist.iloc[first_eval]["time"])).days
    cagr = ((cash / settings.initial_capital) ** (365.25 / period_days) - 1) * 100 if period_days > 0 and cash > 0 else None
    transaction_costs = total_commission + total_sell_tax
    total_costs = transaction_costs + total_slippage_cost

    return {
        "symbol": s,
        "years": settings.years,
        "start_date": pd.Timestamp(hist.iloc[first_eval]["time"]).date().isoformat(),
        "end_date": pd.Timestamp(hist.iloc[-1]["time"]).date().isoformat(),
        "strategy_return_pct": round(strategy_return, 2),
        "cagr_pct": None if cagr is None else round(cagr, 2),
        "buy_hold_return_pct": None if buy_hold_return is None else round(buy_hold_return, 2),
        "max_drawdown_pct": round(_max_drawdown(equity_curve), 2),
        "trades": len(trades),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "transaction_costs_vnd": round(transaction_costs, 2),
        "slippage_costs_vnd": round(total_slippage_cost, 2),
        "total_costs_vnd": round(total_costs, 2),
        "skipped_orders": skipped_orders,
        "signal_counts": signal_counts,
        "trade_log": trades[-20:],
        "observations": observations[-60:],
        "data_stale": bool(stale),
        "method_note": (
            "Vốn giả lập 100 triệu VNĐ; tín hiệu cuối phiên khớp tại giá mở cửa phiên kế tiếp, "
            "mỗi lần tái đánh giá cách nhau 20 phiên. Mô phỏng lô 100 cổ phiếu, phí mua/bán 0,10%, "
            "thuế bán 0,10% và trượt giá 0,10%. BCTC chỉ dùng tới kỳ giả định đã công bố (trễ 45 ngày); "
            "không dùng P/E, P/B hiện tại hồi tố vì có thể gây look-ahead bias."
        ),
        "limitation": (
            "Chưa tính cổ tức, giới hạn thanh khoản, biến động giá khi khớp lệnh và các sự kiện doanh nghiệp; "
            "phí/thuế/trượt giá dùng giả định cố định. Kết quả quá khứ không đảm bảo hiệu quả tương lai."
        ),
    }
