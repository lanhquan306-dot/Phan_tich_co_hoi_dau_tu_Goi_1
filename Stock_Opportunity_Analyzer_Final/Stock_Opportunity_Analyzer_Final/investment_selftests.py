"""Offline tests for the assignment-facing investment dashboard engine."""
from __future__ import annotations

from unittest.mock import patch

import os
import json
from types import SimpleNamespace
from unittest.mock import Mock
import pandas as pd
import numpy as np
from google.genai.errors import APIError

import chart_service as charts
import investment_analysis as ia
import opportunity_backtest as bt
import report_pdf
from valuation_forecast_service import ValuationForecastService


def ai_scenario_result(industry_type="NORMAL"):
    notes = {
        "Thận trọng": "Tăng trưởng chậm và bội số thận trọng.",
        "Cơ sở": "Duy trì xu hướng hiện tại.",
        "Lạc quan": "Tăng trưởng thuận lợi hơn kỳ vọng.",
    }
    if industry_type == "BANK":
        assumptions = {
            "roe_scenarios": {
                "conservative": [0.14, 0.14, 0.14],
                "base": [0.17, 0.17, 0.17],
                "optimistic": [0.20, 0.20, 0.20],
            },
            "retention_rate": 0.75,
            "pb_scenarios": {"conservative": 0.9, "base": 1.2, "optimistic": 1.5},
        }
    else:
        assumptions = {
            "growth_scenarios": {
                "conservative": [0.04, 0.04, 0.04],
                "base": [0.10, 0.10, 0.10],
                "optimistic": [0.16, 0.16, 0.16],
            },
            "margin_scenarios": {
                "conservative": 0.09,
                "base": 0.12,
                "optimistic": 0.15,
            },
            "pe_scenarios": {"conservative": 10.0, "base": 14.0, "optimistic": 18.0},
        }
    return {
        "assumptions": assumptions,
        "scenario_notes": notes,
        "assumptions_text": "Các giả định được tạo từ dữ liệu lịch sử và đặc điểm ngành.",
        "limitations": "Các giả định AI có thể sai; chưa mô phỏng biến cố tương lai.",
    }


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


def test_three_scenario_gemini_valuation_is_independent():
    history = [
        {"year": 2022, "revenue": 80_000_000_000, "revenue_growth": 8.0, "npat": 8_000_000_000, "roe": 12.0},
        {"year": 2023, "revenue": 92_000_000_000, "revenue_growth": 15.0, "npat": 9_200_000_000, "roe": 13.0},
        {"year": 2024, "revenue": 105_000_000_000, "revenue_growth": 14.1, "npat": 11_550_000_000, "roe": 14.0},
        {"year": 2025, "revenue": 120_000_000_000, "revenue_growth": 14.3, "npat": 14_400_000_000, "roe": 15.0},
    ]
    fundamental = {
        "company_type": "regular",
        "history": history,
        "criteria": {
            "pe": {"value": 12.0},
            "pb": {"value": 1.5},
            "de": {"value": 0.8},
        },
        "valuation_reference": {"pe": 13.0, "pb": 1.4},
    }
    with patch.object(
        ValuationForecastService, "_generate_scenarios",
        return_value=ai_scenario_result("TECH"),
    ):
        result = ValuationForecastService.execute(
            "AAA",
            fundamental,
            {"close": 10_000},
            {"issued_share": 12_000_000, "industry": "Công nghệ"},
        )
    assert result["status"] == "COMPLETED", result
    assert set(result["scenarios"]) == {"conservative", "base", "optimistic"}
    for scenario in result["scenarios"].values():
        assert scenario["valuation"]["target_price_y3"] > 0
        assert scenario["investment_metrics"]["cagr_pct"] is not None
        assert len([key for key in scenario["forecast_projections"] if key.startswith("year_")]) == 3
    assert result["valuation"] == result["scenarios"]["base"]["valuation"]
    assert "không kết hợp" in result["model_note"]
    assert result["industry_type"] == "TECH"
    assert result["final_signal"] == "STRONG BUY"
    assert result["scenarios"]["conservative"]["valuation"]["target_multiplier"] < result["scenarios"]["optimistic"]["valuation"]["target_multiplier"]

    missing_shares = ValuationForecastService.execute(
        "AAA", fundamental, {"close": 10_000}, {}
    )
    assert missing_shares["status"] == "INSUFFICIENT_DATA"
    assert "số cổ phiếu lưu hành" in missing_shares["message"]

    real_estate = ValuationForecastService.execute(
        "AAA",
        fundamental,
        {"close": 10_000},
        {"issued_share": 12_000_000, "industry": "Bất động sản"},
    )
    assert real_estate["status"] == "INSUFFICIENT_DATA"
    assert "RNAV" in real_estate["message"]


def test_financial_valuation_uses_pb_and_requires_real_inputs():
    history = [
        {"year": 2023, "revenue": 40_000_000_000, "revenue_growth": 8.0, "npat": 8_000_000_000, "roe": 16.0},
        {"year": 2024, "revenue": 44_000_000_000, "revenue_growth": 10.0, "npat": 9_000_000_000, "roe": 17.0, "equity": 60_000_000_000},
        {"year": 2025, "revenue": 49_000_000_000, "npat": 10_000_000_000, "roe": 18.0, "equity": 70_000_000_000},
    ]
    fundamental = {
        "company_type": "bank",
        "history": history,
        "criteria": {"pb": {"value": 1.2}, "roe": {"value": 18.0}},
        "bvps": 12_000,
        "valuation_reference": {"pb": 1.3},
    }
    with patch.object(
        ValuationForecastService, "_generate_scenarios",
        return_value=ai_scenario_result("BANK"),
    ):
        result = ValuationForecastService.execute(
            "BBB", fundamental, {"close": 14_400}, {"issued_share": 5_000_000}
        )
    assert result["status"] == "COMPLETED", result
    assert "P/B" in result["valuation"]["valuation_method"]
    assert result["forecast_projections"]["year_3"]["bvps"] > 0
    assert result["scenarios"]["conservative"]["valuation"]["target_multiplier"] < result["scenarios"]["optimistic"]["valuation"]["target_multiplier"]
    assert result["industry_type"] == "BANK"

    missing_book_value = {**fundamental, "bvps": None, "valuation_reference": {}}
    missing_book_value["criteria"] = {"roe": {"value": 18.0}}
    missing_book_value["history"][-1]["equity"] = None
    unavailable = ValuationForecastService.execute(
        "BBB", missing_book_value, {"close": 14_400}, {"issued_share": 5_000_000}
    )
    assert unavailable["status"] == "INSUFFICIENT_DATA"
    assert "vốn chủ sở hữu/BVPS" in unavailable["message"]


def test_valuation_requires_gemini_and_validates_ai_response():
    with patch.dict(os.environ, {}, clear=True):
        missing_key = ValuationForecastService._generate_scenarios(
            "AAA", "NORMAL", "Hàng tiêu dùng", "Công ty A", [], 10_000,
            1_000_000, 100_000_000, None, None,
        )
    assert missing_key["error"] == "AI_UNAVAILABLE"

    invalid = ai_scenario_result()
    invalid["assumptions"]["growth_scenarios"]["base"] = [0.1, 0.2]
    try:
        ValuationForecastService._validate_scenario_data(
            {
                **invalid["assumptions"],
                "scenario_notes": invalid["scenario_notes"],
                "assumptions": invalid["assumptions_text"],
                "limitations": invalid["limitations"],
            },
            "NORMAL",
        )
    except ValueError as exc:
        assert "đúng 3 năm" in str(exc)
    else:
        raise AssertionError("Malformed AI scenario accepted")

    generated = {
        **ai_scenario_result()["assumptions"],
        "industry_type": "NORMAL",
        "scenario_notes": ai_scenario_result()["scenario_notes"],
        "assumptions": ai_scenario_result()["assumptions_text"],
        "limitations": ai_scenario_result()["limitations"],
    }
    fake_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=Mock(return_value=SimpleNamespace(text=json.dumps(generated)))
        )
    )
    with (
        patch.dict(
            os.environ,
            {"GEMINI_API_KEY_VALUATION": "", "GEMINI_API_KEY": "test-key"},
        ),
        patch("google.genai.Client", return_value=fake_client),
    ):
        result = ValuationForecastService._generate_scenarios(
            "AAA", "NORMAL", "Hàng tiêu dùng", "Công ty A", [], 10_000,
            1_000_000, 100_000_000, None, None,
        )
    assert "error" not in result
    assert result["assumptions"]["growth_scenarios"]["base"] == [0.1, 0.1, 0.1]
    fake_client.models.generate_content.assert_called_once()
    assert fake_client.models.generate_content.call_args.kwargs["model"] == "gemini-2.5-flash"
    assert fake_client.models.generate_content.call_args.kwargs["config"].response_mime_type == "application/json"
    assert fake_client.models.generate_content.call_args.kwargs["config"].response_schema is None

    class FakeClientError(Exception):
        code = 403
        status = "PERMISSION_DENIED"
        message = "API key AIzaSyabcdefghijklmnopqrstuvwxyz1234567890 is not authorized"

    diagnostic = ValuationForecastService._describe_generation_error(FakeClientError())
    assert "HTTP 403" in diagnostic
    assert "PERMISSION_DENIED" in diagnostic
    assert "Kiểm tra GEMINI_API_KEY" in diagnostic
    assert "AIzaSy" not in diagnostic

    model_unavailable = APIError(
        404,
        {
            "error": {
                "code": 404,
                "status": "NOT_FOUND",
                "message": "gemini-2.5-flash is unavailable",
            }
        },
    )
    model_fallback_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=Mock(
                side_effect=[
                    model_unavailable,
                    SimpleNamespace(text=json.dumps(generated)),
                ]
            )
        )
    )
    with (
        patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY_VALUATION": "valuation-test-key",
                "GEMINI_API_KEY": "shared-test-key",
            },
        ),
        patch("google.genai.Client", return_value=model_fallback_client) as client_factory,
    ):
        model_fallback_result = ValuationForecastService._generate_scenarios(
            "AAA", "NORMAL", "Hàng tiêu dùng", "Công ty A", [], 10_000,
            1_000_000, 100_000_000, None, None,
        )
    assert "error" not in model_fallback_result
    assert client_factory.call_args.kwargs["api_key"] == "valuation-test-key"
    assert [
        call.kwargs["model"]
        for call in model_fallback_client.models.generate_content.call_args_list
    ] == ["gemini-2.5-flash", "gemini-3.8-flash"]

    class TemporaryGeminiError(Exception):
        code = 503
        status = "UNAVAILABLE"
        message = "Temporarily overloaded"

    dedicated_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=Mock(
                side_effect=[
                    TemporaryGeminiError(),
                    TemporaryGeminiError(),
                    TemporaryGeminiError(),
                ]
            )
        )
    )
    shared_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=Mock(return_value=SimpleNamespace(text=json.dumps(generated)))
        )
    )
    with (
        patch.dict(
            os.environ,
            {
                "GEMINI_API_KEY_VALUATION": "valuation-test-key",
                "GEMINI_API_KEY": "shared-test-key",
            },
        ),
        patch(
            "google.genai.Client",
            side_effect=[dedicated_client, shared_client],
        ) as client_factory,
        patch("valuation_forecast_service.time.sleep") as sleep,
    ):
        key_fallback_result = ValuationForecastService._generate_scenarios(
            "AAA", "NORMAL", "Hàng tiêu dùng", "Công ty A", [], 10_000,
            1_000_000, 100_000_000, None, None,
        )
    assert "error" not in key_fallback_result
    assert [
        call.kwargs["api_key"] for call in client_factory.call_args_list
    ] == ["valuation-test-key", "shared-test-key"]
    assert dedicated_client.models.generate_content.call_count == 3
    assert shared_client.models.generate_content.call_count == 1
    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0]

    retry_client = SimpleNamespace(
        models=SimpleNamespace(
            generate_content=Mock(
                side_effect=[
                    TemporaryGeminiError(),
                    TemporaryGeminiError(),
                    SimpleNamespace(text=json.dumps(generated)),
                ]
            )
        )
    )
    with (
        patch.dict(
            os.environ,
            {"GEMINI_API_KEY_VALUATION": "", "GEMINI_API_KEY": "test-key"},
        ),
        patch("google.genai.Client", return_value=retry_client),
        patch("valuation_forecast_service.time.sleep") as sleep,
    ):
        retried_result = ValuationForecastService._generate_scenarios(
            "AAA", "NORMAL", "Hàng tiêu dùng", "Công ty A", [], 10_000,
            1_000_000, 100_000_000, None, None,
        )
    assert "error" not in retried_result
    assert retry_client.models.generate_content.call_count == 3
    assert [call.args[0] for call in sleep.call_args_list] == [1.0, 2.0]
    unavailable_diagnostic = ValuationForecastService._describe_generation_error(
        TemporaryGeminiError()
    )
    assert "đã thử gọi lại" in unavailable_diagnostic
    assert not ValuationForecastService._is_transient_generation_error(
        FakeClientError()
    )


def test_backtest_periods_and_realistic_execution_costs():
    assert bt._settings_for_years(1).years == 1
    assert bt._settings_for_years("3").years == 3
    for unsupported in (False, 0, 1.5, 2, 5, "invalid"):
        try:
            bt._settings_for_years(unsupported)
        except ValueError as exc:
            assert "1 năm hoặc 3 năm" in str(exc)
        else:
            raise AssertionError(f"Unsupported backtest period accepted: {unsupported}")

    settings = bt.BacktestSettings(
        initial_capital=10_000_000,
        buy_fee_pct=0.001,
        sell_fee_pct=0.001,
        sell_tax_pct=0.001,
        slippage_pct=0.001,
        lot_size=100,
    )
    purchase = bt._buy_position(settings.initial_capital, 50_000, settings)
    assert purchase["quantity"] == 100
    assert purchase["cash_after"] >= 0
    approx(purchase["execution_price"], 50_050)
    approx(purchase["commission"], 5_005)

    sale = bt._sell_position(purchase["quantity"], 55_000, settings)
    approx(sale["execution_price"], 54_945)
    approx(sale["commission"], 5_494.5)
    approx(sale["sell_tax"], 5_494.5)
    assert sale["net_proceeds"] < sale["gross_value"]
    assert purchase["slippage_cost"] > 0 and sale["slippage_cost"] > 0
    try:
        bt.run_backtest("AAA", years=2)
    except ValueError:
        pass
    else:
        raise AssertionError("run_backtest accepted an unsupported period")


def test_backtest_end_to_end_with_mocked_market_data():
    dates = pd.bdate_range(end="2026-10-13", periods=500)
    close = np.linspace(50_000, 60_000, len(dates))
    prices = pd.DataFrame({
        "time": dates,
        "open": close - 100,
        "high": close + 200,
        "low": close - 200,
        "close": close,
        "volume": np.full(len(dates), 1_000_000),
    })
    signals = iter(("BUY", "SELL"))

    def fake_signal(*_):
        return {"code": next(signals, "HOLD")}

    score = {
        "score": 75,
        "fundamental": {"score": 80},
        "technical": {"score": 67},
        "coverage_pct": 88,
    }
    with (
        patch.object(bt.service, "get_history", return_value=(prices, None, False)),
        patch.object(bt.service, "confirmed_bars", side_effect=lambda frame: frame),
        patch.object(bt.service, "get_fundamental_tables", return_value=({}, None, None)),
        patch.object(bt.charts, "compute_indicators", side_effect=lambda frame: frame),
        patch.object(bt.ia, "_technical", return_value={}),
        patch.object(bt, "_fundamental_asof", return_value={}),
        patch.object(bt.ia, "_score_all", return_value=score),
        patch.object(bt.ia, "_signal_from_score", side_effect=fake_signal),
    ):
        result = bt.run_backtest("AAA", years=1)

    assert result["years"] == 1
    assert result["trades"] == 1
    assert result["win_rate_pct"] == 100.0
    assert result["strategy_return_pct"] > 0
    assert result["cagr_pct"] is not None
    assert result["buy_hold_return_pct"] is not None
    assert result["transaction_costs_vnd"] > 0
    assert result["slippage_costs_vnd"] > 0
    assert result["trade_log"][0]["return_pct"] > 0


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
    tests=[
        test_weights_and_formula,
        test_signals,
        test_technical_and_history,
        test_three_scenario_gemini_valuation_is_independent,
        test_financial_valuation_uses_pb_and_requires_real_inputs,
        test_valuation_requires_gemini_and_validates_ai_response,
        test_backtest_periods_and_realistic_execution_costs,
        test_backtest_end_to_end_with_mocked_market_data,
        test_pdf_without_chart,
    ]
    for fn in tests:
        fn(); print("PASS",fn.__name__)
    print(f"ALL PASS ({len(tests)} nhóm investment)")
