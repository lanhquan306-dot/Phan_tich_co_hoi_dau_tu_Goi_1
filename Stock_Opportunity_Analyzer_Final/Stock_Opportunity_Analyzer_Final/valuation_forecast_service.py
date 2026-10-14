"""Module Dự phóng Kết quả kinh doanh 3 năm và Định giá chuyên sâu cho cổ phiếu khuyến nghị MUA."""
from __future__ import annotations

from typing import Any, Dict, List, Optional


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
        
        # Lấy số cổ phiếu: fallback sang market_cap / current_price nếu thiếu
        shares_outstanding = company_info.get("issued_share") or 0
        if not shares_outstanding and company_info.get("market_cap") and current_price > 0:
            shares_outstanding = company_info.get("market_cap") / current_price
        if not shares_outstanding:
            shares_outstanding = 1_000_000_000  # Fallback an toàn tránh chia cho 0
            
        # Kiểm tra dữ liệu lịch sử
        history: List[Dict[str, Any]] = fundamental_data.get("history") or []
        if len(history) < 2:
            return {
                "status": "INSUFFICIENT_DATA",
                "message": "Không đủ tối thiểu 2 năm dữ liệu lịch sử để thiết lập giả định dự phóng."
            }

        # 1. Dự phóng KQKD 3 năm (Doanh thu -> LNST -> EPS)
        forecast_result = cls._build_three_year_projections(history, company_type, shares_outstanding)
        if not forecast_result.get("success"):
            return {
                "status": "FORECAST_FAILED",
                "message": forecast_result.get("error", "Dự phóng không thành công.")
            }

        # 2. Định giá cổ phiếu tại cuối Năm 3
        valuation_result = cls._perform_valuation(
            symbol=symbol,
            company_type=company_type,
            forecast=forecast_result["data"],
            current_price=current_price,
            fundamental_data=fundamental_data
        )

        # 3. Tính toán Tỷ suất sinh lời kỳ vọng (IRR) và Biên an toàn
        metrics_result = cls._calculate_investment_metrics(
            valuation=valuation_result,
            current_price=current_price
        )

        return {
            "status": "COMPLETED",
            "current_price": current_price,
            "forecast_projections": forecast_result["data"],
            "valuation": valuation_result,
            "investment_metrics": metrics_result
        }

    @classmethod
    def _build_three_year_projections(cls, history: List[Dict[str, Any]], company_type: str, shares: float) -> Dict[str, Any]:
        """Xây dựng kịch bản dự phóng 3 năm dựa trên tăng trưởng lịch sử và biên LNST chuẩn hóa."""
        sorted_hist = sorted(history, key=lambda x: x.get("year", 0))
        latest_year_data = sorted_hist[-1]
        base_year = latest_year_data.get("year", 2025)
        base_rev = latest_year_data.get("revenue") or 0.0
        base_npat = latest_year_data.get("npat") or 0.0

        if base_rev <= 0 or base_npat <= 0:
            return {"success": False, "error": "Doanh thu hoặc LNST năm gần nhất âm hoặc bằng 0, không khả dụng để dự phóng."}

        # Tốc độ tăng trưởng doanh thu lịch sử tham chiếu
        hist_growth_rates = [
            h.get("revenue_growth") for h in sorted_hist if h.get("revenue_growth") is not None
        ]
        avg_hist_growth = (sum(hist_growth_rates) / len(hist_growth_rates)) / 100.0 if hist_growth_rates else 0.12
        
        # Chuẩn hóa tốc độ tăng trưởng (tránh giả định phi thực tế)
        norm_growth = max(0.05, min(avg_hist_growth * 0.85, 0.22))

        # Biên lợi nhuận sau thuế bình quân chuẩn hóa
        margin_list = [(h.get("npat", 0) / h.get("revenue", 1)) for h in sorted_hist if h.get("revenue", 0) > 0]
        net_margin = sum(margin_list) / len(margin_list) if margin_list else (base_npat / base_rev)

        # Tính toán từng năm
        y1_rev = base_rev * (1 + norm_growth)
        y1_npat = y1_rev * net_margin

        y2_rev = y1_rev * (1 + norm_growth * 0.92)  # Giảm tốc nhẹ theo quy mô
        y2_npat = y2_rev * net_margin

        y3_rev = y2_rev * (1 + norm_growth * 0.85)
        y3_npat = y3_rev * net_margin

        y3_eps = (y3_npat / shares) if shares > 0 else 0

        projections = {
            "year_1": {"year": base_year + 1, "revenue": y1_rev, "npat": y1_npat, "rev_growth_pct": norm_growth * 100},
            "year_2": {"year": base_year + 2, "revenue": y2_rev, "npat": y2_npat, "rev_growth_pct": norm_growth * 0.92 * 100},
            "year_3": {"year": base_year + 3, "revenue": y3_rev, "npat": y3_npat, "rev_growth_pct": norm_growth * 0.85 * 100, "eps": y3_eps},
            "assumptions": f"Giả định tăng trưởng doanh thu bình quân {norm_growth * 100:.1f}%/năm; biên lợi nhuận ròng chuẩn hóa {net_margin * 100:.2f}%."
        }

        return {"success": True, "data": projections}

    @classmethod
    def _perform_valuation(cls, symbol: str, company_type: str, forecast: Dict[str, Any],
                           current_price: float, fundamental_data: Dict[str, Any]) -> Dict[str, Any]:
        """Định giá cổ phiếu tại cuối Năm 3 theo P/E Forward hoặc P/B - ROE."""
        y3_eps = forecast["year_3"]["eps"]

        # Ngân hàng / Tổ chức tài chính -> Cân nhắc P/B & ROE
        if company_type in {"bank", "securities", "finance"}:
            criteria = fundamental_data.get("criteria", {})
            current_pb = criteria.get("pb", {}).get("value") or 1.2
            roe = criteria.get("roe", {}).get("value") or 15.0

            target_pb = min(max(1.0, (roe / 10.0)), 2.2)
            bvps_est = current_price / current_pb if current_pb > 0 else 0
            
            # Dự phóng BVPS sau 3 năm qua giữ lại lợi nhuận
            y3_bvps = bvps_est * ((1 + (roe / 100.0) * 0.7) ** 3)
            target_price_y3 = y3_bvps * target_pb

            return {
                "valuation_method": "P/B kết hợp ROE dự phóng (Đặc thù tài chính/ngân hàng)",
                "target_multiplier": round(target_pb, 2),
                "multiplier_unit": "x P/B",
                "y3_metric_value": y3_bvps,
                "target_price_y3": round(target_price_y3, 0),
                "note": f"Áp dụng P/B mục tiêu {target_pb:.2f}x trên cơ sở ROE duy trì {roe:.1f}%."
            }

        # Doanh nghiệp thông thường: P/E Forward Năm 3
        criteria = fundamental_data.get("criteria", {})
        current_pe = criteria.get("pe", {}).get("value") or 12.0
        target_pe = min(max(9.0, current_pe * 0.95), 18.0)
        target_price_y3 = y3_eps * target_pe

        return {
            "valuation_method": "P/E Forward Năm 3",
            "target_multiplier": round(target_pe, 1),
            "multiplier_unit": "x P/E",
            "y3_metric_value": y3_eps,
            "target_price_y3": round(target_price_y3, 0),
            "note": f"P/E hợp lý cuối năm 3 là {target_pe:.1f}x trên nền EPS dự phóng {y3_eps:,.0f} VND."
        }

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
        }