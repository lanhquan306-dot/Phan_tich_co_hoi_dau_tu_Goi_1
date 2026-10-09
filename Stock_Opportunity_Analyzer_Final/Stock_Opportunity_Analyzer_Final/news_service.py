"""Company news retrieval through the Vnstock Reference unified interface."""
from __future__ import annotations

from typing import Any
import pandas as pd

import stock_service as service
from utils import normalize_text


def _clean(v: Any) -> str:
    if v is None:
        return ""
    s = str(v).strip()
    return "" if s.lower() in {"nan", "none", "nat"} else s


def _load(symbol: str, limit: int = 10) -> list[dict[str, str]]:
    try:
        from vnstock import Reference
    except ImportError:  # compatibility with some 4.x layouts
        from vnstock.ui import Reference

    company = Reference().company(symbol)
    value = None
    # detail contains source/url/summary when the provider supports it.
    try:
        value = service.vn_call(company.news, mode="detail")
    except TypeError:
        value = service.vn_call(company.news)
    except Exception:
        value = service.vn_call(company.news)

    df = service.flatten_columns(pd.DataFrame(value))
    if df.empty:
        return []
    compact = {normalize_text(c).replace(" ", "_"): c for c in df.columns}

    def col(*names):
        for n in names:
            x = compact.get(normalize_text(n).replace(" ", "_"))
            if x is not None:
                return x
        return None

    c_title = col("title", "news_title")
    c_summary = col("summary", "description", "content")
    c_time = col("publish_time", "public_date", "date", "time")
    c_source = col("source", "publisher")
    c_url = col("url", "link")
    c_category = col("category", "type")

    if c_time:
        df["__time"] = pd.to_datetime(df[c_time], errors="coerce")
        df = df.sort_values("__time", ascending=False, na_position="last")

    out: list[dict[str, str]] = []
    for _, row in df.head(max(1, limit)).iterrows():
        title = _clean(row.get(c_title)) if c_title else ""
        if not title:
            continue
        ts = _clean(row.get(c_time)) if c_time else ""
        try:
            parsed = pd.to_datetime(ts, errors="raise")
            ts = parsed.strftime("%d/%m/%Y %H:%M")
        except Exception:
            pass
        out.append({
            "title": title,
            "summary": _clean(row.get(c_summary)) if c_summary else "",
            "publish_time": ts,
            "source": _clean(row.get(c_source)) if c_source else "",
            "url": _clean(row.get(c_url)) if c_url else "",
            "category": _clean(row.get(c_category)) if c_category else "",
            "provider": "VNStock Reference",
        })
    return out


def get_company_news(symbol: str, limit: int = 8):
    # Cache a fixed wider list; slice after retrieval so the cache key stays simple.
    data, cached, stale = service.cached_call(f"company-news:{symbol.upper()}", 1800, lambda: _load(symbol.upper(), 12))
    return list(data)[:limit], cached, stale
