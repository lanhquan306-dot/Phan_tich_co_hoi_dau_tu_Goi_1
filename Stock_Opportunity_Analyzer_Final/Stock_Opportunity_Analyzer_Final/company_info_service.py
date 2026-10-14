"""Company metadata service using Vnstock Reference + Gemini AI Fallback."""
from __future__ import annotations

import json
import os
import re
from typing import Any
import pandas as pd
from dotenv import load_dotenv

import stock_service as service
from utils import normalize_text, to_float

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
PRIMARY_MODEL = "gemini-3.5-flash-lite"
FALLBACK_MODEL = "gemini-3.8-flash"


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


def _enrich_company_with_gemini(symbol: str, company_name: str) -> dict[str, str]:
    """Tự động dùng Gemini xác định ngành chuẩn và tóm tắt hoạt động kinh doanh khi dữ liệu sàn bị thiếu."""
    if not API_KEY:
        return {"industry": "", "profile": ""}

    try:
        from google import genai
        client = genai.Client(api_key=API_KEY)
    except Exception:
        return {"industry": "", "profile": ""}

    prompt = f"""
Bạn là chuyên gia dữ liệu chứng khoán Việt Nam.
Hãy cung cấp thông tin ngắn gọn về doanh nghiệp sau:
- Mã cổ phiếu: {symbol}
- Tên công ty: {company_name or symbol}

Yêu cầu:
1. 'industry': Ngành kinh doanh cốt lõi cấp 2 (ví dụ: Bán buôn, Bán lẻ, Công nghệ thông tin, Bất động sản, Dầu khí, Ngân hàng, Thép, Hóa chất...). Rất ngắn gọn (2-4 từ).
2. 'profile': Mô tả 2-4 mảng hoạt động kinh doanh chính dưới dạng các gạch đầu dòng ngắn (ví dụ: '- Thương mại và phân phối thiết bị công nghệ. - Dịch vụ Dầu khí. - Quản lý Bất động sản.').

Chỉ trả về JSON hợp lệ:
{{
  "industry": "...",
  "profile": "..."
}}
"""
    for model_name in [PRIMARY_MODEL, FALLBACK_MODEL]:
        try:
            response = client.models.generate_content(model=model_name, contents=prompt)
            text = (response.text or "").strip()
            text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
            res = json.loads(text)
            return {
                "industry": str(res.get("industry") or "").strip(),
                "profile": str(res.get("profile") or "").strip(),
            }
        except Exception as e:
            continue

    return {"industry": "", "profile": ""}


def _fetch_business_profile_vci(symbol: str) -> str:
    """Thử lấy mô tả kinh doanh từ VCI."""
    s = symbol.upper().strip()
    try:
        from vnstock.explorer.vci.company import Company
        comp = Company(symbol=s)
        df_prof = comp.profile()
        if df_prof is not None and not df_prof.empty:
            p_row = df_prof.iloc[0].to_dict()
            for col in ["business_model", "main_service", "history_dev"]:
                val = str(p_row.get(col) or "").strip()
                if val and val.lower() not in {"nan", "none", ""}:
                    return val
    except Exception:
        pass

    try:
        from vnstock import Vnstock
        stock = Vnstock().stock(symbol=s, source='VCI')
        df_prof = stock.company.profile()
        if df_prof is not None and not df_prof.empty:
            p_row = df_prof.iloc[0].to_dict()
            for col in ["business_model", "main_service", "history_dev"]:
                val = str(p_row.get(col) or "").strip()
                if val and val.lower() not in {"nan", "none", ""}:
                    return val
    except Exception:
        pass
    return ""


def _load(symbol: str) -> dict[str, Any]:
    try:
        from vnstock import Reference
    except ImportError:
        from vnstock.ui import Reference

    company = Reference().company(symbol)
    df = pd.DataFrame()
    for method_name in ("overview", "info"):
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

# 1. Tự động lấy số cổ phiếu lưu hành chuẩn xác (Ưu tiên VCI / BCTC)
    issued = None
    try:
        from vnstock.explorer.vci.company import Company as VCICompany
        comp = service.vn_call(VCICompany, symbol)
        df_ov = service.vn_call(comp.overview)
        if df_ov is not None and not df_ov.empty:
            r_ov = df_ov.iloc[0].to_dict()
            sh = to_float(_first(r_ov, "issue_share", "issued_share", "shares_outstanding", "total_shares"))
            if sh and sh > 0:
                issued = sh if sh < 50_000_000_000 else sh / 10_000.0
    except Exception:
        pass

    if issued is None or issued <= 0:
        sh = to_float(_first(row, 
            "issue_share", "issued_share", "issued shares", "shares_outstanding", "shares_issued",
            "outstanding_share", "share_outstanding", "total_shares", "so_luong_co_phieu_luu_hanh"
        ))
        if sh and sh > 0:
            issued = sh if sh < 50_000_000_000 else sh / 10_000.0

    if issued is None or issued <= 0:
        sh = to_float(listing.get("issue_share") or listing.get("issued_share") or listing.get("shares"))
        if sh and sh > 0:
            issued = sh if sh < 50_000_000_000 else sh / 10_000.0

    if issued is None or issued <= 0:
        try:
            from vnstock.explorer.vci.financial import Finance as VCIFinance
            fin = service.vn_call(VCIFinance, symbol, period="quarter", get_all=False, show_log=False)
            bs = service.vn_call(fin._get_financial_report, "balance_sheet", period="quarter", lang="vi", dropna=False, show_log=False, limit=4)
            if bs is not None and not bs.empty:
                bs_flat = service.flatten_columns(pd.DataFrame(bs))
                for _, r in bs_flat.iterrows():
                    txt = " ".join([str(v).lower() for v in r.values])
                    if any(k in txt for k in ["von dau tu cua chu so huu", "von gop cua chu so huu", "von dieu le"]):
                        for val in reversed(list(r.values)):
                            v_num = to_float(val)
                            if v_num and v_num > 10_000_000_000:
                                issued = v_num / 10_000.0
                                break
                    if issued and issued > 0:
                        break
        except Exception:
            pass

    company_name = str(_first(row, "name", "company_name", "organ_name", "short_name") or listing.get("name") or "")

    # 2. Lấy mô tả hoạt động
    profile_desc = str(_first(row, "profile", "description", "company_profile", "business_description") or "").strip()
    if not profile_desc or profile_desc.lower() in {"nan", "none"}:
        profile_desc = _fetch_business_profile_vci(symbol)

    # 3. Lấy ngành: ưu tiên ICB cấp 2 của sàn
    industry = str(_first(row, "icb_name2", "icb_name_2") or listing.get("industry") or "").strip()

    # 4. Tự động dùng Gemini nếu thiếu mô tả hoặc thiếu ngành
    if not profile_desc or not industry or industry == "Bán lẻ":
        gemini_info = _enrich_company_with_gemini(symbol, company_name)
        if not profile_desc:
            profile_desc = gemini_info.get("profile", "")
        if (not industry or industry == "Bán lẻ") and gemini_info.get("industry"):
            industry = gemini_info["industry"]

    return {
        "symbol": symbol,
        "company_name": company_name,
        "industry": industry,
        "exchange": str(_first(row, "exchange", "exchange_name", "com_group_code") or listing.get("exchange") or ""),
        "profile": profile_desc,
        "listing_date": str(_first(row, "listing_date", "listed_date") or ""),
        "issued_share": issued,
        "website": str(_first(row, "website", "company_website", "url") or ""),
        "address": str(_first(row, "address", "head_office", "headquarter") or ""),
        "source": "VNStock Reference",
    }

def get_company_info(symbol: str):
    # Lưu vào bộ đệm RAM 24 giờ để đạt tốc độ tải tức thì
    return service.cached_call(f"company-info-v2:{symbol.upper()}", 86_400, lambda: _load(symbol.upper()))


def add_market_cap(info: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    out = dict(info or {})
    shares = to_float(out.get("issued_share"))
    price = to_float(current_price)
    out["market_cap"] = shares * price if shares and shares > 0 and price and price > 0 else None
    return out

def add_market_cap(info: dict[str, Any], current_price: float | None) -> dict[str, Any]:
    out = dict(info or {})
    shares = to_float(out.get("issued_share"))
    price = to_float(current_price)
    out["market_cap"] = shares * price if shares and shares > 0 and price and price > 0 else None
    return out


# 👉 DÁN HÀM VÀO ĐÂY (ở cuối file):
def get_industry_peer_symbols(industry_code: str, exclude_symbol: str = "") -> list[str]:
    """Lấy danh sách các mã cổ phiếu cùng nhóm ngành ICB."""
    exclude = str(exclude_symbol or "").upper().strip()
    listing = service._safe_listing()
    peers = []
    
    ind_target = str(industry_code or "").strip().lower()
    if not ind_target:
        return []

    for sym, meta in listing.items():
        if sym == exclude:
            continue
        cur_ind = str(meta.get("industry") or "").strip().lower()
        # Khớp theo ngành cấp 2 / ICB
        if ind_target in cur_ind or cur_ind in ind_target:
            peers.append(sym)
            if len(peers) >= 8:  # Giới hạn 8 mã tiêu biểu để tính toán nhanh nhất
                break

    # Dự phòng nếu không tìm thấy trong listing
    if not peers and "bán buôn" in ind_target:
        peers = [s for s in ["DGW", "PLX", "PSD", "SMC", "TLH"] if s != exclude]

    return peers