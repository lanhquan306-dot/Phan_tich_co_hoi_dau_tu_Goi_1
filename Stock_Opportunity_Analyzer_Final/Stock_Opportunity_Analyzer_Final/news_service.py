"""Company news retrieval via Financial RSS feeds + Google News RSS + Gemini Analysis."""
from __future__ import annotations

import html
import json
import os
import re
import ssl
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime
from email.utils import parsedate_to_datetime
from typing import Any

from dotenv import load_dotenv
from google import genai

from utils import normalize_text

load_dotenv()
API_KEY = os.getenv("GEMINI_API_KEY")
MODEL_NAME = "gemini-3.8-flash"

client = genai.Client(api_key=API_KEY) if API_KEY else None

# Tạo context bỏ qua lỗi chứng chỉ SSL trên Windows
SSL_CTX = ssl.create_default_context()
SSL_CTX.check_hostname = False
SSL_CTX.verify_mode = ssl.CERT_NONE

RSS_SOURCES = {
    "CafeF": "https://cafef.vn/thi-truong-chung-khoan.rss",
    "Vietstock": "https://vietstock.vn/733/doanh-nghiep.rss",
    "TinNhanhChungKhoan": "https://www.tinnhanhchungkhoan.vn/rss/doanh-nghiep.rss",
}

COMPANY_KEYWORDS = {
    "PET": ["PET", "Petrosetco", "Dịch vụ Tổng hợp Dầu khí"],
    "FPT": ["FPT", "FPT Corporation", "FPT Telecom", "FPT Software"],
    "MWG": ["MWG", "Thế Giới Di Động", "Điện Máy Xanh", "Bách Hóa Xanh"],
    "VIC": ["VIC", "Vingroup", "Vincom", "Vinhomes", "VinFast"],
    "HPG": ["HPG", "Hòa Phát", "Tập đoàn Hòa Phát"],
    "SSI": ["SSI", "Chứng khoán SSI"],
    "VND": ["VND", "VNDirect", "Chứng khoán VNDirect"],
}

_NEWS_CACHE = {}
_NEWS_CACHE_LOCK = threading.Lock()
_NEWS_CACHE_TTL = 1800  # 30 phút


def clean_html(text: str) -> str:
    if not text:
        return ""
    text = re.sub(r"<(script|style)\b[^>]*>.*?</\1>", " ", text, flags=re.IGNORECASE | re.DOTALL)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    return " ".join(text.split())


def parse_date(pub_date_str: str):
    if not pub_date_str:
        return None
    try:
        return parsedate_to_datetime(pub_date_str)
    except Exception:
        return None


def fetch_rss(url: str) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36"}
    )
    with urllib.request.urlopen(req, timeout=12, context=SSL_CTX) as response:
        return response.read()


def parse_rss(xml_data: bytes, source_name: str) -> list[dict]:
    root = ET.fromstring(xml_data)
    items = []
    for item in root.findall(".//item"):
        title = clean_html(item.findtext("title", default=""))
        link = item.findtext("link", default="").strip()
        pub_date = item.findtext("pubDate", default="").strip()
        desc = clean_html(item.findtext("description", default=""))
        if not title or not link:
            continue
        items.append({
            "title": title,
            "link": link,
            "published": pub_date,
            "datetime_obj": parse_date(pub_date),
            "description": desc,
            "source": source_name,
        })
    return items


def fetch_google_news_for_symbol(symbol: str, company_name: str = "") -> list[dict]:
    """Tìm kiếm trực tiếp các bài báo tài chính về mã cổ phiếu qua Google News RSS."""
    query = f"cổ phiếu {symbol}"
    if company_name:
        query += f" OR {company_name}"
    encoded_query = urllib.parse.quote(query)
    url = f"https://news.google.com/rss/search?q={encoded_query}&hl=vi&gl=VN&ceid=VN:vi"
    
    try:
        xml_data = fetch_rss(url)
        return parse_rss(xml_data, "Báo chí tài chính")
    except Exception as e:
        print(f"[Google News Lỗi] {e}")
        return []


def get_all_rss_news() -> list[dict]:
    all_news = []
    seen = set()
    for src, url in RSS_SOURCES.items():
        try:
            raw = fetch_rss(url)
            parsed = parse_rss(raw, src)
            for n in parsed:
                lnk = n["link"].strip().rstrip("/")
                if lnk not in seen:
                    seen.add(lnk)
                    all_news.append(n)
        except Exception as e:
            print(f"[RSS Lỗi] {src}: {e}")
    return all_news


# Thay dòng MODEL_NAME cũ bằng danh sách mô hình ưu tiên
PRIMARY_MODEL = "gemini-3.5-flash-lite"
FALLBACK_MODEL = "gemini-3.8-flash"

def fetch_article_content(url: str) -> str:
    """Tự động cào các đoạn văn bản chính từ bài báo gốc để lấy số liệu thực tế."""
    if not url or not url.startswith("http"):
        return ""
    try:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"}
        )
        with urllib.request.urlopen(req, timeout=5, context=SSL_CTX) as response:
            html_text = response.read().decode("utf-8", errors="ignore")
            # Trích xuất các thẻ đoạn văn <p>
            paragraphs = re.findall(r"<p\b[^>]*>(.*?)</p>", html_text, flags=re.IGNORECASE | re.DOTALL)
            clean_paras = []
            for p in paragraphs:
                cleaned = clean_html(p)
                if len(cleaned) > 40 and not any(kw in cleaned.lower() for kw in ["bản quyền", "liên hệ", "quảng cáo", "theo dõi"]):
                    clean_paras.append(cleaned)
            content = " ".join(clean_paras)
            return content[:3500] if content else ""
    except Exception:
        return ""

def analyze_with_gemini(news_item: dict, symbol: str) -> dict:
    if client is None:
        return {
            "summary": news_item.get("description", ""),
            "sentiment": "Trung lập",
            "reason": "Chưa cấu hình GEMINI_API_KEY trong .env.",
        }

    # 1. Thử lấy nội dung bài gốc chi tiết
    url = news_item.get("link", "")
    full_content = fetch_article_content(url)
    
    # Nếu không cào được thì fallback về description
    article_body = full_content if len(full_content) > 150 else news_item.get("description", "")

    # 2. Prompt phân tích chuyên sâu chuẩn tài chính
    prompt = f"""
Bạn là chuyên gia phân tích tài chính và chứng khoán Việt Nam.
Hãy đọc kỹ nội dung bài viết dưới đây về mã cổ phiếu {symbol}:

TIÊU ĐỀ: {news_item.get("title", "")}
NỘI DUNG CHI TIẾT:
{article_body}

YÊU CẦU:
1. 'summary': Viết đoạn tóm tắt nội dung bài viết (3-5 câu mạch lạc). 
   - BẮT BUỘC trích xuất chính xác các số liệu kinh tế - tài chính cụ thể nếu có trong bài (ví dụ: số triệu cổ phiếu giao dịch, tỷ lệ sở hữu %, doanh thu, lợi nhuận sau thuế, % tăng trưởng YoY, kế hoạch kinh doanh, đối tác lớn...).
   - Đi thẳng vào sự kiện cốt lõi, tuyệt đối KHÔNG viết chung chung sáo rỗng kiểu "Bài viết cung cấp góc nhìn...", "Nội dung phản ánh...".
2. 'sentiment': Đánh giá đúng 1 trong 3 nhãn: 'Tích cực', 'Trung lập', hoặc 'Tiêu cực'.
3. 'reason': Nhận định tác động cụ thể đến cổ phiếu và tâm lý nhà đầu tư trong 2-3 câu ngắn gọn.

Chỉ trả về JSON hợp lệ:
{{
  "summary": "...",
  "sentiment": "Tích cực",
  "reason": "..."
}}
"""
    for model_name in [PRIMARY_MODEL, FALLBACK_MODEL]:
        for attempt in range(2):
            try:
                response = client.models.generate_content(model=model_name, contents=prompt)
                text = (response.text or "").strip()
                text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.IGNORECASE).strip()
                res = json.loads(text)
                sent = res.get("sentiment", "Trung lập")
                if sent not in {"Tích cực", "Trung lập", "Tiêu cực"}:
                    sent = "Trung lập"
                return {
                    "summary": res.get("summary", news_item.get("description", "")),
                    "sentiment": sent,
                    "reason": res.get("reason", ""),
                }
            except Exception as e:
                err_msg = str(e)
                if "503" in err_msg or "429" in err_msg:
                    time.sleep(1.5)
                    continue
                break

    return {
        "summary": news_item.get("description", ""),
        "sentiment": "Trung lập",
        "reason": "Không thể phân tích tự động.",
    }

def get_company_news(symbol: str, limit: int = 3, company_name: str = "") -> tuple[list[dict], Any, bool]:
    s_sym = symbol.upper().strip()
    key = (s_sym, limit, "v2_full_content")
    now = time.time()

    with _NEWS_CACHE_LOCK:
        cached = _NEWS_CACHE.get(key)
        if cached and (now - cached["time"] < _NEWS_CACHE_TTL):
            return cached["data"]

    # 1. Lọc từ các trang RSS tài chính trong nước
    all_news = get_all_rss_news()
    keywords = list(COMPANY_KEYWORDS.get(s_sym, [s_sym]))
    if company_name and company_name not in keywords:
        keywords.append(company_name)

    matched = []
    for n in all_news:
        full_text = f"{n.get('title', '')} {n.get('description', '')}".casefold()
        if any(re.search(r"(?<!\w)" + re.escape(kw.casefold().strip()) + r"(?!\w)", full_text) for kw in keywords if kw.strip()):
            matched.append(n)

    # 2. Nếu không tìm thấy bài nào trên RSS trong ngày, tự động tra cứu báo chí qua Google News RSS
    if not matched:
        print(f"[Tin tức] Không có tin {s_sym} trên RSS hôm nay, đang tìm bài báo liên quan...")
        matched = fetch_google_news_for_symbol(s_sym, company_name)

    selected = matched[:limit]
    analyzed_news = []
    for item in selected:
        ai_res = analyze_with_gemini(item, s_sym)
        pub_str = item.get("published", "")
        if item.get("datetime_obj"):
            try:
                pub_str = item["datetime_obj"].strftime("%d/%m/%Y %H:%M")
            except Exception:
                pass

        analyzed_news.append({
            "title": item.get("title", ""),
            "summary": ai_res["summary"],
            "sentiment": ai_res["sentiment"],
            "sentiment_reason": ai_res["reason"],
            "publish_time": pub_str,
            "source": item.get("source", "Báo chí tài chính"),
            "url": item.get("link", ""),
            "provider": item.get("source", "Báo chí"),
        })

    result = (analyzed_news, None, False)
    with _NEWS_CACHE_LOCK:
        _NEWS_CACHE[key] = {"time": now, "data": result}

    return result