"""PDF equity-analysis report built from the same analysis object as the web.

The report follows a compact securities-research style: snapshot, investment
thesis/risk, financial analysis, technical chart, recent news and a final
provenance table. No score or signal is recalculated inside the PDF module.
"""
from __future__ import annotations

import io
import textwrap

import matplotlib
matplotlib.use("Agg")
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib import rcParams

import chart_service as charts
from utils import to_float

rcParams["font.family"] = "DejaVu Sans"


def _fmt(value, unit=""):
    v = to_float(value)
    if v is None:
        return "N/A"
    if unit == "%":
        return f"{v:+.1f}%"
    if unit == "x":
        return f"{v:.2f}x"
    return f"{v:,.0f}" if abs(v) >= 1000 else f"{v:.2f}"


def _wrap(s, width=95):
    return "\n".join(textwrap.wrap(str(s or ""), width=width))


def _lines(items, width=90):
    if not items:
        return ["- Chưa có mục nổi bật từ dữ liệu hiện có."]
    out = []
    for item in items:
        wrapped = textwrap.wrap(str(item), width=width) or [str(item)]
        out.append("- " + wrapped[0])
        out.extend("  " + x for x in wrapped[1:])
    return out


def _new_page(title: str, subtitle: str = ""):
    fig = plt.figure(figsize=(8.27, 11.69), dpi=130)
    ax = fig.add_axes([0, 0, 1, 1]); ax.axis("off")
    fig.text(.07, .955, title, fontsize=16, fontweight="bold")
    if subtitle:
        fig.text(.07, .932, subtitle, fontsize=8.5, color="#555555")
    return fig, ax


def _footer(fig, result: dict, page_label: str):
    fig.text(.07, .025, f"{result.get('symbol','')} · {result.get('updated_at','')} · {page_label}", fontsize=6.8, color="#666666")
    fig.text(.93, .025, "Hỗ trợ phân tích, không bảo đảm lợi nhuận", fontsize=6.4, color="#666666", ha="right")


def _page_cover(pdf: PdfPages, result: dict):
    fig, ax = _new_page("BÁO CÁO PHÂN TÍCH CƠ HỘI ĐẦU TƯ", "Tóm tắt theo phong cách báo cáo phân tích công ty chứng khoán")
    s, tech, fund, signal = result["score"], result["technical"], result["fundamental"], result["signal"]
    ci = result.get("company_info") or {}
    fig.text(.07, .885, f"{result['symbol']} — {result.get('company_name') or 'Doanh nghiệp niêm yết'}", fontsize=17, fontweight="bold")
    fig.text(.07, .857, f"{result.get('exchange') or 'N/A'} | {result.get('industry') or fund.get('company_type_label','N/A')}", fontsize=9.5)

    # Snapshot cards
    labels = [
        ("TÍN HIỆU", signal.get("label", "N/A")),
        ("ĐIỂM TỔNG HỢP", "N/A" if s.get("score") is None else f"{s['score']:.1f}/100"),
        ("RỦI RO", result.get("risk_level", "N/A")),
        ("GIÁ GẦN NHẤT", _fmt(tech.get("close"))),
    ]
    for i, (k, v) in enumerate(labels):
        x = .07 + (i % 2) * .45; y = .79 - (i // 2) * .11
        fig.text(x, y, k, fontsize=7.8, color="#555555")
        fig.text(x, y-.038, str(v), fontsize=20 if i < 3 else 16, fontweight="bold")

    fig.text(.07, .545, "LUẬN ĐIỂM ĐẦU TƯ", fontsize=10, fontweight="bold")
    y=.515
    for line in _lines(result.get("investment_thesis") or result.get("strengths") or [], 92)[:8]:
        fig.text(.085,y,line,fontsize=8.7); y-=.025
    fig.text(.07, y-.01, "RỦI RO / ĐIỂM CẦN THEO DÕI", fontsize=10, fontweight="bold"); y-=.045
    for line in _lines(result.get("risks") or result.get("weaknesses") or [], 92)[:8]:
        fig.text(.085,y,line,fontsize=8.5); y-=.024

    # Key financial snapshot
    c=fund.get("criteria") or {}
    rows=[]
    for key in ("revenue_growth","profit_growth","roe","roa","de","pe","pb"):
        item=c.get(key)
        if item:
            rows.append([item.get("name"), _fmt(item.get("value"),item.get("unit")), "N/A" if item.get("points") is None else f"{item['points']:.1f}/{item.get('weight',0):.0f}"])
    table=ax.table(cellText=rows,colLabels=["Chỉ tiêu","Giá trị","Điểm"],cellLoc="left",colLoc="left",bbox=[.07,.105,.86,.18],colWidths=[.50,.24,.22])
    table.auto_set_font_size(False);table.set_fontsize(7.7)
    for (r,_),cell in table.get_celld().items():
        if r==0: cell.set_text_props(fontweight="bold")
        cell.set_linewidth(.3)
    fig.text(.07,.305,f"F = {s.get('fundamental',{}).get('score','N/A')}/100 (60%)   |   T = {s.get('technical',{}).get('score','N/A')}/100 (40%)   |   Độ phủ = {s.get('coverage_pct',0):.1f}%",fontsize=9,fontweight="bold")
    fig.text(.07,.075,_wrap(result.get("assessment",""),110),fontsize=7.7)
    _footer(fig,result,"Trang tóm tắt")
    pdf.savefig(fig,bbox_inches="tight");plt.close(fig)


def _page_company_news(pdf: PdfPages, result: dict):
    fig, ax = _new_page("DOANH NGHIỆP, TIN TỨC VÀ NGUỒN ĐỊNH TÍNH")
    ci=result.get("company_info") or {}; ar=result.get("annual_report_source") or {}
    rows=[
        ["Tên doanh nghiệp",ci.get("company_name") or result.get("company_name") or "N/A"],
        ["Ngành",ci.get("industry") or result.get("industry") or "N/A"],
        ["Sàn",ci.get("exchange") or result.get("exchange") or "N/A"],
        ["Ngày niêm yết",ci.get("listing_date") or "N/A"],
        ["Cổ phiếu lưu hành",_fmt(ci.get("issued_share"))],
        ["Vốn hóa ước tính",_fmt(ci.get("market_cap"))],
        ["Website",ci.get("website") or "N/A"],
    ]
    table=ax.table(cellText=rows,cellLoc="left",bbox=[.07,.68,.86,.20],colWidths=[.25,.71])
    table.auto_set_font_size(False);table.set_fontsize(7.8)
    for cell in table.get_celld().values(): cell.set_linewidth(.3)
    fig.text(.07,.645,"MÔ TẢ HOẠT ĐỘNG",fontsize=9.5,fontweight="bold")
    fig.text(.07,.61,_wrap(ci.get("profile") or "Nguồn hiện có chưa cung cấp mô tả doanh nghiệp.",108),fontsize=7.8,va="top")

    fig.text(.07,.455,"TIN TỨC DOANH NGHIỆP GẦN ĐÂY",fontsize=9.5,fontweight="bold")
    y=.425
    news=result.get("news") or []
    if not news:
        fig.text(.08,y,"- Chưa lấy được tin tức từ nguồn hiện có.",fontsize=8)
        y-=.025
    else:
        for n in news[:6]:
            title=str(n.get("title") or "")
            meta=" · ".join(x for x in [str(n.get("publish_time") or ""),str(n.get("source") or n.get("provider") or "")] if x)
            for i,line in enumerate(textwrap.wrap(title,width=95)[:2]):
                fig.text(.08,y,("- " if i==0 else "  ")+line,fontsize=7.9,fontweight="bold" if i==0 else "normal");y-=.020
            fig.text(.095,y,meta,fontsize=6.7,color="#666666");y-=.028
            if y<.18: break

    fig.text(.07,.135,"VN-ANNUAL-REPORT-MINER",fontsize=9.2,fontweight="bold")
    fig.text(.07,.105,_wrap(ar.get("note") or "Nguồn bổ sung chưa kích hoạt.",108),fontsize=7.4)
    fig.text(.07,.075,"Nguồn này chỉ được ghi nhận là đã sử dụng khi arminer thực sự được cài và truy vấn thành công.",fontsize=6.9,color="#555555")
    _footer(fig,result,"Doanh nghiệp & tin tức")
    pdf.savefig(fig,bbox_inches="tight");plt.close(fig)


def _page_fundamental(pdf: PdfPages, result: dict):
    fig, ax = _new_page("PHÂN TÍCH TÀI CHÍNH CƠ BẢN")
    fund=result["fundamental"]; y=.90
    for title,items in [
        ("Tăng trưởng",(fund.get("comments") or {}).get("growth") or []),
        ("Khả năng sinh lời và đòn bẩy",(fund.get("comments") or {}).get("profitability_leverage") or []),
        ("Định giá",(fund.get("comments") or {}).get("valuation") or []),
    ]:
        fig.text(.07,y,title.upper(),fontsize=9.5,fontweight="bold");y-=.03
        for line in _lines(items,96):
            fig.text(.085,y,line,fontsize=8.0);y-=.023
        y-=.012
    fig.text(.07,min(y,.54),"XU HƯỚNG 3–5 NĂM",fontsize=9.5,fontweight="bold")
    hist=fund.get("history") or [];rows=[]
    for r in reversed(hist):
        rows.append([str(r.get("year","")),_fmt(r.get("revenue")),_fmt(r.get("revenue_growth"),"%"),_fmt(r.get("npat")),_fmt(r.get("profit_growth"),"%"),_fmt(r.get("roe"),"%"),_fmt(r.get("roa"),"%"),"N/A" if fund.get("company_type")!="regular" else _fmt(r.get("de"),"x")])
    if not rows: rows=[["N/A","Chưa đủ dữ liệu"]+[""]*6]
    table=ax.table(cellText=rows,colLabels=["Năm","Doanh thu","YoY","LNST","YoY LNST","ROE","ROA","D/E"],cellLoc="left",colLoc="left",bbox=[.055,.12,.89,.31],colWidths=[.08,.18,.12,.16,.13,.10,.10,.10])
    table.auto_set_font_size(False);table.set_fontsize(7.0)
    for (r,_),cell in table.get_celld().items():
        if r==0:cell.set_text_props(fontweight="bold")
        cell.set_linewidth(.3)
    fig.text(.07,.075,"D/E không áp dụng để chấm doanh nghiệp tài chính. Dữ liệu năm chỉ được tổng hợp khi đủ bốn quý; giá trị thiếu không thay bằng 0.",fontsize=7)
    _footer(fig,result,"Phân tích cơ bản")
    pdf.savefig(fig,bbox_inches="tight");plt.close(fig)


def _page_score_risk(pdf: PdfPages, result: dict):
    fig, ax = _new_page("ĐIỂM SỐ, TÍN HIỆU VÀ RỦI RO")
    s,signal,method=result["score"],result["signal"],result.get("methodology") or {}
    fig.text(.07,.90,f"Công thức: {s.get('formula','S = 0.6 × F + 0.4 × T')}",fontsize=11,fontweight="bold")
    fig.text(.07,.866,_wrap("BUY: "+method.get("buy_rule",""),105),fontsize=8.3)
    fig.text(.07,.835,_wrap("HOLD: "+method.get("hold_rule",""),105),fontsize=8.3)
    fig.text(.07,.804,_wrap("SELL: "+method.get("sell_rule",""),105),fontsize=8.3)
    fig.text(.07,.765,_wrap("Kết luận hiện tại: "+signal.get("label","")+" — "+signal.get("reason",""),105),fontsize=9,fontweight="bold")
    rows=[]
    for item in s.get("components",{}).values():
        rows.append([item.get("name"),_fmt(item.get("value"),item.get("unit")),f"{to_float(item.get('weight'),0):.0f}","N/A" if item.get("points") is None else f"{item['points']:.1f}"])
    table=ax.table(cellText=rows,colLabels=["Thành phần","Giá trị","Điểm tối đa","Điểm đạt"],cellLoc="left",colLoc="left",bbox=[.07,.50,.86,.22],colWidths=[.46,.2,.18,.14])
    table.auto_set_font_size(False);table.set_fontsize(7.2)
    for (r,_),cell in table.get_celld().items():
        if r==0:cell.set_text_props(fontweight="bold")
        cell.set_linewidth(.3)
    y=.455
    for title,items in [("Điểm mạnh",result.get("strengths") or []),("Điểm yếu / theo dõi",result.get("weaknesses") or []),("Rủi ro",result.get("risks") or [])]:
        fig.text(.07,y,title.upper(),fontsize=9,fontweight="bold");y-=.026
        for line in _lines(items,95)[:5]: fig.text(.085,y,line,fontsize=7.8);y-=.021
        y-=.008
    _footer(fig,result,"Điểm & rủi ro")
    pdf.savefig(fig,bbox_inches="tight");plt.close(fig)


def _page_sources(pdf: PdfPages, result: dict):
    fig, ax = _new_page("BẢNG NGUỒN DỮ LIỆU VÀ KHẢ NĂNG KIỂM CHỨNG", "Mỗi nhóm số liệu ghi rõ nguồn, kỳ dữ liệu, thời điểm lấy và ghi chú xử lý")
    rows=[]
    for x in result.get("data_sources") or []:
        rows.append([
            _wrap(x.get("data",""),22), _wrap(x.get("source",""),30), _wrap(x.get("period",""),24),
            _wrap(x.get("retrieved_at",""),20), _wrap(x.get("note",""),38),
        ])
    if not rows: rows=[["N/A"]*5]
    table=ax.table(cellText=rows,colLabels=["Dữ liệu","Nguồn","Kỳ / ngày dữ liệu","Ngày lấy","Ghi chú"],cellLoc="left",colLoc="left",bbox=[.045,.22,.91,.68],colWidths=[.17,.20,.17,.15,.31])
    table.auto_set_font_size(False);table.set_fontsize(6.4)
    for (r,_),cell in table.get_celld().items():
        if r==0:cell.set_text_props(fontweight="bold")
        cell.set_linewidth(.3);cell.PAD=.035
    fig.text(.055,.17,"DỮ LIỆU THIẾU",fontsize=8.8,fontweight="bold")
    fig.text(.055,.14,_wrap(", ".join(result.get("missing_data") or []) or "Không có mục chấm điểm bị thiếu tại thời điểm tạo báo cáo.",118),fontsize=7.2)
    fig.text(.055,.085,_wrap(result.get("disclaimer",""),118),fontsize=7)
    _footer(fig,result,"Nguồn dữ liệu")
    pdf.savefig(fig,bbox_inches="tight");plt.close(fig)


def _page_chart(pdf: PdfPages, result: dict):
    try:
        frame,info=charts.build_chart_frame(result["symbol"]);png=charts.render_chart_png(frame,info,"6M");image=mpimg.imread(io.BytesIO(png),format="png")
    except Exception:
        return
    fig=plt.figure(figsize=(11.69,8.27),dpi=130);ax=fig.add_axes([.02,.04,.96,.89]);ax.imshow(image);ax.axis("off")
    fig.text(.03,.965,f"PHÂN TÍCH KỸ THUẬT {result['symbol']} — Nến, khối lượng, MA20/MA50 và RSI14",fontsize=11.5,fontweight="bold")
    fig.text(.03,.02,"Biểu đồ dùng cùng dữ liệu giá với dashboard; tín hiệu kỹ thuật không được tính lại trong PDF.",fontsize=6.8,color="#555555")
    pdf.savefig(fig,bbox_inches="tight");plt.close(fig)


def build_report_pdf(result: dict, *, include_chart: bool = True) -> bytes:
    buffer=io.BytesIO()
    with PdfPages(buffer) as pdf:
        _page_cover(pdf,result)
        _page_company_news(pdf,result)
        _page_fundamental(pdf,result)
        _page_score_risk(pdf,result)
        if include_chart:_page_chart(pdf,result)
        _page_sources(pdf,result)
    return buffer.getvalue()
