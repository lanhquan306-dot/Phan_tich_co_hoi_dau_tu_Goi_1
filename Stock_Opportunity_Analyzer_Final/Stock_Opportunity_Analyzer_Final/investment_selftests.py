"""Offline tests for the assignment-facing investment dashboard engine."""
from __future__ import annotations

import pandas as pd
import numpy as np

import chart_service as charts
import investment_analysis as ia
import report_pdf


def approx(got, want, tol=1e-8):
    assert got is not None and abs(got-want) <= tol, (got, want)


def full_fund(kind="regular"):
    w=ia._weights(kind)
    c={
        "revenue_growth":ia._criterion("Tăng trưởng doanh thu",20,w["revenue_growth"],w["revenue_growth"],"%","","growth"),
        "profit_growth":ia._criterion("Tăng trưởng LNST",30,w["profit_growth"],w["profit_growth"],"%","","growth"),
        "roe":ia._criterion("ROE TTM",20,w["roe"],w["roe"],"%","","profitability_leverage"),
        "roa":ia._criterion("ROA TTM",8,w["roa"],w["roa"],"%","","profitability_leverage"),
        "pe":ia._criterion("P/E",10,w["pe"],w["pe"],"x","","valuation"),
        "pb":ia._criterion("P/B",1.2,w["pb"],w["pb"],"x","","valuation"),
    }
    if kind=="regular": c["de"]=ia._criterion("D/E",.4,w["de"],w["de"],"x","","profitability_leverage")
    return {"company_type":kind,"criteria":c,"history":[],"comments":{},"company_type_label":ia.company_type_label(kind)}


def full_tech():
    return {"trend":"Tích cực","rsi14":60,"score_components":{
        "ma_trend":{"name":"MA","weight":24.0,"points":24.0},
        "rsi14":{"name":"RSI","weight":8.0,"points":8.0},
        "volume_confirmation":{"name":"Volume","weight":8.0,"points":8.0},
    }}


def test_weights_and_formula():
    assert sum(ia._weights("regular").values()) == 60
    assert sum(ia._weights("bank").values()) == 60
    assert "de" not in ia._weights("bank")
    sc=ia._score_all(full_fund(), full_tech())
    approx(sc["fundamental"]["score"],100)
    approx(sc["technical"]["score"],100)
    approx(sc["score"],100)
    assert sc["formula"] == "S = 0.6 × F + 0.4 × T"


def test_signals():
    fund,tech=full_fund(),full_tech(); sc=ia._score_all(fund,tech)
    assert ia._signal_from_score(sc,fund,tech)["code"]=="BUY"
    # Force mixed signals -> HOLD
    tech2=full_tech(); tech2["trend"]="Trung tính"; tech2["score_components"]["ma_trend"]["points"]=14
    sc2=ia._score_all(fund,tech2)
    assert ia._signal_from_score(sc2,fund,tech2)["code"]=="HOLD"
    # Severe deterioration -> SELL
    f3=full_fund();
    for x in f3["criteria"].values(): x["points"]=0.0
    f3["criteria"]["profit_growth"]["value"]=-40
    t3=full_tech(); t3["trend"]="Tiêu cực"
    for x in t3["score_components"].values(): x["points"]=0.0
    sc3=ia._score_all(f3,t3)
    assert ia._signal_from_score(sc3,f3,t3)["code"]=="SELL"
    # Missing most fundamentals blocks a signal
    f4=full_fund()
    for k,x in f4["criteria"].items():
        if k not in {"roe","roa"}: x["points"]=None
    sc4=ia._score_all(f4,full_tech())
    assert ia._signal_from_score(sc4,f4,full_tech())["code"]=="NODATA"


def test_technical_and_history():
    n=100
    d=pd.bdate_range("2026-01-01",periods=n)
    close=np.linspace(100,160,n)
    vol=np.full(n,1_000_000.0); vol[-1]=1_500_000
    raw=pd.DataFrame({"time":d,"open":close-1,"high":close+1,"low":close-2,"close":close,"volume":vol})
    frame=charts.compute_indicators(raw)
    t=ia._technical(frame)
    assert t["trend"]=="Tích cực"
    assert t["score_components"]["ma_trend"]["weight"]==24

    cols={"item":["Doanh thu thuần","Lợi nhuận sau thuế"]}
    bcols={"item":["Vốn chủ sở hữu","Tổng tài sản","Vay ngắn hạn","Vay dài hạn"]}
    for y in (2023,2024,2025):
        for q in range(1,5):
            cols[f"{y}-Q{q}"]=[100*y/2023 + 10*q, 10*y/2023 + q]
            bcols[f"{y}-Q{q}"]=[500+y-2023*0,1000+y-2023*0,100,50]
    hist=ia._financial_history(pd.DataFrame(cols),pd.DataFrame(bcols),"regular",5)
    assert len(hist)>=2
    assert all("revenue" in r and "npat" in r for r in hist)


def test_pdf_without_chart():
    fund=full_fund(); fund.update({"latest_period":"2026-Q2","source":"TEST","comments":{"growth":[],"profitability_leverage":[],"valuation":[]},"history":[]})
    tech=full_tech(); tech.update({"close":100000,"ma20":99000,"ma50":95000,"date":"2026-10-09","vol_ratio":1.2})
    sc=ia._score_all(fund,tech); sig=ia._signal_from_score(sc,fund,tech)
    result={"symbol":"AAA","company_name":"Demo","industry":"Demo","exchange":"HOSE","updated_at":"10:00 09/10/2026","price_source":"TEST",
            "fundamental":fund,"technical":tech,"score":sc,"signal":sig,"risk_level":"Thấp - Trung bình","assessment":"Demo assessment",
            "strengths":[],"weaknesses":[],"risks":[],"missing_data":[],"methodology":ia._methodology("regular"),"disclaimer":"Demo"}
    pdf=report_pdf.build_report_pdf(result,include_chart=False)
    assert pdf.startswith(b"%PDF") and len(pdf)>10_000


if __name__=="__main__":
    tests=[test_weights_and_formula,test_signals,test_technical_and_history,test_pdf_without_chart]
    for fn in tests:
        fn(); print("PASS",fn.__name__)
    print(f"ALL PASS ({len(tests)} nhóm investment)")
