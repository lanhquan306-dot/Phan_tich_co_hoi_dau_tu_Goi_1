"""Investment-analysis engine for the assignment-facing web dashboard.

The module keeps numerical calculation deterministic and separate from the AI
chatbot.  It combines:

* Fundamental analysis: growth, profitability/leverage, valuation.
* Technical analysis: MA20/MA50 structure, RSI14 and volume confirmation.
* A transparent 60% fundamental + 40% technical score.
* BUY / HOLD / SELL / INSUFFICIENT-DATA signal rules.
* 3-5 year financial trend tables when the source contains enough quarters.
* Industry-aware treatment for banks, securities companies and insurers.

All scores are normalized only on criteria that are actually available.  Data
coverage is therefore reported separately and can block an investment signal
when important information is missing.
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

import chart_service as charts
import stock_service as service
import company_info_service
import news_service
import annual_report_service
from config import BANK_SYMBOLS, INSURANCE_SYMBOLS, SECURITIES_SYMBOLS
from utils import normalize_text, to_float

BASE_DIR = Path(__file__).resolve().parent
REFERENCE_PATH = BASE_DIR / "data" / "valuation_reference.json"

FUNDAMENTAL_SHARE = 0.60
TECHNICAL_SHARE = 0.40
MIN_OVERALL_COVERAGE_FOR_SIGNAL = 65.0
MIN_FUNDAMENTAL_COVERAGE_FOR_SIGNAL = 60.0
MIN_TECHNICAL_COVERAGE_FOR_SIGNAL = 75.0

REVENUE_REGULAR = [
    "doanh thu thuan ve ban hang va cung cap dich vu", "doanh thu thuan",
    "doanh thu ban hang va cung cap dich vu", "net revenue", "revenue",
]
REVENUE_FINANCIAL = [
    "tong thu nhap hoat dong", "tong doanh thu hoat dong", "thu nhap hoat dong",
    "operating income", "total operating income", "operating revenue",
]
TOTAL_ASSET_PHRASES = ["tong cong tai san", "tong tai san", "total assets"]
RATIO_PHRASES = {
    "pe": ["p/e", "pe", "pe ratio", "price earnings ratio", "p_e", "pe_ratio"],
    "pb": ["p/b", "pb", "pb ratio", "price book ratio", "p_b", "pb_ratio"],
    "roe": ["roe", "return on equity"],
    "roa": ["roa", "return on assets"],
    "eps": ["eps", "earnings per share", "earnings_per_share"],
    "bvps": ["bvps", "book value per share", "book_value_per_share"],
}


def company_type(symbol: str, industry: str = "", profile: str = "") -> str:
    """Classify financial vs non-financial firms using metadata first, ticker lists as fallback.

    Metadata makes the rule work for newly listed financial firms; curated ticker lists
    remain useful when the provider returns only a broad or missing sector label.
    """
    s = symbol.upper()
    text = normalize_text(f"{industry} {profile}")
    if s in BANK_SYMBOLS or any(k in text for k in ("ngan hang", "banking", "commercial bank", "bank ")):
        return "bank"
    if s in SECURITIES_SYMBOLS or any(k in text for k in ("chung khoan", "securities", "brokerage", "stock brokerage")):
        return "securities"
    if s in INSURANCE_SYMBOLS or any(k in text for k in ("bao hiem", "insurance")):
        return "insurance"
    if any(k in text for k in ("financials", "tai chinh", "financial service", "consumer finance")):
        return "financial"
    return "regular"


def company_type_label(kind: str) -> str:
    return {
        "bank": "Ngân hàng",
        "securities": "Chứng khoán",
        "insurance": "Bảo hiểm",
        "financial": "Doanh nghiệp tài chính khác",
        "regular": "Doanh nghiệp phi tài chính",
    }.get(kind, kind)


def _periods_back(latest: tuple[int, int], n: int) -> list[tuple[int, int]]:
    out, p = [], latest
    for _ in range(n):
        out.append(p)
        p = service._previous_quarter(*p)
    return out


def _growth_yoy(df: pd.DataFrame, phrases: list[str]) -> float | None:
    r = service._yoy_growth(df, phrases)
    return to_float(r.get("growth")) if r.get("status") == "ok" else None


def _compute_roa_ttm(income_q: pd.DataFrame, balance_q: pd.DataFrame) -> float | None:
    profit_row = service._find_report_row(income_q, service._NPAT_TOTAL_PHRASES)
    if profit_row is None:
        profit_row = service._find_report_row(income_q, service._NPAT_PARENT_PHRASES)
    asset_row = service._find_report_row(balance_q, TOTAL_ASSET_PHRASES)
    qi, qb = service._quarter_columns(income_q), service._quarter_columns(balance_q)
    if profit_row is None or asset_row is None or len(qi) < 4 or len(qb) < 4:
        return None
    latest = min(max(qi), max(qb))
    profits, assets = [], []
    for p in _periods_back(latest, 4):
        ci, cb = qi.get(p), qb.get(p)
        if not ci or not cb:
            return None
        profit, asset = to_float(profit_row.get(ci)), to_float(asset_row.get(cb))
        if profit is None or asset is None:
            return None
        profits.append(profit)
        assets.append(asset)
    avg_assets = sum(assets) / len(assets)
    return sum(profits) / avg_assets * 100 if avg_assets > 0 else None


def _load_ratio_table(symbol: str) -> pd.DataFrame:
    """Load ratio table while tolerating minor vnstock 4.x signature differences."""
    from vnstock import Fundamental

    equity = Fundamental().equity(symbol)
    attempts = [
        {"period": "quarter", "orient": "report"},
        {"period": "quarter"},
        {},
    ]
    last: Exception | None = None
    for kwargs in attempts:
        try:
            value = service.vn_call(equity.ratio, **kwargs)
            df = service.flatten_columns(pd.DataFrame(value))
            if not df.empty:
                return df
        except TypeError as exc:
            last = exc
        except Exception as exc:
            last = exc
    if last:
        raise last
    return pd.DataFrame()


def get_ratio_table(symbol: str):
    return service.cached_call(f"ratio:{symbol.upper()}", 43_200, lambda: _load_ratio_table(symbol.upper()))


def _latest_ratio_value(df: pd.DataFrame, phrases: list[str]) -> float | None:
    row = service._find_report_row(df, phrases)
    if row is not None:
        cols = service._quarter_columns(df)
        if cols:
            return to_float(row.get(cols[max(cols)]))
        for key in ("value", "ratio_value", "latest", "current"):
            for col in df.columns:
                if normalize_text(col) == key:
                    return to_float(row.get(col))

    # vnstock_data / tidy layouts may expose period/name/value rows.
    if df is None or df.empty:
        return None
    x = service.flatten_columns(df)
    name_col = next((c for c in x.columns if normalize_text(c) in {"name", "item", "indicator"}), None)
    value_col = next((c for c in x.columns if normalize_text(c) in {"value", "ratio value", "ratio_value"}), None)
    period_col = next((c for c in x.columns if normalize_text(c) == "period"), None)
    if name_col and value_col:
        keys = [normalize_text(p) for p in phrases]
        z = x.copy()
        z["__name"] = z[name_col].astype(str).map(normalize_text)
        hit = z[z["__name"].map(lambda s: any(k == s or k in s for k in keys))]
        if not hit.empty:
            if period_col:
                hit = hit.sort_values(period_col)
            return to_float(hit.iloc[-1].get(value_col))
    return None


def _pct_ratio(x: float | None) -> float | None:
    v = to_float(x)
    if v is None:
        return None
    return v * 100 if 0 < abs(v) <= 1.5 else v


def _valuation_reference(kind: str) -> dict[str, float]:
    try:
        data = json.loads(REFERENCE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    ref = data.get(kind) or data.get("default") or {}
    return {k: v for k in ("pe", "pb") if (v := to_float(ref.get(k))) is not None and v > 0}


def _load_company_profile(symbol: str) -> dict[str, str]:
    """Best-effort company metadata.

    The free vnstock v4 Unified UI exposes Reference().company(symbol).overview().
    Because schemas can vary by provider, this helper only extracts a few fields
    and silently falls back to the listing table when unavailable.
    """
    try:
        try:
            from vnstock import Reference
        except ImportError:
            from vnstock.ui import Reference

        company = Reference().company(symbol)
        method = getattr(company, "info", None) or getattr(company, "overview")
        value = service.vn_call(method)
        df = service.flatten_columns(pd.DataFrame(value))
        if df.empty:
            return {}
        row = df.iloc[0].to_dict()
        compact = {normalize_text(k).replace(" ", "_"): v for k, v in row.items()}

        def first(*names):
            for name in names:
                v = compact.get(normalize_text(name).replace(" ", "_"))
                if v is not None and str(v).strip() and str(v).lower() != "nan":
                    return str(v).strip()
            return ""

        return {
            "industry": first("industry", "industry_name", "icb_name", "sector", "sector_name"),
            "company_name": first("company_name", "name", "organ_name", "short_name"),
            "exchange": first("exchange", "com_group_code", "exchange_name"),
        }
    except Exception:
        return {}


def get_company_profile(symbol: str):
    return service.cached_call(f"profile:{symbol.upper()}", 86_400, lambda: _load_company_profile(symbol.upper()))


def _score_band(value: float | None, weight: float, bands: list[tuple[float, float]], *, higher_better=True):
    v = to_float(value)
    if v is None:
        return None
    for threshold, fraction in bands:
        if (higher_better and v >= threshold) or (not higher_better and v <= threshold):
            return weight * fraction
    return 0.0


def _growth_points(value, weight, profit=False):
    bands = [(20 if profit else 15, 1.0), (10 if profit else 5, .75), (0, .4), (-10, .15)]
    return _score_band(value, weight, bands, higher_better=True)


def _roe_points(value, weight):
    return _score_band(value, weight, [(15, 1.0), (10, .72), (5, .4), (0, .12)], higher_better=True)


def _roa_points(value, weight, kind: str):
    if kind == "bank":
        bands = [(1.5, 1.0), (1.0, .72), (.5, .4), (0, .12)]
    elif kind in {"securities", "insurance"}:
        bands = [(3.0, 1.0), (2.0, .72), (1.0, .4), (0, .12)]
    else:
        bands = [(7, 1.0), (4, .72), (2, .4), (0, .12)]
    return _score_band(value, weight, bands, higher_better=True)


def _de_points(value, weight):
    return _score_band(value, weight, [(0.5, 1.0), (1.0, .75), (1.5, .5), (2.0, .25)], higher_better=False)


def _valuation_points(value, weight, key: str, reference: float | None):
    v = to_float(value)
    if v is None or v <= 0:
        return None
    if reference and reference > 0:
        rel = v / reference
        return _score_band(rel, weight, [(0.9, 1.0), (1.1, .8), (1.3, .5), (1.6, .25)], higher_better=False)
    if key == "pe":
        return _score_band(v, weight, [(12, 1.0), (18, .75), (25, .45), (35, .2)], higher_better=False)
    return _score_band(v, weight, [(1.5, 1.0), (2.5, .75), (4.0, .4), (6.0, .2)], higher_better=False)


def _weights(kind: str) -> dict[str, float]:
    """Weights sum to 60 points (fundamental block)."""
    if kind == "regular":
        return {
            "revenue_growth": 10, "profit_growth": 12,
            "roe": 9, "roa": 6, "de": 7,
            "pe": 8, "pb": 8,
        }
    # D/E is intentionally omitted because structural leverage is not comparable.
    return {
        "revenue_growth": 9, "profit_growth": 13,
        "roe": 12, "roa": 8,
        "pe": 9, "pb": 9,
    }


def _criterion(name, value, weight, points, unit="", note="", group=""):
    return {
        "name": name, "value": value, "unit": unit, "weight": float(weight),
        "points": None if points is None else round(float(points), 2),
        "note": note, "group": group,
    }


def _annual_values(df: pd.DataFrame, phrases: list[str], *, max_years: int = 6) -> dict[int, float]:
    """Sum four quarters by year for flow items, requiring all four quarters."""
    row = service._find_report_row(df, phrases)
    cols = service._quarter_columns(df)
    if row is None or not cols:
        return {}
    years = sorted({y for y, _ in cols}, reverse=True)[:max_years]
    out: dict[int, float] = {}
    for year in years:
        vals = []
        for q in range(1, 5):
            col = cols.get((year, q))
            v = to_float(row.get(col)) if col else None
            if v is None:
                vals = []
                break
            vals.append(v)
        if vals:
            out[year] = float(sum(vals))
    return out


def _year_end_values(df: pd.DataFrame, phrases: list[str], *, max_years: int = 6) -> dict[int, float]:
    """Use Q4 as year-end value for balance-sheet items."""
    row = service._find_report_row(df, phrases)
    cols = service._quarter_columns(df)
    if row is None or not cols:
        return {}
    out: dict[int, float] = {}
    for year in sorted({y for y, _ in cols}, reverse=True)[:max_years]:
        col = cols.get((year, 4))
        v = to_float(row.get(col)) if col else None
        if v is not None:
            out[year] = v
    return out


def _annual_de(balance: pd.DataFrame, max_years=6) -> dict[int, float]:
    st = _year_end_values(balance, service._ST_DEBT_PHRASES, max_years=max_years)
    lt = _year_end_values(balance, service._LT_DEBT_PHRASES, max_years=max_years)
    eq = _year_end_values(balance, service._EQUITY_PHRASES, max_years=max_years)
    out = {}
    for y in sorted(set(st) | set(lt) | set(eq)):
        equity = eq.get(y)
        if equity is not None and equity > 0:
            out[y] = (st.get(y, 0.0) + lt.get(y, 0.0)) / equity
    return out


def _financial_history(income: pd.DataFrame, balance: pd.DataFrame, kind: str, max_years=5) -> list[dict[str, Any]]:
    revenue_phrases = REVENUE_FINANCIAL if kind != "regular" else REVENUE_REGULAR
    rev = _annual_values(income, revenue_phrases, max_years=max_years + 1)
    npat = _annual_values(income, service._NPAT_TOTAL_PHRASES, max_years=max_years + 1)
    if not npat:
        npat = _annual_values(income, service._NPAT_PARENT_PHRASES, max_years=max_years + 1)
    eq = _year_end_values(balance, service._EQUITY_PHRASES, max_years=max_years + 1)
    assets = _year_end_values(balance, TOTAL_ASSET_PHRASES, max_years=max_years + 1)
    de = {} if kind != "regular" else _annual_de(balance, max_years=max_years + 1)

    years = sorted(set(rev) | set(npat) | set(eq) | set(assets), reverse=True)[:max_years]
    rows = []
    for year in sorted(years):
        prev = year - 1
        revenue, profit = rev.get(year), npat.get(year)
        rev_growth = (revenue / rev[prev] - 1) * 100 if revenue is not None and rev.get(prev) not in (None, 0) and rev[prev] > 0 else None
        profit_growth = (profit / npat[prev] - 1) * 100 if profit is not None and npat.get(prev) not in (None, 0) and npat[prev] > 0 else None
        avg_eq = None
        if eq.get(year) is not None and eq.get(prev) is not None:
            avg_eq = (eq[year] + eq[prev]) / 2
        avg_assets = None
        if assets.get(year) is not None and assets.get(prev) is not None:
            avg_assets = (assets[year] + assets[prev]) / 2
        roe = profit / avg_eq * 100 if profit is not None and avg_eq and avg_eq > 0 else None
        roa = profit / avg_assets * 100 if profit is not None and avg_assets and avg_assets > 0 else None
        rows.append({
            "year": year,
            "revenue": revenue,
            "revenue_growth": rev_growth,
            "npat": profit,
            "profit_growth": profit_growth,
            "roe": roe,
            "roa": roa,
            "de": de.get(year),
        })
    return rows


def _economic_comments(criteria: dict[str, dict], kind: str, history: list[dict]) -> dict[str, list[str]]:
    out = {"growth": [], "profitability_leverage": [], "valuation": []}
    rg = to_float((criteria.get("revenue_growth") or {}).get("value"))
    pg = to_float((criteria.get("profit_growth") or {}).get("value"))
    if rg is not None and pg is not None:
        if rg > 0 and pg > rg + 10:
            out["growth"].append(
                f"Doanh thu/thu nhập tăng {rg:.1f}% trong khi LNST tăng {pg:.1f}%; lợi nhuận đang tăng nhanh hơn quy mô hoạt động. Cần kiểm tra thêm biên lợi nhuận và tính bền vững của mức tăng này."
            )
        elif rg > 0 and pg > 0:
            out["growth"].append(f"Doanh thu/thu nhập và LNST cùng tăng ({rg:.1f}% và {pg:.1f}%), cho thấy tăng trưởng hiện có sự đồng thuận giữa quy mô và lợi nhuận.")
        elif rg > 0 >= pg:
            out["growth"].append(f"Doanh thu/thu nhập tăng {rg:.1f}% nhưng LNST giảm {abs(pg):.1f}%; hiệu quả chuyển doanh thu thành lợi nhuận đang chịu áp lực.")
        elif rg < 0 and pg < 0:
            out["growth"].append("Doanh thu/thu nhập và LNST cùng suy giảm; cần thận trọng với sức khỏe tăng trưởng ngắn hạn.")
    elif rg is not None:
        out["growth"].append(f"Doanh thu/thu nhập thay đổi {rg:+.1f}% YoY; chưa đủ dữ liệu LNST để đánh giá chất lượng tăng trưởng đầy đủ.")

    roe = to_float((criteria.get("roe") or {}).get("value"))
    roa = to_float((criteria.get("roa") or {}).get("value"))
    de = to_float((criteria.get("de") or {}).get("value"))
    if roe is not None:
        out["profitability_leverage"].append(f"ROE TTM {roe:.1f}% phản ánh mức sinh lời trên vốn chủ sở hữu theo dữ liệu hiện có.")
    if roa is not None:
        out["profitability_leverage"].append(f"ROA TTM {roa:.1f}% cho biết hiệu quả tạo lợi nhuận trên tổng tài sản.")
    if kind == "regular" and de is not None:
        if de <= 1:
            out["profitability_leverage"].append(f"D/E {de:.2f}x cho thấy nợ vay chịu lãi chưa vượt vốn chủ sở hữu theo cách tính của hệ thống.")
        elif de > 1.5:
            out["profitability_leverage"].append(f"D/E {de:.2f}x tương đối cao; biến động lãi suất và khả năng trả nợ cần được theo dõi.")
    elif kind != "regular":
        out["profitability_leverage"].append("D/E không được dùng để chấm điểm vì đòn bẩy là đặc trưng cấu trúc của doanh nghiệp tài chính; hệ thống chuyển trọng số sang ROE, ROA và định giá.")

    pe = to_float((criteria.get("pe") or {}).get("value"))
    pb = to_float((criteria.get("pb") or {}).get("value"))
    pe_note = (criteria.get("pe") or {}).get("note", "")
    if pe is not None:
        out["valuation"].append(f"P/E hiện tại {pe:.2f}x. {pe_note}".strip())
    if pb is not None:
        out["valuation"].append(f"P/B hiện tại {pb:.2f}x. {(criteria.get('pb') or {}).get('note','')}".strip())
    if pe is not None and pe < 12:
        out["valuation"].append("P/E thấp không tự động đồng nghĩa cổ phiếu rẻ; mức thấp có thể phản ánh kỳ vọng tăng trưởng yếu hoặc rủi ro cao.")

    if len(history) >= 3:
        rev_g = [to_float(r.get("revenue_growth")) for r in history[-3:]]
        rev_g = [x for x in rev_g if x is not None]
        if len(rev_g) >= 2 and all(x > 0 for x in rev_g):
            out["growth"].append("Chuỗi năm gần đây cho thấy doanh thu/thu nhập duy trì tăng trưởng dương ở các năm có đủ dữ liệu.")
    return out


def _technical(frame: pd.DataFrame) -> dict[str, Any]:
    snap = charts.latest_snapshot(frame)
    close, ma20, ma50, rsi = (to_float(snap.get(k)) for k in ("close", "ma20", "ma50", "rsi14"))
    vol_ratio = to_float(snap.get("vol_ratio"))
    change_pct = to_float(snap.get("change_pct"))

    if None not in (close, ma20, ma50):
        if close > ma20 > ma50 and (rsi is None or rsi >= 45):
            trend = "Tích cực"
        elif close < ma20 < ma50 or (rsi is not None and rsi < 35):
            trend = "Tiêu cực"
        else:
            trend = "Trung tính"
    else:
        trend = "Chưa đủ dữ liệu"

    ma_points = None
    if None not in (close, ma20, ma50):
        if close > ma20 > ma50:
            ma_points = 24.0
        elif close < ma20 < ma50:
            ma_points = 0.0
        elif close >= ma20 or close >= ma50:
            ma_points = 14.0
        else:
            ma_points = 6.0

    rsi_points = None
    if rsi is not None:
        if 50 <= rsi <= 70:
            rsi_points = 8.0
        elif 45 <= rsi < 50 or 70 < rsi <= 75:
            rsi_points = 6.0
        elif 35 <= rsi < 45 or 75 < rsi <= 80:
            rsi_points = 3.5
        else:
            rsi_points = 1.0

    volume_points = None
    if vol_ratio is not None and change_pct is not None:
        if change_pct > 0 and vol_ratio >= 1.2:
            volume_points = 8.0
        elif change_pct >= 0 and vol_ratio >= .8:
            volume_points = 6.0
        elif change_pct < 0 and vol_ratio >= 1.2:
            volume_points = 1.0
        else:
            volume_points = 4.0

    reasons = []
    if None not in (close, ma20, ma50):
        if close > ma20 > ma50:
            reasons.append("Giá > MA20 > MA50, cấu trúc xu hướng ngắn-trung hạn đang tích cực.")
        elif close < ma20 < ma50:
            reasons.append("Giá < MA20 < MA50, cấu trúc xu hướng đang yếu.")
        else:
            reasons.append("Giá và MA20/MA50 chưa đồng thuận, xu hướng chưa rõ ràng.")
    if rsi is not None:
        if rsi > 70:
            reasons.append(f"RSI14 {rsi:.1f} cho thấy động lượng mạnh nhưng cổ phiếu có thể ở trạng thái quá mua.")
        elif rsi < 30:
            reasons.append(f"RSI14 {rsi:.1f} ở vùng quá bán; đây không phải tín hiệu mua tự động.")
        elif rsi >= 50:
            reasons.append(f"RSI14 {rsi:.1f} trên 50 hỗ trợ nhận định động lượng tích cực.")
        else:
            reasons.append(f"RSI14 {rsi:.1f} dưới 50 phản ánh động lượng còn yếu.")
    if vol_ratio is not None:
        if change_pct is not None and change_pct > 0 and vol_ratio >= 1.2:
            reasons.append(f"Khối lượng phiên gần nhất bằng {vol_ratio:.2f}x trung bình 20 phiên và đi cùng giá tăng, tạo xác nhận tốt hơn cho xu hướng.")
        elif change_pct is not None and change_pct < 0 and vol_ratio >= 1.2:
            reasons.append(f"Giá giảm với khối lượng {vol_ratio:.2f}x trung bình 20 phiên, cho thấy áp lực bán cần theo dõi.")

    return {
        "trend": trend,
        "close": close,
        "ma20": ma20,
        "ma50": ma50,
        "rsi14": rsi,
        "change_pct": change_pct,
        "volume": to_float(snap.get("volume")),
        "vol_ma20": to_float(snap.get("vol_ma20")),
        "vol_ratio": vol_ratio,
        "high_52w": to_float(snap.get("high_52w")),
        "low_52w": to_float(snap.get("low_52w")),
        "date": snap.get("time").date().isoformat() if snap.get("time") is not None else None,
        "reasons": reasons,
        "score_components": {
            "ma_trend": {"name": "Xu hướng MA20/MA50", "value": trend, "unit": "", "weight": 24.0, "points": ma_points, "group": "technical", "note": "Đánh giá cấu trúc giá, MA20 và MA50."},
            "rsi14": {"name": "RSI14", "value": rsi, "unit": "", "weight": 8.0, "points": rsi_points, "group": "technical", "note": "RSI > 70 không tự động là BUY; RSI < 30 không tự động là SELL."},
            "volume_confirmation": {"name": "Xác nhận khối lượng", "value": vol_ratio, "unit": "x", "weight": 8.0, "points": volume_points, "group": "technical", "note": "Khối lượng phiên gần nhất so với trung bình 20 phiên."},
        },
    }


def _fundamental_analysis(symbol: str, current_price: float | None, profile: dict | None = None) -> tuple[dict, list[str]]:
    notes: list[str] = []
    profile = profile or {}
    kind = company_type(symbol, str(profile.get("industry") or ""), str(profile.get("profile") or ""))
    try:
        tables, _, stale = service.get_fundamental_tables(symbol)
        if stale:
            notes.append("BCTC đang dùng cache cũ do nguồn tạm lỗi.")
    except Exception as exc:
        tables = {"income_q": pd.DataFrame(), "balance_q": pd.DataFrame(), "source": "?"}
        notes.append(f"Không tải được BCTC: {type(exc).__name__}")

    income = tables.get("income_q", pd.DataFrame())
    balance = tables.get("balance_q", pd.DataFrame())
    revenue_phrases = REVENUE_FINANCIAL if kind != "regular" else REVENUE_REGULAR
    revenue_growth = _growth_yoy(income, revenue_phrases)
    profit_growth = _growth_yoy(income, service._NPAT_PARENT_PHRASES)
    if profit_growth is None:
        profit_growth = _growth_yoy(income, service._NPAT_TOTAL_PHRASES)
    roe = service._compute_roe_ttm(income, balance)
    roa = _compute_roa_ttm(income, balance)
    de = None if kind != "regular" else service._compute_debt_equity(balance)

    ratio = pd.DataFrame()
    try:
        ratio, _, stale = get_ratio_table(symbol)
        if stale:
            notes.append("Bảng tỷ số tài chính đang dùng cache cũ.")
    except Exception as exc:
        notes.append(f"Không tải được bảng P/E, P/B: {type(exc).__name__}")

    reported_pe = _latest_ratio_value(ratio, RATIO_PHRASES["pe"])
    reported_pb = _latest_ratio_value(ratio, RATIO_PHRASES["pb"])
    ratio_roe = _pct_ratio(_latest_ratio_value(ratio, RATIO_PHRASES["roe"]))
    ratio_roa = _pct_ratio(_latest_ratio_value(ratio, RATIO_PHRASES["roa"]))
    eps = _latest_ratio_value(ratio, RATIO_PHRASES["eps"])
    bvps = _latest_ratio_value(ratio, RATIO_PHRASES["bvps"])

    if roe is None:
        roe = ratio_roe
    if roa is None:
        roa = ratio_roa

    # Current-price-relative valuation. If provider already returns P/E/P/B, it is used as fallback.
    pe = current_price / eps if current_price and eps and eps > 0 else reported_pe
    pb = current_price / bvps if current_price and bvps and bvps > 0 else reported_pb

    latest = service._latest_period(income, balance, ratio)
    ref = _valuation_reference(kind)
    w = _weights(kind)
    criteria = {
        "revenue_growth": _criterion(
            "Tăng trưởng doanh thu" if kind == "regular" else "Tăng trưởng thu nhập hoạt động",
            revenue_growth, w["revenue_growth"], _growth_points(revenue_growth, w["revenue_growth"]), "%",
            group="growth",
        ),
        "profit_growth": _criterion("Tăng trưởng LNST", profit_growth, w["profit_growth"],
                                    _growth_points(profit_growth, w["profit_growth"], profit=True), "%", group="growth"),
        "roe": _criterion("ROE TTM", roe, w["roe"], _roe_points(roe, w["roe"]), "%", group="profitability_leverage"),
        "roa": _criterion("ROA TTM", roa, w["roa"], _roa_points(roa, w["roa"], kind), "%", group="profitability_leverage"),
        "pe": _criterion("P/E", pe, w["pe"], _valuation_points(pe, w["pe"], "pe", ref.get("pe")), "x",
                         f"Tham chiếu nhóm {ref['pe']:.2f}x." if ref.get("pe") else "Chưa có tham chiếu ngành; dùng dải định giá tuyệt đối để chấm thử nghiệm.",
                         group="valuation"),
        "pb": _criterion("P/B", pb, w["pb"], _valuation_points(pb, w["pb"], "pb", ref.get("pb")), "x",
                         f"Tham chiếu nhóm {ref['pb']:.2f}x." if ref.get("pb") else "Chưa có tham chiếu ngành; dùng dải định giá tuyệt đối để chấm thử nghiệm.",
                         group="valuation"),
    }
    if kind == "regular":
        criteria["de"] = _criterion("D/E", de, w["de"], _de_points(de, w["de"]), "x", group="profitability_leverage")
    else:
        notes.append("Doanh nghiệp tài chính: không chấm D/E như doanh nghiệp phi tài chính; trọng số được chuyển sang ROE/ROA và định giá.")

    history = _financial_history(income, balance, kind, max_years=5)
    comments = _economic_comments(criteria, kind, history)

    return {
        "company_type": kind,
        "company_type_label": company_type_label(kind),
        "latest_period": latest,
        "source": tables.get("source", "?"),
        "criteria": criteria,
        "groups": {
            "growth": ["revenue_growth", "profit_growth"],
            "profitability_leverage": ["roe", "roa"] + (["de"] if kind == "regular" else []),
            "valuation": ["pe", "pb"],
        },
        "comments": comments,
        "history": history,
        "valuation_reference": ref,
        "eps": eps,
        "bvps": bvps,
        "valuation_source": {
            "pe": "Tính từ giá hiện tại / EPS" if current_price and eps and eps > 0 else "VNStock ratio table",
            "pb": "Tính từ giá hiện tại / BVPS" if current_price and bvps and bvps > 0 else "VNStock ratio table",
        },
        "classification_basis": "metadata ngành + danh sách mã dự phòng",
    }, notes


def _block_score(components: dict[str, dict], total_weight: float) -> dict[str, Any]:
    earned = sum(to_float(v.get("points"), 0.0) for v in components.values() if v.get("points") is not None)
    available = sum(to_float(v.get("weight"), 0.0) for v in components.values() if v.get("points") is not None)
    normalized = earned / available * 100 if available > 0 else None
    coverage = available / total_weight * 100 if total_weight > 0 else 0.0
    return {
        "score": None if normalized is None else round(normalized, 1),
        "earned_points": round(earned, 2),
        "available_points": round(available, 2),
        "total_points": round(total_weight, 2),
        "coverage_pct": round(coverage, 1),
    }


def _score_all(fund: dict, tech: dict) -> dict:
    f = _block_score(fund["criteria"], 60.0)
    t = _block_score(tech["score_components"], 40.0)
    if f["score"] is None or t["score"] is None:
        overall = None
    else:
        overall = FUNDAMENTAL_SHARE * f["score"] + TECHNICAL_SHARE * t["score"]

    available_global = f["available_points"] + t["available_points"]
    coverage = available_global / 100.0 * 100
    if overall is None:
        label = "Chưa đủ dữ liệu"
    elif overall >= 70:
        label = "Tích cực"
    elif overall >= 40:
        label = "Trung tính"
    else:
        label = "Tiêu cực"

    return {
        "score": None if overall is None else round(overall, 1),
        "label": label,
        "coverage_pct": round(coverage, 1),
        "fundamental": f,
        "technical": t,
        "formula": "S = 0.6 × F + 0.4 × T",
        "components": {**fund["criteria"], **tech["score_components"]},
    }


def _severe_risks(fund: dict, tech: dict) -> list[str]:
    c = fund["criteria"]
    risks = []
    pg = to_float((c.get("profit_growth") or {}).get("value"))
    rg = to_float((c.get("revenue_growth") or {}).get("value"))
    de = to_float((c.get("de") or {}).get("value"))
    if pg is not None and pg <= -30:
        risks.append("LNST giảm từ 30% YoY trở lên.")
    if rg is not None and rg <= -20:
        risks.append("Doanh thu/thu nhập giảm mạnh từ 20% YoY trở lên.")
    if fund["company_type"] == "regular" and de is not None and de >= 2.5:
        risks.append("D/E từ 2.5x trở lên, đòn bẩy ở mức cảnh báo của bộ tiêu chí.")
    if tech.get("trend") == "Tiêu cực" and (to_float(tech.get("rsi14")) or 100) < 35:
        risks.append("Xu hướng kỹ thuật tiêu cực đồng thời RSI14 suy yếu rõ rệt.")
    return risks


def _signal_from_score(score: dict, fund: dict, tech: dict) -> dict[str, Any]:
    fs = score["fundamental"]["score"]
    ts = score["technical"]["score"]
    total = score["score"]
    severe = _severe_risks(fund, tech)

    if (
        total is None
        or score["coverage_pct"] < MIN_OVERALL_COVERAGE_FOR_SIGNAL
        or score["fundamental"]["coverage_pct"] < MIN_FUNDAMENTAL_COVERAGE_FOR_SIGNAL
        or score["technical"]["coverage_pct"] < MIN_TECHNICAL_COVERAGE_FOR_SIGNAL
    ):
        return {
            "code": "NODATA", "label": "KHÔNG ĐỦ DỮ LIỆU", "tone": "neutral",
            "reason": "Độ phủ dữ liệu chưa đạt ngưỡng tối thiểu để phát BUY/HOLD/SELL.",
            "severe_risks": severe,
        }

    if total >= 70 and tech.get("trend") == "Tích cực" and not severe and (fs is None or fs >= 60):
        return {
            "code": "BUY", "label": "MUA", "tone": "positive",
            "reason": "Điểm tổng hợp đạt từ 70, nền tảng cơ bản đủ mạnh, kỹ thuật xác nhận xu hướng tích cực và không có cảnh báo nghiêm trọng.",
            "severe_risks": severe,
        }

    if total < 40 and (tech.get("trend") == "Tiêu cực" or (fs is not None and fs < 40)):
        return {
            "code": "SELL", "label": "BÁN / GIẢM TỶ TRỌNG", "tone": "negative",
            "reason": "Điểm tổng hợp dưới 40 và cơ bản hoặc kỹ thuật cho tín hiệu suy yếu rõ rệt.",
            "severe_risks": severe,
        }

    return {
        "code": "HOLD", "label": "GIỮ / CHỜ XÁC NHẬN", "tone": "warning",
        "reason": "Các tín hiệu chưa đồng thuận đủ mạnh để mua mới hoặc bán ra; ưu tiên theo dõi thêm.",
        "severe_risks": severe,
    }


def _risk_level(score: dict, fund: dict, tech: dict, severe: list[str]) -> str:
    if severe or score["coverage_pct"] < 65:
        return "Cao"
    total = to_float(score.get("score"))
    rsi = to_float(tech.get("rsi14"))
    if total is not None and total >= 70 and tech.get("trend") == "Tích cực" and not (rsi is not None and rsi > 75):
        return "Thấp - Trung bình"
    if total is not None and total < 40:
        return "Cao"
    return "Trung bình"


def _explain(fund: dict, tech: dict, score: dict, signal: dict) -> tuple[list[str], list[str], list[str], list[str]]:
    strengths, weaknesses, risks, missing = [], [], [], []
    c = fund["criteria"]

    def value(key):
        return to_float((c.get(key) or {}).get("value"))

    if (x := value("revenue_growth")) is not None:
        (strengths if x >= 10 else weaknesses if x < 0 else strengths).append(f"Tăng trưởng doanh thu/thu nhập {x:+.1f}% YoY.")
    if (x := value("profit_growth")) is not None:
        (strengths if x >= 15 else weaknesses if x < 0 else strengths).append(f"LNST tăng trưởng {x:+.1f}% YoY.")
    if (x := value("roe")) is not None:
        (strengths if x >= 15 else weaknesses).append(f"ROE TTM {x:.1f}%.")
    if (x := value("roa")) is not None and x > 0:
        strengths.append(f"ROA TTM dương {x:.1f}%.")
    if fund["company_type"] == "regular" and (x := value("de")) is not None:
        if x <= 1:
            strengths.append(f"D/E {x:.2f}x ở mức kiểm soát được theo bộ tiêu chí.")
        elif x > 1.5:
            risks.append(f"Đòn bẩy cao: D/E {x:.2f}x.")
    if (x := value("pe")) is not None and x > 25:
        risks.append(f"P/E {x:.1f}x tương đối cao theo dải đánh giá hiện tại.")
    if (x := value("pb")) is not None and x > 4:
        risks.append(f"P/B {x:.1f}x tương đối cao theo dải đánh giá hiện tại.")

    if tech["trend"] == "Tích cực":
        strengths.append("Xu hướng kỹ thuật tích cực: cấu trúc giá/MA20/MA50 đồng thuận tăng.")
    elif tech["trend"] == "Tiêu cực":
        risks.append("Xu hướng kỹ thuật tiêu cực theo MA20/MA50 hoặc RSI suy yếu.")
    else:
        weaknesses.append("Xu hướng kỹ thuật chưa tạo cấu trúc tăng rõ ràng.")
    rsi = to_float(tech.get("rsi14"))
    if rsi is not None and rsi > 70:
        risks.append(f"RSI14 {rsi:.1f}: động lượng mạnh nhưng có rủi ro quá mua/ngắn hạn.")
    if rsi is not None and rsi < 35:
        risks.append(f"RSI14 {rsi:.1f}: động lượng yếu.")
    if (vr := to_float(tech.get("vol_ratio"))) is not None and (cp := to_float(tech.get("change_pct"))) is not None:
        if cp < 0 and vr >= 1.2:
            risks.append(f"Phiên giảm đi kèm khối lượng {vr:.2f}x trung bình 20 phiên.")

    risks.extend(signal.get("severe_risks") or [])
    for key, item in score["components"].items():
        if item.get("points") is None:
            missing.append(item.get("name", key))
    if score["coverage_pct"] < 70:
        risks.append(f"Độ phủ dữ liệu chỉ {score['coverage_pct']:.0f}%; kết luận có độ tin cậy thấp hơn.")
    # de-duplicate while retaining order
    uniq = lambda xs: list(dict.fromkeys(x for x in xs if x))
    return uniq(strengths)[:7], uniq(weaknesses)[:7], uniq(risks)[:8], uniq(missing)


def _methodology(kind: str) -> dict[str, Any]:
    return {
        "formula": "S = 0.6 × F + 0.4 × T",
        "fundamental_weight": 60,
        "technical_weight": 40,
        "buy_rule": "S ≥ 70, kỹ thuật tích cực, điểm cơ bản đủ mạnh, không có cảnh báo nghiêm trọng và dữ liệu đủ phủ.",
        "hold_rule": "40 ≤ S < 70 hoặc các tín hiệu cơ bản/kỹ thuật chưa đồng thuận.",
        "sell_rule": "S < 40 và nền tảng cơ bản hoặc kỹ thuật suy yếu rõ rệt.",
        "missing_rule": "Nếu độ phủ dữ liệu dưới ngưỡng tối thiểu, hệ thống hiển thị KHÔNG ĐỦ DỮ LIỆU thay vì gán BUY/HOLD/SELL.",
        "industry_rule": "Không dùng D/E để chấm ngân hàng, chứng khoán và bảo hiểm; trọng số được chuyển sang ROE, ROA và định giá." if kind != "regular" else "D/E chỉ dùng cho doanh nghiệp phi tài chính.",
        "disclaimer": "Ngưỡng và trọng số là giả định thiết kế để kiểm thử, chưa được chứng minh tối ưu cho mọi cổ phiếu.",
    }


def analyze_investment(symbol: str) -> dict:
    s = symbol.upper().strip()
    resolution = service.resolve_symbol(s)
    if resolution.get("status") == "suggest":
        raise service.DataUnavailable(f"Không tìm thấy {s}; gợi ý: {', '.join(resolution.get('suggestions') or [])}")
    if resolution.get("status") == "ok":
        s = resolution.get("symbol", s)

    # Company metadata is loaded before fundamental scoring so sector metadata can
    # participate in the financial/non-financial classification.
    notes: list[str] = []
    profile: dict[str, Any] = {}
    try:
        profile, _, stale_profile = company_info_service.get_company_info(s)
        if stale_profile:
            notes.append("Thông tin doanh nghiệp đang dùng cache cũ.")
    except Exception as exc:
        notes.append(f"Không tải được thông tin doanh nghiệp: {type(exc).__name__}")

    frame, info = charts.build_chart_frame(s)
    tech = _technical(frame)
    profile = company_info_service.add_market_cap(profile, tech.get("close"))
    fund, fund_notes = _fundamental_analysis(s, tech.get("close"), profile)
    notes.extend(fund_notes)

    # News is contextual evidence, not a direct score input in this version.
    news: list[dict[str, str]] = []
    news_stale = False
    try:
        news, _, news_stale = news_service.get_company_news(s, 8)
        if news_stale:
            notes.append("Tin tức đang dùng cache cũ do nguồn tạm lỗi.")
    except Exception as exc:
        notes.append(f"Không tải được tin tức doanh nghiệp: {type(exc).__name__}")

    # Optional annual-report catalogue integration. The result explicitly states
    # whether arminer was actually used, avoiding false provenance claims.
    annual_report = annual_report_service.catalog_lookup(s)

    score = _score_all(fund, tech)
    signal = _signal_from_score(score, fund, tech)
    strengths, weaknesses, risks, missing = _explain(fund, tech, score, signal)
    risk_level = _risk_level(score, fund, tech, signal.get("severe_risks") or [])

    f_score = score["fundamental"]["score"]
    t_score = score["technical"]["score"]
    assessment = (
        f"{s}: tín hiệu {signal['label']}. Điểm tổng hợp "
        f"{score['score'] if score['score'] is not None else 'N/A'}/100 "
        f"(cơ bản {f_score if f_score is not None else 'N/A'}/100; kỹ thuật {t_score if t_score is not None else 'N/A'}/100). "
        f"{signal['reason']}"
    )

    industry = profile.get("industry") or company_type_label(fund["company_type"])
    company_name = profile.get("company_name") or info.get("name", "")
    exchange = profile.get("exchange") or info.get("exchange", "")
    generated_at = info.get("generated_at") or datetime.now().strftime("%H:%M %d/%m/%Y")

    # Full, human-readable provenance table. This same structure is rendered on
    # the website and appended to the PDF.
    data_sources = [
        {
            "data": "Giá, OHLCV và khối lượng",
            "source": f"VNStock ({info.get('source') or 'provider hiện có'})",
            "period": f"Đến {tech.get('date') or 'N/A'}",
            "retrieved_at": generated_at,
            "note": "Dùng cho giá gần nhất, biểu đồ nến và khối lượng; đơn vị giá được chuẩn hóa về VND.",
        },
        {
            "data": "Doanh thu/thu nhập, LNST",
            "source": f"VNStock ({fund.get('source') or 'BCTC'})",
            "period": fund.get("latest_period") or "N/A",
            "retrieved_at": generated_at,
            "note": "BCTC quý; dữ liệu năm chỉ tổng hợp khi đủ 4 quý.",
        },
        {
            "data": "Tổng tài sản, VCSH, nợ vay",
            "source": f"VNStock ({fund.get('source') or 'BCTC'})",
            "period": fund.get("latest_period") or "N/A",
            "retrieved_at": generated_at,
            "note": "Dùng tính ROE/ROA và D/E; D/E không dùng để chấm doanh nghiệp tài chính.",
        },
        {
            "data": "P/E, P/B",
            "source": "VNStock ratio table + giá hiện tại khi có EPS/BVPS",
            "period": f"Giá {tech.get('date') or 'N/A'}; BCTC {fund.get('latest_period') or 'N/A'}",
            "retrieved_at": generated_at,
            "note": f"P/E: {fund.get('valuation_source',{}).get('pe','N/A')}; P/B: {fund.get('valuation_source',{}).get('pb','N/A')}.",
        },
        {
            "data": "MA20, MA50, RSI14",
            "source": "Hệ thống tự tính từ OHLCV VNStock",
            "period": f"Đến {tech.get('date') or 'N/A'}",
            "retrieved_at": generated_at,
            "note": "MA theo giá đóng cửa; RSI14 tính từ chuỗi biến động giá theo implementation của hệ thống.",
        },
        {
            "data": "Thông tin doanh nghiệp",
            "source": profile.get("source") or "VNStock Reference",
            "period": "Thông tin tham chiếu hiện có",
            "retrieved_at": generated_at,
            "note": "Tên, ngành, mô tả hoạt động, ngày niêm yết và số cổ phiếu lưu hành khi nguồn cung cấp.",
        },
        {
            "data": "Tin tức doanh nghiệp",
            "source": "VNStock Reference (nguồn gốc từng tin được giữ riêng khi có)",
            "period": news[0].get("publish_time") if news else "Chưa có dữ liệu",
            "retrieved_at": generated_at,
            "note": "Tin tức chỉ dùng làm bối cảnh/kiểm chứng, chưa đưa trực tiếp vào điểm BUY/HOLD/SELL.",
        },
        {
            "data": "Báo cáo thường niên / dữ liệu bổ sung",
            "source": annual_report.get("source_name") or "vn-annual-report-miner (Tumiqa)",
            "period": "Theo danh mục BCTN khi arminer được kích hoạt",
            "retrieved_at": generated_at,
            "note": annual_report.get("note") or "Nguồn bổ sung tùy chọn.",
        },
    ]

    result = {
        "symbol": s,
        "company_name": company_name,
        "industry": industry,
        "exchange": exchange,
        "company_info": profile,
        "generated_at": generated_at,
        "updated_at": generated_at,
        "price_source": info.get("source", ""),
        "stale": bool(info.get("stale")),
        "fundamental": fund,
        "technical": tech,
        "score": score,
        "signal": signal,
        "risk_level": risk_level,
        "assessment": assessment,
        "investment_thesis": strengths[:3],
        "strengths": strengths,
        "weaknesses": weaknesses,
        "risks": risks,
        "missing_data": missing,
        "news": news,
        "annual_report_source": annual_report,
        "data_sources": data_sources,
        "notes": notes,
        "methodology": _methodology(fund["company_type"]),
        "disclaimer": "Kết quả phục vụ phân tích học thuật/tham khảo, không bảo đảm lợi nhuận và không phải khuyến nghị đầu tư cá nhân hóa.",
    }
    result["handoff"] = {
        "TV1": {
            "technical": tech, "score": score, "signal": signal, "assessment": assessment,
            "strengths": strengths, "weaknesses": weaknesses, "risks": risks, "news": news,
        },
        "TV4": {
            "financial_metrics": fund["criteria"], "financial_history": fund["history"],
            "company_info": profile, "data_sources": data_sources,
            "missing_data": missing, "notes": notes,
        },
    }
    return result

