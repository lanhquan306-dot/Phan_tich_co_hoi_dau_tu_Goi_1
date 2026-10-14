# 📈 Stock Opportunity Analyzer (Final Edition)

Hệ thống phân tích cơ hội đầu tư cổ phiếu toàn diện trên thị trường chứng khoán Việt Nam, kết hợp phân tích cơ bản (Fundamental Analysis - 60%) và phân tích kỹ thuật (Technical Analysis - 40%), tích hợp kiểm thử chéo (Cross-validation) theo 18 cụm ngành, backtest chiến lược đầu tư và module dự phóng kết quả kinh doanh 3 năm theo cơ chế Gatekeeper.

---

## 🌟 Tính năng nổi bật

### 1. Hệ thống chấm điểm đầu tư chuẩn hóa (Investment Score 60/40)
* **Điểm cơ bản (60%)**: Đánh giá đa chiều qua 3 trụ cột tài chính nội tại:
  * **Tăng trưởng**: Tăng trưởng doanh thu thuần và lợi nhuận sau thuế (LNST) so với cùng kỳ (YoY).
  * **Khả năng sinh lời & Đòn bẩy**: Hiệu quả sử dụng vốn (ROE, ROA) và tỷ lệ đòn bẩy tài chính (D/E). Hệ thống tự động phân loại và miễn trừ tiêu chí D/E đối với khối tài chính (Ngân hàng, Chứng khoán, Bảo hiểm) để tái phân bổ trọng số phù hợp.
  * **Định giá tương quan**: Bội số P/E, P/B kết hợp đối chiếu trung vị ngành.
* **Điểm kỹ thuật (40%)**:
  * Nhận diện cấu trúc xu hướng giá kết hợp đường trung bình động (MA20, MA50).
  * Đo lường động lượng quá mua/quá bán qua chỉ báo RSI(14).
  * Đánh giá xung lực dòng tiền qua khối lượng giao dịch phiên gần nhất so với bình quân 20 phiên.
* **Tín hiệu khuyến nghị tự động**: Phân loại theo ma trận quyết định minh bạch: **MUA (BUY)**, **GIỮ / CHỜ XÁC NHẬN (HOLD)**, **BÁN / GIẢM TỶ TRỌNG (SELL)** hoặc **KHÔNG ĐỦ DỮ LIỆU (NODATA)** khi độ phủ dữ liệu dưới 65%.

---

### 2. Bộ nhận xét kinh tế chuyên sâu 3 nhóm
Hệ thống tự động sinh nhận định định tính dựa trên các quy tắc chênh lệch định lượng:
* **Nhóm Tăng trưởng (Growth)**:
  * Đối chiếu biên độ tăng trưởng Doanh thu ($rg$) và LNST ($pg$).
  * Phân tích các kịch bản: LNST vượt trội ($pg - rg \ge 5\%$), Doanh thu vượt trội ($pg - rg \le -5\%$), Biến động đồng pha, Tăng trưởng quy mô nhưng suy giảm lợi nhuận, hoặc Cải thiện lợi nhuận nhờ tối ưu chi phí/hoạt động cốt lõi.
  * Đánh giá tính bền vững qua chuỗi dữ liệu lịch sử 3–5 năm gần nhất.
* **Nhóm Sinh lời & Đòn bẩy (Profitability & Leverage)**:
  * So sánh khoảng cách ROE và ROA: Cảnh báo rủi ro đòn bẩy khi khoảng cách $ROE - ROA \ge 10\%$.
  * Đánh giá tỷ lệ D/E cho doanh nghiệp phi tài chính theo các ngưỡng an toàn ($D/E \le 0.5x$), tiêu chuẩn ($0.5x < D/E \le 1.5x$) và cảnh báo độ nhạy lãi suất ($D/E > 1.5x$).
  * Nhận xét riêng biệt về an toàn vốn và chất lượng tài sản đối với các tổ chức tài chính.
* **Nhóm Định giá (Valuation)**:
  * Nhận định chi tiết vị thế P/E, P/B so với trung vị ngành.
  * Cảnh báo bẫy giá trị (*Value Trap*) khi $P/E < 12x$.
  * Tự động bổ sung nhận định về giá mục tiêu 12 tháng, upside/downside kỳ vọng và phương pháp định giá khi đạt tín hiệu MUA.

---

### 3. Module Dự phóng KQKD 3 năm & Định giá chuyên sâu (Gatekeeper)
* **Cơ chế kiểm soát Gatekeeper**: Chỉ tự động mở khóa và thực thi module định giá chuyên sâu khi cổ phiếu đạt tín hiệu **MUA (BUY)**. Các mã có tín hiệu GIỮ hoặc BÁN sẽ được giữ ở trạng thái theo dõi thông thường để tránh suy diễn chủ quan.
* **Nội dung dự phóng**:
  * Lập bảng dự phóng Kết quả kinh doanh 3 năm: Doanh thu thuần, LNST và EPS dự phóng.
  * Xác định Giá mục tiêu cuối Năm 3 ($Target_{Y3}$).
  * Chiết khấu về Giá trị hợp lý hiện tại ($Fair\ Value$) theo lãi suất chiết khấu chuẩn $10\%$/năm.
  * Tính toán Tỷ suất sinh lời nội hàm kỳ vọng ($IRR$/năm) và Biên độ an toàn ($Margin\ of\ Safety$).

---

### 4. Kiểm thử & So sánh đối chiếu (Cross-validation)
* **Bản đồ 18 cụm ngành toàn diện (250+ cổ phiếu)**: Tự động nhận diện và gom cụm ngành hoạt động (Ngân hàng, Bất động sản, Thép, Bán lẻ, Công nghệ, Hóa chất, Cảng biển, Xây dựng & Hạ tầng, Thiết bị điện, Thủy sản, Dược phẩm,...).
* **So sánh đồng hạng**: Luôn chọn lọc chính xác 5 mã (1 mã đang xem + 4 mã đồng nghiệp tương đồng nhất về mô hình kinh doanh và quy mô) để hiển thị bảng so sánh đa chiều về điểm số F, điểm số T, tín hiệu và rủi ro.

---

### 5. Backtest chiến lược đầu tư
* Mô phỏng hiệu suất sinh lời lịch sử theo chu kỳ **1 năm, 3 năm, 5 năm**.
* So sánh trực tiếp hiệu suất của chiến lược với chiến lược nắm giữ thụ động (**Buy & Hold**), tính toán Mức sụt giảm tối đa (**Max Drawdown**), số lượng giao dịch và tỷ lệ thắng (**Win Rate**).
* Giảm thiểu *look-ahead bias* bằng cách gắn dữ liệu tài chính theo mốc công bố BCTC thực tế.

---

### 6. Trải nghiệm người dùng & Trực quan hóa dữ liệu (UI/UX)
* **Thanh tab điều hướng cố định (`position: sticky`)**: Chuyển đổi nhanh giữa 3 khu vực:
  * *Tab 1: Phân tích & Tín hiệu* (Hồ sơ, Tài chính cơ bản, Kỹ thuật, Dự phóng KQKD, Tin tức).
  * *Tab 2: Kiểm thử & Backtest* (Backtest lịch sử và Cross-validation 5 mã).
  * *Tab 3: Phương pháp luận & Bảng tính điểm*.
* **Bảng tính điểm chi tiết (Scoring Breakdown)**: Minh bạch hóa toàn bộ công thức $S = 0.6 \times F + 0.4 \times T$, bóc tách rõ ràng trọng số tối đa, điểm đạt được và quy tắc chấm của từng tiêu chí.
* **Biểu đồ tài chính trực quan (Chart.js)**:
  * Biểu đồ cột: Doanh thu thuần và Lợi nhuận sau thuế qua các năm.
  * Biểu đồ đường 2 trục $Y$: Xu hướng biên sinh lời (ROE, ROA) và tỷ lệ đòn bẩy tài chính (D/E).
* **AI Chatbot**: Trợ lý hỗ trợ giải thích chuyên sâu nguyên nhân xếp hạng tín hiệu và nhận diện rủi ro danh mục.
* **Chế độ hiển thị**: Chuyển đổi linh hoạt giao diện **Sáng / Tối (Light / Dark mode)**.

---

## 🛠️ Yêu cầu môi trường & Cài đặt

### Yêu cầu tiên quyết
* Python 3.10 trở lên.
* Môi trường ảo (`.venv` hoặc `venv`).

### Hướng dẫn cài đặt

1. **Kích hoạt môi trường ảo (PowerShell):**
   ```powershell
   # Nếu dùng thư mục .venv
   .\.venv\Scripts\Activate.ps1
   
   # Hoặc nếu dùng thư mục venv
   .\venv\Scripts\Activate.ps1
   ```

2. **Cài đặt các gói thư viện cần thiết:**
   ```powershell
   pip install vnstock pandas numpy requests
   ```
   *(Hoặc: `pip install -r requirements.txt` nếu dự án có sẵn file)*

---

## ⚙️ Cấu hình dữ liệu (Data Sources)

Dự án sử dụng cơ chế kết nối và nạp dữ liệu chuẩn hóa:
* **Lịch sử giá nến (OHLCV) & Khối lượng**: Nạp trực tiếp qua VNStock, chuẩn hóa về đơn vị VNĐ và phục vụ vẽ biểu đồ kỹ thuật cùng tính toán MA20, MA50, RSI14.
* **Chỉ số BCTC & Hồ sơ doanh nghiệp**: Tự động bóc tách từ Báo cáo tài chính quý/năm hợp nhất.
* **Tham chiếu định giá**: File cấu hình `data/valuation_reference.json` làm mốc so sánh trung vị ngành cho các hệ số P/E, P/B.

---

## 🚀 Khởi chạy hệ thống

1. Khởi chạy máy chủ web nội bộ:
   ```powershell
   python web_app.py
   ```

2. Mở trình duyệt và truy cập:
   * **Trang chủ Dashboard**: `http://127.0.0.1:8050/`
   * **Phân tích trực tiếp một mã cổ phiếu**: `http://127.0.0.1:8050/analysis/MWG`

---

## 📂 Cấu trúc thư mục

```text
Stock_Opportunity_Analyzer/
│
├── web_app.py                      # File thực thi khởi chạy ứng dụng web
├── dashboard_server.py             # HTTP server, routing và API handlers
├── investment_analysis.py          # Core logic chấm điểm cơ bản, kỹ thuật và nhận xét 3 nhóm
├── valuation_forecast_service.py   # Module dự phóng KQKD 3 năm & định giá Gatekeeper
├── stock_service.py                # Dịch vụ nạp dữ liệu, tính toán chỉ báo và bộ nhớ đệm (cache)
├── company_info_service.py         # Dịch vụ nạp thông tin doanh nghiệp và danh mục peers cùng ngành
├── chart_service.py                # Xử lý khung dữ liệu biểu đồ kỹ thuật
├── news_service.py                 # Thu thập và chuẩn hóa tin tức doanh nghiệp
├── annual_report_service.py        # Tích hợp danh mục báo cáo thường niên
├── config.py                       # Danh mục mã cổ phiếu ngành tài chính (Ngân hàng, CK, BH)
├── utils.py                        # Các hàm tiện ích chuẩn hóa chuỗi và chuyển đổi số
│
├── data/
│   └── valuation_reference.json    # File dữ liệu cấu hình trung vị định giá P/E, P/B
│
├── templates/
│   └── investment_dashboard.html   # Giao diện Dashboard (HTML5, CSS Variables, JS, Chart.js)
│
└── README.md                       # Tài liệu hướng dẫn dự án
```

---

## 📌 Miễn trừ trách nhiệm (Disclaimer)

Hệ thống được phát triển phục vụ cho mục đích nghiên cứu học thuật và hỗ trợ góc nhìn phân tích tham khảo. Các ngưỡng điểm, quy tắc phân bổ trọng số, kịch bản dự phóng và tín hiệu khuyến nghị không cấu thành lời mời chào hay khuyến nghị mua bán đầu tư tài chính cá nhân hóa. Người dùng chịu hoàn toàn trách nhiệm đối với các quyết định giao dịch trên thị trường thực tế.