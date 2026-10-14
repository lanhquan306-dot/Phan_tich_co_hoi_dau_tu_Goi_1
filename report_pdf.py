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
NAVY = "#142B4A"
BLUE = "#245B86"
TEAL = "#1D8A83"
PALE = "#F2F6FA"
INK = "#203047"
MUTED = "#66758A"
LINE = "#D9E2EC"


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
    fig = plt.figure(figsize=(8.27, 11.69), dpi=130, facecolor="#F7F9FC")
    ax = fig.add_axes([0, 0, 1, 1]); ax.axis("off")
    ax.add_patch(plt.Rectangle((0, .925), 1, .075, transform=ax.transAxes,
                               color=NAVY, zorder=0))
    ax.add_patch(plt.Rectangle((0, .918), 1, .007, transform=ax.transAxes,
                               color=TEAL, zorder=0))
    fig.text(.065, .958, title, fontsize=14, fontweight="bold", color="white")
    if subtitle:
        fig.text(.067, .936, subtitle, fontsize=7.4, color="#DCE8F2")
    return fig, ax


def _footer(fig, result: dict, page_label: str):
    fig.text(.07, .025, f"{result.get('symbol','')}  |  {result.get('updated_at','')}  |  {page_label}", fontsize=6.6, color=MUTED)
    fig.text(.93, .025, "PHÂN TÍCH THAM KHẢO • KHÔNG BẢO ĐẢM LỢI NHUẬN", fontsize=5.8, color=MUTED, ha="right")
    fig.add_artist(plt.Line2D([.07, .93], [.043, .043], color=LINE, linewidth=.6, transform=fig.transFigure) )


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
        if r==0:
            cell.set_facecolor(NAVY); cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor("white" if r % 2 else PALE)
        cell.set_edgecolor(LINE); cell.set_linewidth(.45)
    fig.text(.07,.305,f"F = {s.get('fundamental',{}).get('score','N/A')}/100 (60%)   |   T = {s.get('technical',{}).get('score','N/A')}/100 (40%)   |   Độ phủ = {s.get('coverage_pct',0):.1f}%",fontsize=9,fontweight="bold")
    fig.text(.07,.075,_wrap(result.get("assessment",""),110),fontsize=7.7)
    _footer(fig,result,"Trang tóm tắt")
    pdf.savefig(fig,bbox_inches="tight");plt.close(fig)


def _page_executive_analysis(pdf: PdfPages, result: dict):
    """Evidence-based synthesis that connects the existing model outputs without recalculating scores."""
    fig, ax = _new_page("PHÂN TÍCH TỔNG HỢP", "Đọc kết quả theo logic: chất lượng doanh nghiệp → xu hướng giá → rủi ro → điều kiện cần kiểm chứng")
    fund = result.get("fundamental") or {}
    tech = result.get("technical") or {}
    score = result.get("score") or {}
    signal = result.get("signal") or {}
    criteria = fund.get("criteria") or {}
    comments = fund.get("comments") or {}
    close, ma20, ma50, rsi = (to_float(tech.get(k)) for k in ("close", "ma20", "ma50", "rsi14"))
    fscore = to_float((score.get("fundamental") or {}).get("score"))
    tscore = to_float((score.get("technical") or {}).get("score"))
    total = to_float(score.get("score"))
    # KPI strip
    cards = [
        ("ĐIỂM TỔNG HỢP", "N/A" if total is None else f"{total:.1f}/100"),
        ("CƠ BẢN", "N/A" if fscore is None else f"{fscore:.1f}/100"),
        ("KỸ THUẬT", "N/A" if tscore is None else f"{tscore:.1f}/100"),
    ]
    for i, (label, value) in enumerate(cards):
        x=.07+i*.30
        ax.add_patch(plt.Rectangle((x,.825),.27,.075,transform=ax.transAxes,facecolor="white",edgecolor=LINE,linewidth=.8))
        fig.text(x+.015,.873,label,fontsize=7,color=MUTED,fontweight="bold")
        fig.text(x+.015,.838,value,fontsize=14,color=NAVY,fontweight="bold")
    fig.text(.07,.785,"1. KẾT LUẬN ĐỌC NHANH",fontsize=10,fontweight="bold",color=BLUE)
    label = signal.get("label","Chưa xác định")
    reason = signal.get("reason","")
    overview = f"Mô hình hiện ghi nhận tín hiệu {label}. Đây là kết quả sàng lọc theo dữ liệu đang có, không phải kết luận mua/bán độc lập."
    fig.text(.085,.752,_wrap(overview,105),fontsize=8.3,color=INK,va="top")
    if reason:
        fig.text(.085,.711,_wrap("Căn cứ tín hiệu: "+str(reason),105),fontsize=7.8,color=INK,va="top")
    fig.text(.07,.665,"2. ĐỐI CHIẾU CƠ BẢN VÀ KỸ THUẬT",fontsize=10,fontweight="bold",color=BLUE)
    # Explicitly describe alignment / divergence from existing scores.
    if fscore is not None and tscore is not None:
        if fscore >= 60 and tscore >= 60:
            alignment = "Hai lớp phân tích cùng tương đối tích cực: nền tảng tài chính và diễn biến giá đang ủng hộ lẫn nhau. Vẫn cần kiểm tra định giá và chất lượng dữ liệu trước khi kết luận."
        elif fscore < 50 and tscore >= 60:
            alignment = "Giá có tín hiệu kỹ thuật tốt hơn nền tảng cơ bản. Cần thận trọng với khả năng đà tăng không được hỗ trợ tương xứng bởi kết quả kinh doanh."
        elif fscore >= 60 and tscore < 50:
            alignment = "Nền tảng cơ bản tốt hơn tín hiệu giá. Có thể doanh nghiệp ổn nhưng thị trường chưa xác nhận xu hướng; tránh suy ra thời điểm mua chỉ từ điểm cơ bản."
        elif fscore < 50 and tscore < 50:
            alignment = "Cả hai lớp điểm đều chưa mạnh. Ưu tiên tìm nguyên nhân ở tăng trưởng, sinh lời, định giá và xu hướng giá thay vì dựa riêng vào một chỉ báo."
        else:
            alignment = "Điểm cơ bản và kỹ thuật chưa đồng thuận rõ rệt. Cần đọc từng chỉ tiêu và các khoảng trống dữ liệu trước khi diễn giải tín hiệu tổng hợp."
    else:
        alignment = "Chưa đủ điểm thành phần để đối chiếu đầy đủ giữa nền tảng doanh nghiệp và xu hướng giá."
    fig.text(.085,.635,_wrap(alignment,105),fontsize=8.0,color=INK,va="top")
    fig.text(.07,.565,"3. BẰNG CHỨNG KỸ THUẬT TẠI THỜI ĐIỂM LẤY DỮ LIỆU",fontsize=10,fontweight="bold",color=BLUE)
    tech_rows=[
        ["Giá đóng cửa",_fmt(close)],
        ["MA20 / MA50",f"{_fmt(ma20)} / {_fmt(ma50)}"],
        ["RSI 14",_fmt(rsi)],
        ["Xu hướng do hệ thống phân loại",str(tech.get("trend") or "Chưa đủ dữ liệu")],
    ]
    table=ax.table(cellText=tech_rows,colLabels=["Chỉ báo","Giá trị / trạng thái"],cellLoc="left",colLoc="left",bbox=[.07,.405,.86,.135],colWidths=[.42,.58])
    table.auto_set_font_size(False);table.set_fontsize(7.5)
    for (r,c),cell in table.get_celld().items():
        if r==0: cell.set_facecolor(NAVY);cell.set_text_props(fontweight="bold",color="white")
        else: cell.set_facecolor("white" if r%2 else PALE)
        cell.set_edgecolor(LINE);cell.set_linewidth(.45)
    tech_interpretation=[]
    if None not in (close,ma20,ma50):
        tech_interpretation.append("Giá nằm trên MA20 và MA50" if close>ma20 and close>ma50 else "Giá nằm dưới cả MA20 và MA50" if close<ma20 and close<ma50 else "Giá đang ở hai phía khác nhau so với MA20/MA50")
    if rsi is not None:
        tech_interpretation.append("RSI trên 70: động lượng mạnh nhưng cần lưu ý trạng thái quá mua" if rsi>70 else "RSI dưới 30: áp lực bán mạnh, không tự động đồng nghĩa giá sẽ hồi phục" if rsi<30 else f"RSI ở mức {rsi:.1f}, nên đọc cùng xu hướng giá và khối lượng")
    if tech_interpretation:
        fig.text(.085,.383,_wrap("Diễn giải: "+"; ".join(tech_interpretation)+".",105),fontsize=7.5,color=INK,va="top")
    fig.text(.07,.325,"4. ĐIỂM CẦN KIỂM CHỨNG TRƯỚC KHI RA QUYẾT ĐỊNH",fontsize=10,fontweight="bold",color=BLUE)
    checks=[]
    for key in ("revenue_growth","profit_growth","roe","roa","de","pe","pb"):
        item=criteria.get(key) or {}
        if item:
            val=to_float(item.get("value"))
            name=item.get("name") or key
            points=to_float(item.get("points"))
            weight=to_float(item.get("weight"))
            if val is None:
                checks.append(f"{name}: chưa có giá trị hợp lệ, cần bổ sung trước khi tin cậy điểm số.")
            elif points is not None and weight is not None and points < weight*.5:
                checks.append(f"{name}: đóng góp điểm thấp ({points:.1f}/{weight:.0f}); cần đọc nguyên nhân từ số liệu gốc.")
    for item in (result.get("missing_data") or [])[:2]:
        checks.append("Thiếu dữ liệu: "+str(item))
    if not checks:
        checks=["Đối chiếu kỳ báo cáo tài chính mới nhất, tính nhất quán giữa lợi nhuận và dòng tiền, định giá so với doanh nghiệp cùng ngành, và các sự kiện bất thường chưa phản ánh vào dữ liệu."]
    y=.294
    for line in checks[:4]:
        fig.text(.085,y,"• "+_wrap(line,103).replace("\\n"," "),fontsize=7.4,color=INK,va="top");y-=.035
    fig.text(.07,.085,_wrap("Giới hạn: điểm tổng hợp phụ thuộc độ phủ, độ mới và chất lượng dữ liệu. Tin tức được dùng làm bối cảnh, không tự động chứng minh quan hệ nhân quả với giá cổ phiếu.",115),fontsize=7,color=MUTED)
    _footer(fig,result,"Phân tích tổng hợp")
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
        if r==0:
            cell.set_facecolor(NAVY); cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor("white" if r % 2 else PALE)
        cell.set_edgecolor(LINE); cell.set_linewidth(.45)
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
        if r==0:
            cell.set_facecolor(NAVY); cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor("white" if r % 2 else PALE)
        cell.set_edgecolor(LINE); cell.set_linewidth(.45)
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
        if r==0:
            cell.set_facecolor(NAVY); cell.set_text_props(fontweight="bold", color="white")
        else:
            cell.set_facecolor("white" if r % 2 else PALE)
        cell.set_edgecolor(LINE); cell.set_linewidth(.45);cell.PAD=.035
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
        _page_executive_analysis(pdf,result)
        _page_company_news(pdf,result)
        _page_fundamental(pdf,result)
        _page_score_risk(pdf,result)
        if include_chart:_page_chart(pdf,result)
        _page_sources(pdf,result)
    return buffer.getvalue()
