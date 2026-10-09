"""Company metadata service using Vnstock Reference with conservative fallbacks.

The service deliberately returns only fields actually supplied by the data source.
Missing values stay empty instead of being guessed.
"""
from __future__ import annotations

from typing import Any
import pandas as pd

import stock_service as service
from utils import normalize_text, to_float


def _first(mapping: dict[str, Any], *names: str):
    compact = {normalize_text(k).replace(" ", "_"): v for k, v in mapping.items()}
    for name in names:
        v = compact.get(normalize_text(name).replace(" ", "_"))
        if v is None:
            continue
        s = str(v).strip()
        if s and s.lower() not in {"nan", "none", "nat"}:
            return v
    return None


def _load(symbol: str) -> dict[str, Any]:
    try:
        from vnstock import Reference
    except ImportError:  # compatibility with some 4.x layouts
        from vnstock.ui import Reference

    company = Reference().company(symbol)
    df = pd.DataFrame()
    # Current Vnstock Reference exposes .info(). Keep overview() as a compatibility fallback.
    for method_name in ("info", "overview"):
        method = getattr(company, method_name, None)
        if not callable(method):
            continue
        try:
            value = service.vn_call(method)
            candidate = service.flatten_columns(pd.DataFrame(value))
            if not candidate.empty:
                df = candidate
                break
        except Exception:
            continue

    row = df.iloc[0].to_dict() if not df.empty else {}
    listing = service._safe_listing().get(symbol, {})

    issued = to_float(_first(row, "issued_share", "issued shares", "shares_outstanding", "outstanding_share"))
    return {
        "symbol": symbol,
        "company_name": str(_first(row, "name", "company_name", "organ_name", "short_name") or listing.get("name") or ""),
        "industry": str(_first(row, "sector", "industry", "industry_name", "icb_name", "sector_name") or ""),
        "exchange": str(_first(row, "exchange", "exchange_name", "com_group_code") or listing.get("exchange") or ""),
        "profile": str(_first(row, "profile", "description", "company_profile", "business_description") or ""),
        "listing_date": str(_first(row, "listing_date", "listed_date") or ""),
        "issued_share": issued,
        "website": str(_first(row, "website", "company_website", "url") or ""),
        "address": str(_first(row, "address", "head_office", "headquarter") or ""),
        "source": "VNStock Reference",
    }


def get_company_info(symbol: str):
    return service.cached_call(f"company-info:{symbol.upper()}", 86_400, lambda: _load(symbol.upper()))


def add_market_cap(info: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    out = dict(info or {})
    shares = to_float(out.get("issued_share"))
    price = to_float(current_price)
    out["market_cap"] = shares * price if shares and shares > 0 and price and price > 0 else None
    return out
