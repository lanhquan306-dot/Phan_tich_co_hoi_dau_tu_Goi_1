# Stock Opportunity Analyzer - Final

Website phân tích cơ hội đầu tư cổ phiếu Việt Nam phục vụ bài tập môn học. Hệ thống kết hợp phân tích cơ bản, kỹ thuật, điểm số 60/40, BUY/HOLD/SELL, chatbot giải thích, backtest, kiểm thử nhiều mã và báo cáo PDF có bảng nguồn dữ liệu.

## Chức năng chính

1. **Trang tổng quan**: tìm mã, tên doanh nghiệp, ngành, sàn, giá gần nhất, tín hiệu, điểm tổng hợp, rủi ro, thời điểm cập nhật.
2. **Thông tin doanh nghiệp**: hồ sơ hoạt động, ngày niêm yết, số cổ phiếu lưu hành, vốn hóa ước tính khi nguồn cung cấp.
3. **Phân tích cơ bản**: tăng trưởng doanh thu/thu nhập, LNST, ROE, ROA, D/E, P/E, P/B và bảng 3-5 năm khi đủ dữ liệu.
4. **Phân ngành chấm điểm**: ngân hàng/chứng khoán/bảo hiểm/tài chính khác không bị áp D/E như doanh nghiệp phi tài chính; phân loại dùng metadata ngành và danh sách mã dự phòng.
5. **Phân tích kỹ thuật**: nến, volume, MA20, MA50, RSI14, xác nhận khối lượng và giải thích xu hướng.
6. **Điểm tổng hợp**: `S = 0.6F + 0.4T`, có độ phủ dữ liệu và quy tắc chặn tín hiệu khi dữ liệu chưa đủ.
7. **Tin tức doanh nghiệp**: lấy qua `VNStock Reference().company(symbol).news()`; tin tức là bối cảnh, chưa cộng/trừ trực tiếp vào điểm.
8. **Báo cáo PDF**: format equity-research gọn, có luận điểm đầu tư, rủi ro, tài chính, kỹ thuật, tin tức và **bảng nguồn dữ liệu** ở cuối.
9. **Backtest**: 1/3/5 năm cho một mã; giảm look-ahead bias bằng cách chỉ dùng kỳ tài chính được giả định đã công bố.
10. **Kiểm thử nhiều mã**: endpoint và bảng web chạy cùng engine trên FPT/MWG/HPG/VCB/MBB/SSI/BVH hoặc danh sách tự chọn.
11. **Module thu thập đa mã độc lập**: `multi_stock_collector.py`, tách khỏi scoring/web.
12. **vn-annual-report-miner**: tích hợp tùy chọn qua CLI `arminer`; hệ thống chỉ ghi nguồn này là "đã dùng" khi truy vấn thực sự thành công.

## Cài đặt

```bash
pip install -r requirements.txt
copy .env.example .env   # Windows
python web_app.py
```

Mở:

```text
http://127.0.0.1:8050/
http://127.0.0.1:8050/analysis/FPT
```

## Các API chính

```text
GET  /api/analysis/FPT
GET  /api/backtest/FPT?years=3
GET  /api/validate?symbols=FPT,VCB,SSI,MWG
GET  /api/collect?symbols=FPT,VCB,SSI
GET  /report/FPT.pdf
POST /api/chat
```

## Tích hợp vn-annual-report-miner (tùy chọn)

Theo nguồn tham khảo của đề, có thể cài riêng:

```bash
git clone https://github.com/Tumiqa/vn-annual-report-miner.git
cd vn-annual-report-miner
pip install -e .
```

Sau đó đặt trong `.env`:

```text
ARMINER_ENABLED=1
```

Dashboard sẽ thử `arminer catalog search --ticker <MÃ>` và ghi trạng thái nguồn vào báo cáo. Nếu chưa cài, website vẫn hoạt động bình thường và **không tuyên bố sai rằng arminer đã được dùng**.

## Các file mới quan trọng

- `company_info_service.py`: thông tin doanh nghiệp từ VNStock Reference.
- `news_service.py`: tin tức doanh nghiệp.
- `annual_report_service.py`: bridge tùy chọn tới vn-annual-report-miner.
- `multi_stock_collector.py`: thu thập raw data nhiều mã độc lập.
- `multi_stock_validation.py`: bảng QA/chấm điểm nhiều mã.
- `investment_analysis.py`: engine cơ bản + kỹ thuật + 60/40 + phân ngành.
- `report_pdf.py`: PDF theo format báo cáo phân tích và bảng nguồn.
- `templates/investment_dashboard.html`: dashboard hoàn chỉnh.

## Kiểm thử offline

```bash
python selftests.py
python investment_selftests.py
python final_selftests.py
```

Kỳ vọng hiện tại:

```text
22 nhóm legacy PASS
4 nhóm investment PASS
4 nhóm final PASS
```

## Lưu ý phương pháp

- P/E thấp không tự động là tốt; P/E/P/B cần được đọc cùng tăng trưởng và rủi ro.
- RSI > 70 không tự động BUY, RSI < 30 không tự động SELL.
- D/E không được dùng máy móc cho ngân hàng/chứng khoán/bảo hiểm.
- Trọng số và ngưỡng 60/40 là thiết kế khởi đầu để kiểm thử, chưa được chứng minh tối ưu cho mọi cổ phiếu.
- Kết quả chỉ phục vụ phân tích học thuật/tham khảo, không bảo đảm lợi nhuận.
