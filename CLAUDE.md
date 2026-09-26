# Bối cảnh 

Dự án: Digital Twin Control Tower — đề D2 hackathon DENSO. Luồng: Kho → AMR → Packer (cổng) → AGV → Line.
Kiến trúc: DATA → STATE → TWIN → KPI → DECISION.

## Quy tắc bắt buộc
- Contract chuẩn nằm trong `contracts/` (spec v0.1 + fixture). KHÔNG đổi tên trường/kiểu/đơn vị;
  muốn đổi phải tăng version và cả nhóm duyệt.
- Mọi số ngẫu nhiên đi qua `World.rng` (numpy Generator có seed). Cùng seed -> cùng output.
- Thời gian nội bộ: phút tính từ t=0 (float). Timestamp ra ngoài: ISO 8601 có múi giờ +07:00.
- Data Layer KHÔNG được đọc `data/raw/*/_truth/`.
- Viết từng thay đổi nhỏ, kèm test trong `tests/`, chạy `pytest -q` trước khi đề xuất commit.
- Giải thích code bằng tiếng Việt trong comment/docstring.

## Phân công (không sửa thư mục của người khác)
- Trung: `src/generator/`, `src/data_layer/`, `src/demand/planner.py`, `src/demand/conversion.py`, `contracts/validate_contracts.py`
- Nhân: `src/twin/`, `src/scenario/runner.py`, `src/validation/backtest.py`
- Cường: `src/demand/forecast.py`, `src/scenario/scenarios.py`, `src/scenario/ranking.py`
- Hải Anh: `app/`

## Tài liệu
- `docs/feeds.md`: schema 6 feed + chuỗi ghép ID.
