from pathlib import Path

import investment_analysis as investment
import report_pdf_new

# Phân tích cổ phiếu FPT
print("Đang phân tích cổ phiếu FPT...")
result = investment.analyze_investment("FPT")

# Tạo PDF bằng phiên bản mới
print("Đang tạo báo cáo PDF phiên bản mới...")
pdf_bytes = report_pdf_new.build_report_pdf(
    result,
    include_chart=True
)

# Xuất thành file riêng, không ảnh hưởng PDF cũ
output_path = (
    Path(__file__).resolve().parent
    / "FPT_report_NEW.pdf"
)

output_path.write_bytes(pdf_bytes)

print("ĐÃ XUẤT PDF THÀNH CÔNG!")
print("File:", output_path)