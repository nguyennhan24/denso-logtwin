# Đề xuất thay đổi contract — từ Data Layer (Trung, Phase 4)

Người đọc: cả nhóm (Nhân — Twin, Cường — Scenario, Hải Anh — Dashboard).
Trạng thái: **đề xuất, chưa áp dụng vào contract**. Không trường nào của v0.1 bị đổi tên, đổi kiểu hay xóa.
Cập nhật 2026-10-09: thêm `est_remaining_min`, `completion_unconfirmed`, `created_offset_min`, `resource_counts`,
`blocked_jobs` (mục A) và vấn đề B7–B9.

## A. Trường đã THÊM vào output (giữ nguyên toàn bộ trường v0.1)

File `data/gold/<ngày>_seed<seed>/state_HHMM.json` có dạng giống `contracts/mock_v0_1.json`:
`{"contract_version": "0.1", "current_state": {...}}`. Chạy được qua `contracts/validate_contracts.py`
và nạp được bằng dataclass của Twin (`src/twin/entities.py`), đã có test.

| Vị trí | Trường thêm | Kiểu | Ý nghĩa |
|---|---|---|---|
| `active_jobs[]` | `is_estimated` | bool | Luôn `true`: `remaining_min` là ước lượng, không quan sát được |
| `active_jobs[]` | `estimate_method` | string | Cách tính, ví dụ `median(ARRIVE_GATE - DISPATCH) trên 215 lượt trước as_of = 3.77 phút, trừ thời gian đã chạy` |
| `active_jobs[]` | `overdue` | bool | Đã quá thời gian dự kiến mà chưa thấy sự kiện kết thúc. Khi `true`, `remaining_min` = 0.5 (giá trị sàn vì contract đòi > 0) |
| `active_jobs[]` | `est_remaining_min` | number ≥ 0 | Ước lượng thật. Quá hạn dự kiến thì = 0. Luôn có `overdue ⇔ est_remaining_min = 0` (xem B7) |
| `active_jobs[]` | `completion_unconfirmed` | bool | `true` khi `overdue` và mốc kết thúc khâu đến từ nguồn manual (Excel): có thể đã xong mà chưa được ghi nhận |
| `queued_jobs[]`, `active_jobs[]` | `created_offset_min` | number ≤ 0 | Phút từ `as_of` tới lúc tạo đơn, âm = trước as_of (xem B8) |
| `current_state` | `resource_counts` | object | Số đếm vật lý: `amr {total, busy, blocked, idle, offline}`, `agv`/`packer {total, busy, idle, offline}`. Tổng các phần = `total` (xem B1) |
| `current_state.resource_detail.amr` | `blocked_jobs` | list[{amr_id, request_id}] | Đơn mà mỗi xe blocked đang giữ ở cổng; `request_id = null` nếu không nối được (xem B9) |
| `current_state` | `open_request_count` | int | Số đơn đang mở tại `as_of` |
| `current_state` | `resource_detail` | object | Trạng thái **vật lý** từng xe: `amr`: `{busy, blocked, idle, offline}`; `agv`: `{busy, idle, offline}` (đều là list ID); `packer`: `slots`, `workers_on_shift`, `workers_on_break`, `workers_available` |
| `current_state.resource_detail.amr` | `blocked` | list[str] | AMR đứng ở cổng chờ người nhận thùng (sự kiện gần nhất `ARRIVE_GATE`). `idle` chỉ còn xe thật sự rảnh. Luôn có `len(busy) + len(blocked) + len(idle) + len(offline) = resources.amr.total` |
| `current_state` | `source_freshness` | object | Theo từng feed: `source_type` (stream/batch/manual), `last_update`, `age_min`, `stale_after_min`, `stale` |

Ví dụ 14:10 ngày 21 seed 42: `resource_counts.amr = {total: 6, busy: 4, blocked: 2, idle: 0, offline: 0}`,
`blocked_jobs = [{AMR-02, PICK-00215}, {AMR-03, PICK-00214}]`. `PICK-00210` (packer) có
`overdue = true, est_remaining_min = 0, completion_unconfirmed = true, remaining_min = 0.5`.

**Không thêm khóa nào vào `request` và `resources.*`**: Twin dựng `MaterialRequest(**request)` và
`ResourceStatus(**resource)`, khóa lạ sẽ làm Twin lỗi. `contract_check.py` chặn điều này.

## B. Vấn đề cần nhóm quyết (Data Layer KHÔNG tự đổi)

### B1. `resources.amr.busy` không phản ánh AMR bị giữ ở cổng / đang chạy về — **quan trọng cho Twin**
Spec mục 3: `busy` = số active job ở stage đó, và baseline capacity = `total - offline`.
Nhưng trong nhà máy (và Generator), AMR **đứng chờ ở cổng** tới khi packer nhận thùng, rồi **chạy về kho** mới rảnh.
Những xe này không có "active job ở stage amr" nhưng cũng không nhận được việc mới.

Ví dụ ngày 21 seed 42, 14:10: contract báo `amr = {total: 6, busy: 4, offline: 0}` → Twin coi còn 2 AMR rảnh.
Thực tế `resource_detail.amr` = 4 busy (đang chở) + 2 blocked (AMR-02, AMR-03 đứng ở cổng) + 0 idle,
trong khi 4 đơn đang xếp hàng chờ AMR. → Twin sẽ dự báo hàng AMR thoát nhanh hơn thực tế.

**Đã làm (v0.1, chỉ thêm):** `resource_detail.amr.blocked` (list robot_id) và `resource_counts.amr.blocked` (số xe).
Có yêu cầu đặt `"blocked": <số xe>` thẳng vào `resources.amr`. Chưa làm vì `ResourceStatus(**resources["amr"])`
của Twin sẽ báo lỗi khóa lạ, và validator chung cũng chặn. Lưu ý thêm: nếu chỉ thêm `blocked` mà giữ
`busy` = số job thì `total − busy − blocked − offline` vẫn **không** bằng số xe rảnh, vì xe đang chạy về kho
không có job. Ngày 21 seed 42 đẳng thức này sai ở 29/31 mốc mỗi 30 phút. Phương án (a) dưới đây phải đi kèm
quyết định `busy` đếm job hay đếm xe. Đặt trong `resource_detail`, không đặt trong
`resources.amr`, vì Twin hiện dựng `ResourceStatus(**resources["amr"])` và sẽ lỗi nếu có khóa lạ.

Đề xuất v0.2 (cần Nhân đồng ý vì Twin phải sửa):
- (a) thêm `resources.amr.blocked` (số nguyên) vào contract, baseline capacity = `total - offline - blocked`; hoặc
- (b) Twin đọc `len(resource_detail.amr.idle)` để biết số AMR rảnh thật lúc snapshot.

### B2. `feed_updated_at` bắt buộc là timestamp, kể cả khi nguồn chưa có dữ liệu
Ví dụ Excel trước 08:00 chưa có dòng nào. Hiện Data Layer điền giờ đầu ngày (06:00) và đặt
`source_freshness.<feed>.stale = true`. Đề xuất v0.2: cho phép `null`.

### B3. Thông tin không có trong feed nên phải lấy từ config
- `origin_id`: WMS không có cột kho → lấy `STORE-1` từ `config/data_layer.yaml` (v0.1 chỉ có 1 kho).
- `priority`: WMS không có → cột `priority` trong `order_status` để trống, thứ tự hàng đợi = FIFO quan sát được.
- Số xe (`total`): lấy từ danh sách `fleet` trong config, cộng thêm xe lạ nếu xuất hiện trong feed.

### B4. Packer: `offline` lấy theo lịch nghỉ KẾ HOẠCH
`labor.csv` chỉ có giờ nghỉ kế hoạch. Thực tế người chỉ đi nghỉ sau khi làm xong thùng đang làm, nên lúc
tới giờ nghỉ vẫn có thể đang bận. Data Layer kẹp `offline = min(slot không có người, total - busy)` để không vi phạm
`busy + offline <= total`.

### B5. Khâu packer → AGV phải suy ra (Excel trễ tới 2 giờ)
Mốc "đóng gói xong" chỉ có trong Excel. Thùng đã đóng xong mà đang chờ AGV trông giống "đang đóng gói".
Nếu số đơn đang đóng gói > số slot, các đơn bắt đầu sớm nhất được chuyển sang `queued_jobs` stage `agv`
(trong `order_status`: `stage_is_estimated = true`). Ngày 21 seed 42 xảy ra lúc 10:05, 17:55, 19:05.
Đề xuất cho Generator / hệ thống thật: thêm sự kiện `PACK_DONE` (stream) để bỏ suy luận này.

### B6. AGV không có sự kiện hỏng và chuyến về
`resources.agv.offline` luôn = 0; AGV "giao xong" được coi là rảnh ngay (thực tế còn ~2.5 phút chạy về).

### B7. `remaining_min` của job quá hạn — yêu cầu ghi 0
Có yêu cầu: quá thời gian ước lượng thì `remaining_min = 0` và `overdue = true`. Spec v0.1 mục 3 bắt buộc
`remaining_min > 0`, và `ActiveJob` của Twin báo lỗi khi `remaining_min <= 0`.
**Đã làm (v0.1, chỉ thêm):** `active_jobs[].est_remaining_min` = 0 khi overdue. `remaining_min` giữ giá trị sàn 0.5.
Đề xuất v0.2 (cần Nhân): cho phép `remaining_min = 0` nghĩa là "xong ngay ở phút 0" hoặc "chưa biết", và Twin
xử lý ca này (vd. dùng một thời lượng tối thiểu do scenario quy định thay vì 0.5 cố định của Data Layer).

### B8. `arrival_min` của WIP — yêu cầu ghi số âm
Có yêu cầu: `arrival_min` là phút tương đối so với `as_of`, đơn đến trước `as_of` phải âm, đơn tạo khác lúc phải khác nhau.
Spec v0.1 mục 2 định nghĩa ngược lại: `arrival_min ≥ 0`, WIP **dùng 0** vì as_of là phút 0 của Twin, và KPI lead time
chỉ đo từ snapshot. Twin (`MaterialRequest`, `simulation.py`) báo lỗi khi `arrival_min < 0`. Vậy `arrival_min = 0` hiện
tại là đúng contract, không phải lỗi tính toán.
**Đã làm (v0.1, chỉ thêm):** `created_offset_min` ở cấp job (cạnh `request`, không trong `request`).
Đề xuất v0.2 (cần Nhân + Cường vì đổi ý nghĩa KPI `avg_lead_time_min`): hoặc giữ nguyên như hiện tại, hoặc cho phép
`arrival_min < 0` với WIP để lead time tính cả phần chờ trước snapshot.

### B9. `resource_detail.amr.blocked` kèm request_id
Có yêu cầu đổi `blocked` thành list object `{amr_id, request_id}`. `blocked` (list robot_id) đã được dùng trong
`contract_check` và đã thông báo cho nhóm ở mục A, nên đổi kiểu sẽ làm vỡ người đọc hiện có.
**Đã làm:** thêm trường riêng `blocked_jobs`, giữ nguyên `blocked`. contract_check kiểm `blocked_jobs` có đúng các xe
trong `blocked`.

## C. Cho Hải Anh (Dashboard)
`app/streamlit_app.py` đang đọc `data/unified/current_state.json`. Đề xuất đổi sang đường dẫn cố định
`data/gold/latest/` (`state.json`, `order_status.parquet`, `data_quality.json`) — luôn là lần chạy gần nhất,
chỉ được ghi khi đã qua contract_check. Bản theo từng mốc giờ nằm ở `data/gold/<ngày>_seed<seed>/`.
Xem `docs/data_dictionary.md` mục 6.
