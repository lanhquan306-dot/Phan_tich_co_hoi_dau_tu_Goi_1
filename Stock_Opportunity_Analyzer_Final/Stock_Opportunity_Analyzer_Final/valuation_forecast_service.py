"""Gemini-assisted three-year scenario valuation, independent of the L2 signal."""
from __future__ import annotations

import json
import logging
import os
import re
import time
from typing import Any, Dict, Optional

from dotenv import load_dotenv
from google.genai.errors import APIError


load_dotenv()
log = logging.getLogger("stockinsight.valuation")

SCENARIOS = {
    "conservative": "Thận trọng",
    "base": "Cơ sở",
    "optimistic": "Lạc quan",
}
YEAR_KEYS = ("year_1", "year_2", "year_3")


class ValuationForecastService:
    """Generate scenario assumptions with Gemini, then calculate prices locally."""

    MODEL_NAME = "gemini-2.5-flash"
    FALLBACK_MODEL_NAME = "gemini-3.8-flash"
    GENERATION_RETRY_DELAYS = (1.0, 2.0)
    INDUSTRY_MINIMUM_UPSIDE = {
        "REAL_ESTATE": 0.25,
        "CYCLICAL": 0.20,
        "NORMAL": 0.15,
        "TECH": 0.12,
        "BANK": 0.10,
    }

    @classmethod
    def execute(
        cls,
        symbol: str,
        fundamental_data: Dict[str, Any],
        technical_data: Dict[str, Any],
        company_info: Dict[str, Any],
    ) -> Dict[str, Any]:
        """Value the stock independently; never gate or rewrite the L2 signal."""
        industry_type = cls._industry_type(
            str(fundamental_data.get("company_type") or "regular"),
            str(company_info.get("industry") or ""),
        )
        if industry_type == "REAL_ESTATE":
            return {
                "status": "INSUFFICIENT_DATA",
                "industry_type": industry_type,
                "message": (
                    "Chưa thể định giá RNAV vì dữ liệu hiện có chưa bao gồm danh mục dự án, "
                    "giá trị phát triển và nghĩa vụ nợ theo từng dự án. Mô hình không tự tạo số RNAV."
                ),
            }

        current_price = cls._positive_value(technical_data.get("close"))
        if current_price is None:
            return {
                "status": "INSUFFICIENT_DATA",
                "industry_type": industry_type,
                "message": "Không có giá thị trường hợp lệ để tính giá mục tiêu.",
            }

        history = sorted(
            fundamental_data.get("history") or [],
            key=lambda row: row.get("year", 0),
        )
        if not history:
            return {
                "status": "INSUFFICIENT_DATA",
                "industry_type": industry_type,
                "message": "Thiếu số liệu tài chính gần nhất để làm cơ sở tạo kịch bản.",
            }

        shares = cls._positive_value(company_info.get("issued_share"))
        if shares is None:
            market_cap = cls._positive_value(company_info.get("market_cap"))
            shares = market_cap / current_price if market_cap is not None else None

        latest = history[-1]
        base_revenue = cls._positive_value(latest.get("revenue"))
        base_equity = cls._positive_value(latest.get("equity"))
        if industry_type == "BANK":
            if base_equity is None and shares is not None:
                bvps = cls._positive_value(fundamental_data.get("bvps"))
                base_equity = bvps * shares if bvps is not None else None
            if shares is None or base_equity is None:
                return {
                    "status": "INSUFFICIENT_DATA",
                    "industry_type": industry_type,
                    "message": "Thiếu số cổ phiếu lưu hành hoặc vốn chủ sở hữu/BVPS để định giá P/B.",
                }
        elif shares is None or base_revenue is None:
            return {
                "status": "INSUFFICIENT_DATA",
                "industry_type": industry_type,
                "message": "Thiếu số cổ phiếu lưu hành hoặc doanh thu gần nhất để định giá P/E.",
            }

        ai_result = cls._generate_scenarios(
            symbol=symbol,
            industry_type=industry_type,
            industry=str(company_info.get("industry") or ""),
            company_name=str(company_info.get("company_name") or symbol),
            history=history[-5:],
            current_price=current_price,
            shares=shares,
            base_revenue=base_revenue,
            base_equity=base_equity,
            current_roe=cls._positive_value(
                (fundamental_data.get("criteria", {}).get("roe") or {}).get("value")
            ),
        )
        if ai_result.get("error"):
            return {
                "status": ai_result["error"],
                "industry_type": industry_type,
                "message": ai_result["message"],
            }

        scenario_results: Dict[str, Dict[str, Any]] = {}
        summary_results = []
        for key, label in SCENARIOS.items():
            if industry_type == "BANK":
                forecast, valuation = cls._project_bank_scenario(
                    key=key,
                    assumptions=ai_result["assumptions"],
                    base_equity=base_equity,
                    shares=shares,
                    base_year=int(latest.get("year") or 0),
                )
            else:
                forecast, valuation = cls._project_company_scenario(
                    key=key,
                    assumptions=ai_result["assumptions"],
                    base_revenue=base_revenue,
                    shares=shares,
                    base_year=int(latest.get("year") or 0),
                )
            metrics = cls._calculate_metrics(valuation["target_price_y3"], current_price)
            scenario_results[key] = {
                "name": label,
                "forecast_projections": forecast,
                "valuation": valuation,
                "investment_metrics": metrics,
                "note": ai_result["scenario_notes"][label],
            }
            summary_results.append({
                "name": label,
                "method": valuation["valuation_method"],
                "target_price_y3": valuation["target_price_y3"],
                "cumulative_return_pct": metrics["upside_pct"],
                "cagr_pct": metrics["cagr_pct"],
            })

        base_result = scenario_results["base"]
        base_upside = base_result["investment_metrics"]["upside_pct"] / 100
        min_upside = cls.INDUSTRY_MINIMUM_UPSIDE[industry_type]
        final_signal, action_note = cls._independent_valuation_signal(
            base_upside, min_upside
        )
        return {
            "status": "COMPLETED",
            "symbol": symbol,
            "industry_type": industry_type,
            "industry_minimum_upside_pct": min_upside * 100,
            "current_price": current_price,
            "final_signal": final_signal,
            "base_upside_pct": round(base_upside * 100, 1),
            "action_note": action_note,
            "summary_table": summary_results,
            "scenarios": scenario_results,
            "forecast_projections": base_result["forecast_projections"],
            "valuation": base_result["valuation"],
            "investment_metrics": base_result["investment_metrics"],
            "scenario_notes": ai_result["scenario_notes"],
            "assumptions": ai_result["assumptions_text"],
            "limitations": ai_result["limitations"],
            "model_note": (
                "Gemini chỉ đề xuất giả định cho các kịch bản; giá mục tiêu và hiệu suất được tính "
                "bằng công thức trong ứng dụng. Đây là kết quả định giá độc lập, không kết hợp "
                "và không ghi đè tín hiệu L2."
            ),
        }

    @staticmethod
    def _industry_type(company_type: str, industry: str) -> str:
        normalized = industry.casefold()
        if any(term in normalized for term in ("bất động sản", "bat dong san", "real estate", "property")):
            return "REAL_ESTATE"
        if company_type in {"bank", "securities", "insurance", "financial"}:
            return "BANK"
        if any(term in normalized for term in ("công nghệ", "cong nghe", "technology", "software", "it ")):
            return "TECH"
        if any(term in normalized for term in (
            "thép", "thep", "dầu khí", "dau khi", "phân bón", "phan bon",
            "hóa chất", "hoa chat", "commodity", "cyclical", "steel",
            "oil", "gas", "fertilizer", "chemical",
        )):
            return "CYCLICAL"
        return "NORMAL"

    @classmethod
    def _generate_scenarios(
        cls,
        symbol: str,
        industry_type: str,
        industry: str,
        company_name: str,
        history: list[dict[str, Any]],
        current_price: float,
        shares: Optional[float],
        base_revenue: Optional[float],
        base_equity: Optional[float],
        current_roe: Optional[float],
    ) -> Dict[str, Any]:
        api_keys = cls._configured_api_keys()
        if not api_keys:
            return {
                "error": "AI_UNAVAILABLE",
                "message": (
                    "Chưa cấu hình GEMINI_API_KEY_VALUATION hoặc GEMINI_API_KEY; "
                    "không thể tạo giả định định giá bằng Gemini."
                ),
            }

        if industry_type == "BANK":
            instructions = (
                "Sinh roe_scenarios gồm ROE dự phóng cho từng năm (số thập phân, ví dụ 0.18 là 18%), "
                "retention_rate là tỷ lệ giữ lại lợi nhuận (0 đến 1), và pb_scenarios là P/B mục tiêu. "
                "Căn cứ vào ROE lịch sử, vốn chủ sở hữu và điều kiện ngành; không tạo số liệu lịch sử."
            )
        else:
            instructions = (
                "Sinh growth_scenarios gồm tăng trưởng doanh thu theo từng năm (số thập phân), "
                "margin_scenarios là biên lợi nhuận ròng (số thập phân từ 0 đến 1), "
                "và pe_scenarios là P/E mục tiêu. Căn cứ vào lịch sử tài chính và ngành; "
                "không tạo số liệu lịch sử."
            )

        prompt_data = {
            "symbol": symbol,
            "company_name": company_name,
            "industry": industry,
            "industry_type": industry_type,
            "current_price_vnd": current_price,
            "shares_outstanding": shares,
            "base_revenue_vnd": base_revenue,
            "base_equity_vnd": base_equity,
            "current_roe_pct": current_roe,
            "financial_history": history,
        }
        prompt = (
            "Bạn hỗ trợ lập giả định định giá cổ phiếu cho mục đích học tập. "
            "Không khẳng định chắc chắn về tương lai; chỉ trả về một JSON object theo schema, "
            "không có markdown. Ba kịch bản phải có thứ tự rủi ro hợp lý: thận trọng, cơ sở, lạc quan. "
            f"{instructions}\n"
            "Cấu trúc JSON bắt buộc: industry_type (string), scenario_notes (object gồm "
            "Thận trọng/Cơ sở/Lạc quan), assumptions (string), limitations (string), và các trường "
            "kịch bản tương ứng với loại ngành.\n"
            "Trong scenario_notes, ghi ngắn gọn lý do cho từng kịch bản bằng tiếng Việt. "
            "assumptions nêu giả định chính; limitations nêu rủi ro và giới hạn dữ liệu. "
            "Đơn vị dữ liệu tài chính và giá là VND, số cổ phiếu là cổ phiếu.\n"
            f"Thông tin đầu vào:\n{json.dumps(prompt_data, ensure_ascii=False, default=str)}"
        )
        try:
            from google.genai import types

            response = cls._generate_with_fallback(api_keys, prompt, types)
            text = response.text
            if not text:
                raise ValueError("Gemini trả về nội dung rỗng.")
            generated = json.loads(text)
            validated = cls._validate_scenario_data(generated, industry_type)
            return {
                "assumptions": validated,
                "scenario_notes": generated["scenario_notes"],
                "assumptions_text": generated["assumptions"],
                "limitations": generated["limitations"],
            }
        except APIError as exc:
            log.error("Gemini valuation API failed for %s (%s)", symbol, type(exc).__name__)
            return {
                "error": "AI_GENERATION_FAILED",
                "message": cls._describe_generation_error(exc),
            }
        except (ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
            log.exception("Invalid Gemini valuation response for %s", symbol)
            return {
                "error": "AI_GENERATION_FAILED",
                "message": f"Gemini trả về giả định định giá không hợp lệ: {exc}",
            }
        except Exception as exc:
            log.exception("Gemini valuation generation failed for %s", symbol)
            return {
                "error": "AI_GENERATION_FAILED",
                "message": cls._describe_generation_error(exc),
            }

    @staticmethod
    def _configured_api_keys() -> list[tuple[str, str]]:
        configured: list[tuple[str, str]] = []
        seen: set[str] = set()
        for name in ("GEMINI_API_KEY_VALUATION", "GEMINI_API_KEY"):
            value = os.getenv(name, "").strip()
            if value and value not in seen:
                configured.append((name, value))
                seen.add(value)
        return configured

    @classmethod
    def _generate_with_fallback(
        cls,
        api_keys: list[tuple[str, str]],
        prompt: str,
        config_types: Any,
    ) -> Any:
        from google import genai

        last_error: Optional[Exception] = None
        for key_name, api_key in api_keys:
            try:
                client = genai.Client(api_key=api_key)
            except Exception as exc:
                last_error = exc
                if cls._is_transient_generation_error(exc):
                    log.warning(
                        "Gemini valuation client for %s could not connect; trying the next configured key",
                        key_name,
                    )
                    continue
                raise

            retry_with_next_key = False
            for model_name in (cls.MODEL_NAME, cls.FALLBACK_MODEL_NAME):
                config = config_types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0.2,
                )
                for attempt in range(len(cls.GENERATION_RETRY_DELAYS) + 1):
                    try:
                        return client.models.generate_content(
                            model=model_name,
                            contents=prompt,
                            config=config,
                        )
                    except APIError as exc:
                        last_error = exc
                        if cls._is_model_unavailable_error(exc):
                            log.warning(
                                "Gemini valuation model %s is unavailable for %s; trying the fallback model",
                                model_name,
                                key_name,
                            )
                            break
                        if not cls._is_transient_generation_error(exc):
                            raise
                    except Exception as exc:
                        last_error = exc
                        if not cls._is_transient_generation_error(exc):
                            raise

                    if attempt < len(cls.GENERATION_RETRY_DELAYS):
                        delay = cls.GENERATION_RETRY_DELAYS[attempt]
                        log.warning(
                            "Transient Gemini valuation error for %s; retrying in %.1f seconds",
                            key_name,
                            delay,
                        )
                        time.sleep(delay)
                    else:
                        retry_with_next_key = True
                        log.warning(
                            "Gemini valuation retries exhausted for %s; trying the next configured key",
                            key_name,
                        )
                if retry_with_next_key:
                    break

        if last_error is not None:
            raise last_error
        raise RuntimeError("Gemini valuation has no configured API key.")

    @staticmethod
    def _is_model_unavailable_error(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        status = str(getattr(exc, "status", "") or "").upper()
        return code == 404 or status == "NOT_FOUND"

    @staticmethod
    def _describe_generation_error(exc: Exception) -> str:
        code = getattr(exc, "code", None)
        status = str(getattr(exc, "status", "") or "").upper()
        detail = str(getattr(exc, "message", "") or "").strip()
        detail = re.sub(r"AIza[0-9A-Za-z_-]{20,}", "[đã ẩn]", detail)
        if not detail:
            detail = type(exc).__name__

        if code == 400 or status == "INVALID_ARGUMENT":
            hint = " Kiểm tra định dạng yêu cầu Gemini; nếu lỗi tiếp diễn, xem nhật ký máy chủ."
        elif code == 401 or code == 403 or status in {"UNAUTHENTICATED", "PERMISSION_DENIED"}:
            hint = " Kiểm tra GEMINI_API_KEY và quyền truy cập Gemini API."
        elif code == 404 or status == "NOT_FOUND":
            hint = " Mô hình Gemini được cấu hình không khả dụng cho API key này."
        elif code == 429 or status == "RESOURCE_EXHAUSTED":
            hint = " Gemini API đang hết quota hoặc chạm giới hạn tốc độ."
        elif code == 503 or status == "UNAVAILABLE":
            hint = " Gemini đang tạm quá tải; hệ thống đã thử gọi lại. Vui lòng chờ một lát rồi thử lại."
        else:
            hint = " Kiểm tra kết nối mạng và nhật ký máy chủ."
        code_text = f"HTTP {code} " if code else ""
        status_text = f"({status}) " if status else ""
        return f"Gemini API lỗi {code_text}{status_text}: {detail}.{hint}"

    @staticmethod
    def _is_transient_generation_error(exc: Exception) -> bool:
        code = getattr(exc, "code", None)
        status = str(getattr(exc, "status", "") or "").upper()
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return True
        message = str(getattr(exc, "message", "") or exc).lower()
        exception_type = type(exc).__name__.lower()
        return code in {500, 502, 503, 504} or status in {
            "INTERNAL",
            "UNAVAILABLE",
            "DEADLINE_EXCEEDED",
        } or any(
            term in message or term in exception_type
            for term in ("connection", "connecterror", "timeout", "timed out", "temporarily unavailable")
        )

    @classmethod
    def _validate_scenario_data(
        cls, generated: Dict[str, Any], industry_type: str
    ) -> Dict[str, Any]:
        validated: Dict[str, Any] = {}

        def number(value: Any, field: str, minimum: float, maximum: float) -> float:
            if isinstance(value, bool):
                raise ValueError(f"{field} phải là một số hợp lệ.")
            try:
                result = float(value)
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{field} phải là một số hợp lệ.") from exc
            if not minimum <= result <= maximum:
                raise ValueError(f"{field} nằm ngoài khoảng cho phép.")
            return result

        if industry_type == "BANK":
            roe_scenarios = generated["roe_scenarios"]
            for key in SCENARIOS:
                values = roe_scenarios[key]
                if not isinstance(values, list) or len(values) != 3:
                    raise ValueError(f"roe_scenarios.{key} phải có đúng 3 năm.")
                validated.setdefault("roe_scenarios", {})[key] = [
                    number(value, f"roe_scenarios.{key}", -0.20, 0.60)
                    for value in values
                ]
            validated["retention_rate"] = number(
                generated["retention_rate"], "retention_rate", 0.0, 1.0
            )
            validated["pb_scenarios"] = {
                key: number(generated["pb_scenarios"][key], f"pb_scenarios.{key}", 0.1, 5.0)
                for key in SCENARIOS
            }
        else:
            growth = generated["growth_scenarios"]
            margins = generated["margin_scenarios"]
            multiples = generated["pe_scenarios"]
            for key in SCENARIOS:
                rates = growth[key]
                if not isinstance(rates, list) or len(rates) != 3:
                    raise ValueError(f"growth_scenarios.{key} phải có đúng 3 năm.")
                validated.setdefault("growth_scenarios", {})[key] = [
                    number(value, f"growth_scenarios.{key}", -0.50, 1.00)
                    for value in rates
                ]
                validated.setdefault("margin_scenarios", {})[key] = number(
                    margins[key], f"margin_scenarios.{key}", 0.0001, 1.0
                )
            validated["pe_scenarios"] = {
                key: number(multiples[key], f"pe_scenarios.{key}", 1.0, 60.0)
                for key in SCENARIOS
            }

        notes = generated.get("scenario_notes")
        if not isinstance(notes, dict):
            raise ValueError("scenario_notes phải là object.")
        validated_notes = {}
        for label in SCENARIOS.values():
            note = notes.get(label)
            if not isinstance(note, str) or not note.strip():
                raise ValueError(f"Thiếu ghi chú cho kịch bản {label}.")
            validated_notes[label] = note.strip()[:600]
        generated["scenario_notes"] = validated_notes
        for field in ("assumptions", "limitations"):
            value = generated.get(field)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"Thiếu trường {field}.")
            generated[field] = value.strip()[:1200]
        return validated

    @classmethod
    def _project_company_scenario(
        cls,
        key: str,
        assumptions: Dict[str, Any],
        base_revenue: Optional[float],
        shares: Optional[float],
        base_year: int,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if base_revenue is None or shares is None or shares <= 0:
            raise ValueError("Thiếu doanh thu hoặc số cổ phiếu lưu hành.")
        projections: Dict[str, Any] = {"metric_type": "eps"}
        previous_revenue = base_revenue
        for index, year_key in enumerate(YEAR_KEYS):
            growth = assumptions["growth_scenarios"][key][index]
            revenue = previous_revenue * (1 + growth)
            npat = revenue * assumptions["margin_scenarios"][key]
            if npat <= 0:
                raise ValueError("Lợi nhuận dự phóng không dương, không thể định giá P/E.")
            projections[year_key] = {
                "year": base_year + index + 1,
                "revenue": revenue,
                "npat": npat,
                "rev_growth_pct": growth * 100,
                "net_margin_pct": assumptions["margin_scenarios"][key] * 100,
                "eps": npat / shares,
            }
            previous_revenue = revenue
        target_pe = assumptions["pe_scenarios"][key]
        target_price = projections["year_3"]["eps"] * target_pe
        return projections, {
            "valuation_method": "P/E mục tiêu trên EPS dự phóng Năm 3",
            "target_multiplier": round(target_pe, 2),
            "multiplier_unit": "x P/E",
            "y3_metric_value": projections["year_3"]["eps"],
            "target_price_y3": round(target_price, 0),
        }

    @classmethod
    def _project_bank_scenario(
        cls,
        key: str,
        assumptions: Dict[str, Any],
        base_equity: Optional[float],
        shares: Optional[float],
        base_year: int,
    ) -> tuple[Dict[str, Any], Dict[str, Any]]:
        if base_equity is None or shares is None or shares <= 0:
            raise ValueError("Thiếu vốn chủ sở hữu hoặc số cổ phiếu lưu hành để định giá P/B.")
        retention = assumptions["retention_rate"]
        equity = base_equity
        projections: Dict[str, Any] = {"metric_type": "bvps"}
        for index, year_key in enumerate(YEAR_KEYS):
            roe = assumptions["roe_scenarios"][key][index]
            npat = equity * roe
            equity += npat * retention
            if equity <= 0:
                raise ValueError("Vốn chủ sở hữu dự phóng không dương, không thể định giá P/B.")
            projections[year_key] = {
                "year": base_year + index + 1,
                "revenue": None,
                "npat": npat,
                "roe_pct": roe * 100,
                "retention_rate_pct": retention * 100,
                "equity": equity,
                "bvps": equity / shares,
            }
        target_pb = assumptions["pb_scenarios"][key]
        target_price = projections["year_3"]["bvps"] * target_pb
        return projections, {
            "valuation_method": "P/B mục tiêu trên BVPS dự phóng Năm 3",
            "target_multiplier": round(target_pb, 2),
            "multiplier_unit": "x P/B",
            "y3_metric_value": projections["year_3"]["bvps"],
            "target_price_y3": round(target_price, 0),
        }

    @staticmethod
    def _calculate_metrics(target_price: float, current_price: float) -> Dict[str, float]:
        cumulative_return = target_price / current_price - 1
        cagr = (1 + cumulative_return) ** (1 / 3) - 1 if 1 + cumulative_return > 0 else -1.0
        return {
            "target_price_y3": round(target_price, 0),
            "upside_pct": round(cumulative_return * 100, 1),
            "cagr_pct": round(cagr * 100, 1),
        }

    @staticmethod
    def _independent_valuation_signal(
        base_upside: float, minimum_upside: float
    ) -> tuple[str, str]:
        upside_pct = round(base_upside * 100, 1)
        if base_upside >= 0.25:
            return (
                "STRONG BUY",
                f"Upside kịch bản cơ sở đạt {upside_pct}%; biên định giá vượt ngưỡng 25%.",
            )
        if base_upside >= minimum_upside:
            return (
                "ACCUMULATE / BUY",
                f"Upside kịch bản cơ sở đạt {upside_pct}%; đạt ngưỡng ngành {minimum_upside:.0%}.",
            )
        if base_upside >= 0:
            return (
                "HOLD",
                f"Upside kịch bản cơ sở chỉ đạt {upside_pct}%; thấp hơn ngưỡng ngành {minimum_upside:.0%}.",
            )
        return (
            "SELL / TAKE PROFIT",
            f"Upside kịch bản cơ sở âm ({upside_pct}%); giá hiện tại cao hơn giá trị định giá.",
        )

    @staticmethod
    def _positive_value(value: Any) -> Optional[float]:
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        return number if number > 0 else None
