"""Module Dự phóng Kết quả kinh doanh 3 năm và Định giá chuyên sâu cho cổ phiếu khuyến nghị MUA."""
from __future__ import annotations

from statistics import median
from typing import Any, Dict, List, Optional

from investment_profiles import INVESTMENT_PROFILES


class ValuationForecastService:
    # Các tham số chiết khấu chuẩn tài chính (không gắn với khẩu vị cá nhân)
    DISCOUNT_RATE_ANNUAL = 0.10  # Tỷ suất chiết khấu tham chiếu chuẩn (10%/năm)

    @staticmethod
    def is_eligible_for_forecast(signal_code: str) -> bool:
        """Chỉ kích hoạt dự phóng chuyên sâu khi tín hiệu chấm điểm là MUA (BUY)."""
        return str(signal_code).upper() == "BUY"

    @classmethod
    def execute(cls,
                symbol: str,
                signal_code: str,
                fundamental_data: Dict[str, Any],
                technical_data: Dict[str, Any],
                company_info: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        """
        Quy trình dự phóng KQKD 3 năm và định giá chuyên sâu.
        Nếu signal != BUY -> Trả về None.
        """
        if not cls.is_eligible_for_forecast(signal_code):
            return None

        company_type = fundamental_data.get("company_type", "regular")
        current_price = technical_data.get("close") or 0.0
        if current_price <= 0:
            return {
                "status": "INSUFFICIENT_DATA",
                "message": "Không có giá thị trường hợp lệ để tính giá mục tiêu và tỷ suất sinh lời."
            }
        
        # Số cổ phiếu phải lấy từ hồ sơ hoặc vốn hóa, không tự gán một giá trị giả định.
        shares_outstanding = company_info.get("issued_share") or 0
        if not shares_outstanding and company_info.get("market_cap") and current_price > 0:
            shares_outstanding = company_info.get("market_cap") / current_price
            
        # Kiểm tra dữ liệu lịch sử
        history: List[Dict[str, Any]] = fundamental_data.get("history") or []
        if len(history) < 2:
            return {
                "status": "INSUFFICIENT_DATA",
                "message": "Không đủ tối thiểu 2 năm dữ liệu lịch sử để thiết lập giả định dự phóng."
            }

        is_financial = company_type in {"bank", "securities", "insurance", "financial"}
        if not is_financial and shares_outstanding <= 0:
            return {
                "status": "INSUFFICIENT_DATA",
                "message": "Thiếu số cổ phiếu lưu hành; không thể tính EPS và giá mục tiêu P/E một cách đáng tin cậy."
            }

        # Dự phóng từng kịch bản từ chuỗi tài chính lịch sử; không dùng dữ liệu do AI tự sinh.
        forecast_result = cls._build_three_year_projections(history, company_type, shares_outstanding)
        if not forecast_result.get("success"):
            return {
                "status": "FORECAST_FAILED",
                "message": forecast_result.get("error", "Dự phóng không thành công.")
            }

        scenario_results = {}
        try:
            for scenario_key, scenario in forecast_result["scenarios"].items():
                valuation_result = cls._perform_valuation(
                    company_type=company_type,
                    forecast=scenario,
                    current_price=current_price,
                    fundamental_data=fundamental_data,
                    industry=str(company_info.get("industry") or ""),
                )
                metrics_result = cls._calculate_investment_metrics(valuation_result, current_price)
                scenario_results[scenario_key] = {
                    "name": scenario["name"],
                    "forecast_projections": scenario,
                    "valuation": valuation_result,
                    "investment_metrics": metrics_result,
                }
        except ValueError as exc:
            return {"status": "INSUFFICIENT_DATA", "message": str(exc)}

        profile_results = cls._evaluate_profiles(
            profiles=INVESTMENT_PROFILES,
            scenarios=scenario_results,
            fundamental_data=fundamental_data,
            company_type=company_type,
            company_info=company_info,
        )
        base_result = scenario_results["base"]

        return {
            "status": "COMPLETED",
            "symbol": symbol,
            "current_price": current_price,
            "valuation_method": base_result["valuation"]["valuation_method"],
            "forecast_projections": base_result["forecast_projections"],
            "valuation": base_result["valuation"],
            "investment_metrics": base_result["investment_metrics"],
            "scenarios": scenario_results,
            "profile_evaluations": profile_results,
            "model_note": (
                "Các kịch bản là phép ngoại suy định lượng từ dữ liệu lịch sử, không phải dự báo chắc chắn. "
                "Tín hiệu BUY/HOLD/SELL tổng thể vẫn được chấm riêng, không bị kết quả định giá ghi đè."
            ),
        }

    @classmethod
    def _build_three_year_projections(
        cls, history: List[Dict[str, Any]], company_type: str, shares: float
    ) -> Dict[str, Any]:
        """Tạo ba kịch bản tăng trưởng và biên lợi nhuận từ số liệu lịch sử."""
        sorted_hist = sorted(history, key=lambda x: x.get("year", 0))
        latest_year_data = sorted_hist[-1]
        base_year = latest_year_data.get("year", 2025)
        base_rev = latest_year_data.get("revenue") or 0.0
        base_npat = latest_year_data.get("npat") or 0.0

        if base_rev <= 0 or base_npat <= 0:
            return {"success": False, "error": "Doanh thu hoặc LNST năm gần nhất âm hoặc bằng 0, không khả dụng để dự phóng."}

        growth_rates = [
            float(h["revenue_growth"])
            for h in sorted_hist
            if h.get("revenue_growth") is not None
        ]
        base_growth = median(growth_rates) / 100.0 if growth_rates else 0.12
        base_growth = max(-0.05, min(base_growth * 0.85, 0.22))

        margins = [
            float(h["npat"]) / float(h["revenue"])
            for h in sorted_hist
            if h.get("revenue") and h.get("npat") is not None and float(h["revenue"]) > 0
        ]
        net_margin = median(margins) if margins else base_npat / base_rev
        net_margin = max(0.001, min(net_margin, 1.0))

        definitions = {
            "conservative": {
                "name": "Thận trọng",
                "growth": max(-0.10, base_growth - 0.05),
                "margin_factor": 0.90,
            },
            "base": {"name": "Cơ sở", "growth": base_growth, "margin_factor": 1.0},
            "optimistic": {
                "name": "Lạc quan",
                "growth": min(0.30, max(0.05, base_growth + 0.05)),
                "margin_factor": 1.10,
            },
        }
        scenarios = {}
        for key, definition in definitions.items():
            growth = definition["growth"]
            scenario_margin = min(1.0, net_margin * definition["margin_factor"])
            projections = {"name": definition["name"]}
            projections["valuation_factor"] = {
                "conservative": 0.90,
                "base": 1.0,
                "optimistic": 1.10,
            }[key]
            previous_revenue = base_rev
            for index, year_key in enumerate(("year_1", "year_2", "year_3")):
                annual_growth = growth * (1.0 if index == 0 else 0.92 if index == 1 else 0.85)
                revenue = previous_revenue * (1.0 + annual_growth)
                npat = revenue * scenario_margin
                item = {
                    "year": base_year + index + 1,
                    "revenue": revenue,
                    "npat": npat,
                    "rev_growth_pct": annual_growth * 100.0,
                    "net_margin_pct": scenario_margin * 100.0,
                    "eps": npat / shares if shares > 0 else None,
                }
                projections[year_key] = item
                previous_revenue = revenue
            projections["assumptions"] = (
                f"Tăng trưởng doanh thu khởi điểm {growth * 100:.1f}%/năm; "
                f"biên LNST {scenario_margin * 100:.2f}%; tốc độ tăng trưởng giảm dần qua từng năm."
            )
            scenarios[key] = projections

        if company_type in {"bank", "securities", "insurance", "financial"}:
            latest_roe = next(
                (float(h["roe"]) for h in reversed(sorted_hist) if h.get("roe") is not None),
                None,
            )
            if latest_roe is not None:
                for key, scenario in scenarios.items():
                    roe_adjustment = {"conservative": -2.0, "base": 0.0, "optimistic": 2.0}[key]
                    scenario["roe_pct"] = max(-5.0, min(latest_roe + roe_adjustment, 40.0))
                    scenario["retention_rate_pct"] = 70.0

        return {
            "success": True,
            "scenarios": scenarios,
            "data": scenarios["base"],
        }

    @classmethod
    def _perform_valuation(
        cls,
        company_type: str,
        forecast: Dict[str, Any],
        current_price: float,
        fundamental_data: Dict[str, Any],
        industry: str = "",
    ) -> Dict[str, Any]:
        """Định giá cuối Năm 3 bằng P/E hoặc P/B tùy loại hình doanh nghiệp."""
        criteria = fundamental_data.get("criteria", {})
        reference = fundamental_data.get("valuation_reference", {})
        scenario_factor = forecast.get("valuation_factor", 1.0)
        real_estate = any(
            term in industry.lower()
            for term in ("bất động sản", "bat dong san", "real estate", "property")
        )

        if company_type in {"bank", "securities", "insurance", "financial"}:
            current_pb = cls._positive_value(criteria.get("pb", {}).get("value"))
            base_bvps = cls._positive_value(fundamental_data.get("bvps"))
            if base_bvps is None and current_pb is not None:
                base_bvps = current_price / current_pb
            if base_bvps is None:
                raise ValueError("Thiếu BVPS và P/B hợp lệ để định giá doanh nghiệp tài chính.")

            roe = forecast.get("roe_pct")
            if roe is None:
                roe = cls._positive_value(criteria.get("roe", {}).get("value"))
            if roe is None:
                raise ValueError("Thiếu ROE hợp lệ để dự phóng giá trị sổ sách doanh nghiệp tài chính.")
            retention_rate = forecast.get("retention_rate_pct", 70.0) / 100.0
            y3_bvps = base_bvps * (1.0 + (roe / 100.0) * retention_rate) ** 3
            book_value_per_share = base_bvps
            for year_key in ("year_1", "year_2", "year_3"):
                book_value_per_share *= 1.0 + (roe / 100.0) * retention_rate
                forecast[year_key]["bvps"] = book_value_per_share
            peer_pb = cls._positive_value(reference.get("pb"))
            pb_anchor = peer_pb or current_pb
            if pb_anchor is None:
                raise ValueError("Thiếu P/B hiện tại và tham chiếu ngành để xác định P/B mục tiêu.")
            target_pb = max(0.25, min(pb_anchor * scenario_factor, 3.0))
            target_price_y3 = y3_bvps * target_pb

            return {
                "valuation_method": "P/B kết hợp ROE và tăng trưởng BVPS (doanh nghiệp tài chính)",
                "target_multiplier": round(target_pb, 2),
                "multiplier_unit": "x P/B",
                "y3_metric_value": y3_bvps,
                "target_price_y3": round(target_price_y3, 0),
                "note": (
                    f"P/B mục tiêu {target_pb:.2f}x; BVPS cơ sở {base_bvps:,.0f} VND; "
                    f"ROE kịch bản {roe:.1f}% và tỷ lệ giữ lại lợi nhuận {retention_rate:.0%}."
                ),
            }

        y3_eps = forecast["year_3"].get("eps")
        if not y3_eps or y3_eps <= 0:
            raise ValueError("Thiếu EPS dự phóng dương để định giá P/E.")
        current_pe = cls._positive_value(criteria.get("pe", {}).get("value"))
        peer_pe = cls._positive_value(reference.get("pe"))
        pe_anchor = peer_pe or current_pe
        if pe_anchor is None:
            raise ValueError("Thiếu P/E hiện tại và tham chiếu ngành để xác định P/E mục tiêu.")
        target_pe = max(3.0, min(pe_anchor * scenario_factor, 40.0))
        target_price_y3 = y3_eps * target_pe
        multiple_source = "trung vị ngành" if peer_pe is not None else "P/E hiện tại"
        method = "P/E Forward Năm 3"
        note = (
            f"P/E mục tiêu {target_pe:.1f}x theo {multiple_source} trên nền EPS dự phóng năm 3 "
            f"{y3_eps:,.0f} VND."
        )
        if real_estate:
            method += " (RNAV chưa khả dụng)"
            note += " Chưa có dữ liệu tài sản/dự án để tính RNAV, nên mô hình không tự giả định giá trị RNAV."

        return {
            "valuation_method": method,
            "target_multiplier": round(target_pe, 1),
            "multiplier_unit": "x P/E",
            "y3_metric_value": y3_eps,
            "target_price_y3": round(target_price_y3, 0),
            "note": note,
        }

    @staticmethod
    def _positive_value(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None

    @classmethod
    def _calculate_investment_metrics(cls, valuation: Dict[str, Any], current_price: float) -> Dict[str, Any]:
        """Đo lường các chỉ số đầu tư cốt lõi: Giá trị hiện tại, Tỷ suất IRR và Biên an toàn."""
        target_price = valuation.get("target_price_y3", 0.0)
        if current_price <= 0 or target_price <= 0:
            return {
                "valid": False,
                "expected_irr_annual": 0.0,
                "margin_of_safety_pct": 0.0,
                "fair_value_present": 0.0
            }

        # IRR 3 năm: (Target / Current)^(1/3) - 1
        irr_3y = (target_price / current_price) ** (1 / 3.0) - 1.0

        # Giá trị hiện tại ước lượng (Chiết khấu 10%/năm về thời điểm này)
        fair_value_today = target_price / ((1 + cls.DISCOUNT_RATE_ANNUAL) ** 3)
        margin_of_safety = (fair_value_today - current_price) / fair_value_today if fair_value_today > 0 else 0.0

        return {
            "valid": True,
            "target_price_y3": target_price,
            "fair_value_present": round(fair_value_today, 0),
            "expected_irr_annual": round(irr_3y * 100, 2),
            "margin_of_safety_pct": round(margin_of_safety * 100, 1),
            "upside_pct": round((target_price / current_price - 1.0) * 100, 1),
        }

    @classmethod
    def _evaluate_profiles(
        cls,
        profiles: Dict[str, Dict[str, Any]],
        scenarios: Dict[str, Dict[str, Any]],
        fundamental_data: Dict[str, Any],
        company_type: str,
        company_info: Dict[str, Any],
    ) -> Dict[str, Dict[str, Any]]:
        """Đối chiếu kịch bản phù hợp với ngưỡng rủi ro từng hồ sơ đầu tư."""
        industry_text = " ".join(
            str(company_info.get(key) or "")
            for key in ("industry", "profile", "company_name")
        ).lower()
        if company_type == "bank":
            industry_upside = 0.10
            industry_label = "Ngân hàng"
        elif any(term in industry_text for term in ("bất động sản", "bat dong san", "real estate")):
            industry_upside = 0.25
            industry_label = "Bất động sản"
        elif any(term in industry_text for term in ("công nghệ", "cong nghe", "technology")):
            industry_upside = 0.12
            industry_label = "Công nghệ"
        elif any(term in industry_text for term in ("thép", "thep", "dầu khí", "dau khi", "phân bón", "phan bon")):
            industry_upside = 0.20
            industry_label = "Chu kỳ"
        else:
            industry_upside = 0.15
            industry_label = "Thông thường"

        criteria = fundamental_data.get("criteria", {})
        debt_equity = cls._positive_value((criteria.get("de") or {}).get("value"))
        results = {}
        for key, profile in profiles.items():
            scenario_key = profile["forecast_scenario"]
            result = scenarios[scenario_key]
            metrics = result["investment_metrics"]
            valuation = result["valuation"]
            checks = [
                {
                    "label": "Biên an toàn",
                    "passed": metrics["margin_of_safety_pct"] >= profile["required_margin_of_safety"] * 100,
                },
                {
                    "label": "IRR kỳ vọng",
                    "passed": metrics["expected_irr_annual"] >= profile["min_required_irr"] * 100,
                },
                {
                    "label": f"Dư địa tối thiểu ngành {industry_label}",
                    "passed": metrics["upside_pct"] >= industry_upside * 100,
                },
            ]
            is_financial = company_type in {"bank", "securities", "insurance", "financial"}
            max_multiple = profile[
                "max_acceptable_pb" if is_financial else "max_acceptable_pe"
            ]
            checks.append({
                "label": f"{valuation['multiplier_unit']} mục tiêu trong ngưỡng hồ sơ",
                "passed": valuation["target_multiplier"] <= max_multiple,
            })
            if company_type == "regular" and debt_equity is not None:
                checks.append({
                    "label": "D/E trong ngưỡng hồ sơ",
                    "passed": debt_equity <= profile["max_acceptable_de"],
                })

            passed = all(check["passed"] for check in checks)
            has_positive_outlook = (
                metrics["expected_irr_annual"] > 0 and metrics["upside_pct"] > 0
            )
            results[key] = {
                "name": profile["name"],
                "description": profile["description"],
                "scenario_key": scenario_key,
                "scenario_name": result["name"],
                "eligible": passed,
                "verdict": (
                    "Định giá đạt ngưỡng hồ sơ"
                    if passed
                    else "Theo dõi thêm"
                    if has_positive_outlook
                    else "Chưa đạt ngưỡng định giá"
                ),
                "required_margin_of_safety_pct": profile["required_margin_of_safety"] * 100,
                "min_required_irr_pct": profile["min_required_irr"] * 100,
                "industry_label": industry_label,
                "min_required_upside_pct": industry_upside * 100,
                "checks": checks,
            }
        return results