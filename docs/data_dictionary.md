# Data dictionary — 6 feed thô, output của Data Layer (Phase 4) và Demand (Phase 6)

Tài liệu này ghi lại những gì đọc được từ `src/generator/` và dữ liệu thật `data/raw/2026-10-21_seed42/`
(sinh bằng `python -m src.generator --seed 42 --date 2026-10-21`). Bổ sung cho `docs/feeds.md`.

## 1. Thư mục raw

```
data/raw/<YYYY-MM-DD>_seed<seed>/        <- Generator CLI / batch (seed THẬT của rng ngày đó)
data/raw/<YYYY-MM-DD>/                   <- layout cũ: generate_day() mặc định, phase8.py, --legacy-dir
  wms.csv  amr.csv  agv.csv  production.csv  labor.csv  excel.csv   <- 6 feed
  master.csv                                                         <- bảng mã tham chiếu (tĩnh)
  _truth/requests.csv, _truth/events.csv                             <- ĐÁP ÁN, Data Layer / Demand KHÔNG đọc
```

Từ Phase 6, `python -m src.generator` ghi vào `<ngày>_seed<seed>` nên đổi seed không ghi đè nhau. `--days N`
dùng seed = seed gốc + i cho ngày thứ i, và tên thư mục ghi đúng seed đó. Thư mục cũ `data/raw/<ngày>/` không rõ seed
(ngày 2026-10-21 có thể là seed 42 hoặc seed 62 nếu sinh bằng `--date 2026-10-01 --days 21`); Data Layer và Demand
chỉ dùng nó khi không có thư mục có seed, và in cảnh báo.

Sinh hàng loạt (`python -m src.generator.batch`, seed gốc trong `batch.seeds` của `config/generator.yaml`):
ngày 21 dùng seed gốc `s`, ngày `d` khác dùng `s + 1000 × (21 − d)`, yêu cầu `0 ≤ s < 1000` → không hai thư mục nào
trùng seed, và ngày 21 seed 42 luôn là dữ liệu demo. Phase 8 (`src/validation/phase8.py`) tự sinh riêng 1 seed/ngày
(seed 42 + i) vào `data/results/phase8_data/raw/<ngày>/`; batch không làm lại việc đó mà bổ sung nhiều seed cho
cùng một ngày.

Số dòng ngày 21 seed 42: wms 430, amr 1719, agv 428, production 32, labor 6, excel 406, master 10.

## 2. Từng feed

Thời gian trong Generator là phút từ 06:00; khi xuất feed mới đổi ra giờ thật, múi +07:00.
Data Layer đổi mọi cột giờ về `datetime` có timezone `Asia/Ho_Chi_Minh`.

### wms.csv — phiếu xuất kho (batch)
| Cột | Kiểu thô | Ví dụ | Ghi chú |
|---|---|---|---|
| `pick_no` | chuỗi | `PICK-00001` | Khóa gốc của lượt giao, tăng theo `created_at`. **= request_id chuẩn** |
| `created_at` | ISO 8601 +07:00 | `2026-10-21T06:00:26+07:00` | Lúc phát yêu cầu (giây bị cắt) |
| `material_code` | chuỗi | `MAT-02` | Mã WMS, map qua master → `M02` |
| `line_code` | chuỗi | `LINE_A` | Mã WMS, map qua master → `L1` |
| `qty_packs` | int | `3` | 2..6 pack → `quantity` trong contract |
| `need_by` | ISO 8601 | `…T06:30:26+07:00` | Hạn = created_at + 30 phút → `due_at` |

Không có cột kho (origin) và không có cột ưu tiên (priority).

### amr.csv — fleet manager AMR (stream, mỗi sự kiện 1 dòng)
| Cột | Kiểu thô | Ví dụ | Ghi chú |
|---|---|---|---|
| `ts` | ISO 8601 | `2026-10-21T06:00:26+07:00` | Lúc xảy ra sự kiện |
| `robot_id` | chuỗi | `AMR-01` | AMR-01..AMR-06 |
| `task_id` | chuỗi | `T-0001` | Trống ở FAULT/RECOVER |
| `event` | chuỗi | `DISPATCH` | Xem bảng dưới |
| `wms_ref` | chuỗi | `PICK-00001` | **Chỉ dòng DISPATCH có** |

| `event` | Ý nghĩa | Trạng thái robot sau sự kiện |
|---|---|---|
| `DISPATCH` | Nhận task, rời kho | busy |
| `ARRIVE_GATE` | Tới cổng, đứng chờ người nhận thùng (AMR bị giữ) | busy |
| `RELEASE` | Thùng được lấy ra = packer bắt đầu; robot chạy về kho | busy (đang chạy về) |
| `RETURNED` | Về tới kho, rảnh | idle |
| `FAULT` / `RECOVER` | Hỏng / sửa xong (chỉ khi robot đang rảnh) | offline / idle |

### agv.csv — hệ thống AGV (mỗi job 1 dòng)
| Cột | Kiểu thô | Ví dụ | Ghi chú |
|---|---|---|---|
| `job_id` | chuỗi | `J-0001` | Đánh số theo thứ tự LOAD |
| `agv_id` | chuỗi | `AGV-01` | AGV-01..AGV-04 |
| `label` | chuỗi | `PK1` | Nhãn = "PK" + số phiếu không có số 0 đứng đầu |
| `station_code` | chuỗi | `ST-A1` | Mã trạm AGV, map qua master → `L1` |
| `load_epoch` | int | `1792537595` | Epoch **giây** UTC, lúc xếp hộp lên AGV |
| `deliver_epoch` | int / trống | `1792537768` | Epoch giây; trống nếu cuối ngày chưa giao |

Chuyến về của AGV không có trong feed (chỉ có trong `_truth`).

### production.csv — kế hoạch sản xuất (batch, đầu ngày)
`line_code` (LINE_A), `hour_start` (ISO, đầu mỗi giờ), `product` (P100/P200), `planned_qty` (int).

### labor.csv — bảng phân ca (batch, đầu ngày)
`date` (YYYY-MM-DD), `worker_id` (W01..W06), `slot` (PK-1..PK-3), `shift` (S1/S2),
`shift_start`, `shift_end`, `break_start`, `break_end` (đều dạng `HH:MM`).
Giờ nghỉ là **kế hoạch**. Trong Generator, slot chỉ nghỉ sau khi xong lượt đang làm, nên nghỉ thật có thể trễ hơn.

### excel.csv — log đóng gói nhập tay (manual)
| Cột | Kiểu thô | Ví dụ | Ghi chú |
|---|---|---|---|
| `worker` | chuỗi | `W01` | Người đóng gói |
| `pick` | int | `1` | Số phiếu trần, không có tiền tố |
| `start` | `HH:MM` | `06:02` | Bắt đầu tách pack (giây bị cắt) |
| `end` | `HH:MM` | `06:06` | Xong tách pack (giây bị cắt). **Nguồn duy nhất cho mốc "đóng gói xong"** |
| `entered_at` | ISO 8601 | `2026-10-21T08:00:00+07:00` | **Lúc tổ trưởng nhập** = mỗi 2 giờ (08:00, 10:00, …) |

Khoảng 5% dòng bị thiếu (nhập tay quên).

### master.csv — bảng mã (tham chiếu, không phải feed)
`kind` (line/material), `system` (wms/agv), `code`, `canonical_id`.
LINE_A, ST-A1 → L1; LINE_B, ST-B1 → L2; MAT-01..06 → M01..M06.

## 3. Chuỗi nối ID (đã đọc code `feeds.py`)

```
wms.pick_no "PICK-00012"  ==  request_id chuẩn
   ▲ amr.wms_ref (chỉ ở dòng DISPATCH, cùng dạng "PICK-00012")
   │      └─ amr.task_id "T-0007" ── các dòng ARRIVE_GATE/RELEASE/RETURNED chỉ mang task_id
   ├─ agv.label  "PK12"   (số phiếu, bỏ số 0 đứng đầu)
   └─ excel.pick 12       (số phiếu trần, int)
line:     wms.line_code / agv.station_code ──master──► L1, L2
material: wms.material_code ──master──► M01..M06
origin:   không có trong feed → lấy từ config (STORE-1, v0.1 chỉ có 1 kho)
```

Các cách Generator làm lệch ID: tiền tố khác (`PICK-` / `PK` / không có), số 0 đứng đầu khác
(`00012` / `12`), kiểu khác (chuỗi / int), mã hệ thống khác (`LINE_A` / `ST-A1` / `L1`).
Data Layer còn chịu được thêm (để chống dữ liệu thật bẩn hơn): chữ thường, khoảng trắng, `PK-12`, `PICK-12`.
Generator **không** sinh ID sai chính tả, dòng trùng, giờ trống hay giá trị ngoài khoảng → các lỗi này chỉ có trong fixtures test.

## 4. Thời điểm ghi nhận và quy tắc cắt theo `as_of`

Mỗi cột giờ thuộc một trong hai loại (`EVENT_TIME_COLUMNS` trong `ingest.py`):
- **Sự kiện thực tế**: chỉ biết được khi đã xảy ra. Dòng bị bỏ nếu cột cắt > as_of; trong dòng được giữ,
  mọi ô sự kiện > as_of bị đặt thành rỗng (NaT). Số ô bị xóa ghi ở `feed_rows.<feed>.future_cells_masked`.
- **Kế hoạch / hạn**: công bố trước, được phép nằm sau as_of → giữ nguyên.

| Feed | Cột sự kiện thực tế | Cột kế hoạch / hạn | Ghi nhận riêng? | Xử lý |
|---|---|---|---|---|
| wms | `created_at` | `need_by` (hạn giao) | Không | Bỏ dòng `created_at > as_of` (giả định: phiếu có ngay khi tạo) |
| amr | `ts` | — | Không | Bỏ dòng `ts > as_of` (giả định: stream, ghi ngay) |
| agv | `load_epoch`, `deliver_epoch` | — | Không | Giữ dòng từ lúc LOAD; `deliver_epoch > as_of` → NaT (giả định: job hiện ra lúc LOAD) |
| production | — | `hour_start`, `planned_qty` | Không | Toàn bộ là kế hoạch, giữ hết nếu đã công bố (giả định: công bố lúc `hour_start` sớm nhất) |
| labor | — | `shift_start/end`, `break_start/end` | Không | Toàn bộ là KẾ HOẠCH, giữ hết nếu đã công bố (giả định: lúc `shift_start` sớm nhất). **Feed không có nghỉ thực tế** (chỉ có trong `_truth`) |
| excel | `start`, `end`, `entered_at` | — | **Có**: `entered_at` | Bỏ dòng `entered_at > as_of`; `start`/`end` luôn ≤ `entered_at` nên cũng ≤ as_of |

Nhu cầu thực tế (request được tạo) nằm ở `wms.created_at`, đã cắt theo as_of. Production/labor không chứa sự kiện thực tế nào.

Dòng có cột giờ dùng để cắt bị trống/không đọc được thì **vẫn giữ** qua ingest, để bước validate cách ly với lý do `missing_timestamp`.

## 5. Quy tắc thứ tự quan sát được (không tự đặt quy tắc điều phối)

Kiểm trên ngày 21 seed 42:
- AMR nhận đơn đúng thứ tự `created_at` (cũng là thứ tự `pick_no`): 430/430 DISPATCH theo đúng thứ tự.
- Packer nhận thùng đúng thứ tự `ARRIVE_GATE`: 429/429.

→ `queued_jobs` và `order_status` sắp FIFO: hàng AMR theo `created_at`, hàng packer theo `amr_arrive_gate_at`,
hàng AGV theo `packer_end_at`; hòa thì theo `request_id`. WMS không có priority nên cột `priority` để trống.

## 6. Output của Data Layer

```
data/silver/<ngày>_seed<seed>/   6 bảng sạch (parquet, đã cắt theo as_of lần chạy gần nhất) + quarantine.csv
data/gold/<ngày>_seed<seed>/     state_HHMM.json, order_status_HHMM.parquet, journey.parquet,
                                 quarantine.csv, data_quality_HHMM.json
data/gold/latest/                state.json, order_status.parquet, data_quality.json
```

`data/gold/latest/` là bản sao của **lần chạy gần nhất** (không phải as_of muộn nhất), tên file cố định
để Dashboard đọc một đường dẫn không đổi. Chỉ được ghi khi output đã qua `contract_check`.
Ngày/giờ của dữ liệu xem ở `current_state.as_of` trong `state.json`.

### quarantine.csv
`feed`, `row_ref` (`<feed>.csv:<số dòng trong file, header = dòng 1>`), `check`, `reason`.

| reason | Bước | Ý nghĩa |
|---|---|---|
| `duplicate_row` | ingest | Dòng trùng hoàn toàn với dòng trước (bị bỏ, vẫn ghi lại) |
| `missing_value` | validate | Cột bắt buộc bị trống |
| `missing_timestamp` | validate | Cột giờ trống hoặc không đọc được |
| `not_a_number` | validate | Cột số không đọc được |
| `out_of_range` | validate | Giá trị ngoài khoảng trong `config/data_layer.yaml` |
| `invalid_event` | validate | `amr.event` không thuộc tập hợp lệ |
| `bad_request_id` / `bad_label` / `bad_pick` / `bad_task_id` | mapper | ID không chuẩn hóa được |
| `unknown_line_code` / `unknown_material_code` / `unknown_station_code` | mapper | Mã không có trong master |
| `duplicate_pick_no` | mapper | 2 phiếu WMS cùng request_id |
| `ref_not_in_wms` | mapper | DISPATCH trỏ tới phiếu không có trong WMS |
| `duplicate_task` | mapper | 1 task_id được DISPATCH 2 lần |
| `duplicate_dispatch` | mapper | 1 phiếu được DISPATCH 2 lần |
| `task_not_in_wms` | mapper | Sự kiện AMR mang task_id không truy được về phiếu WMS |
| `label_not_in_wms` / `duplicate_label` | mapper | AGV job không nối được / nối trùng |
| `station_line_mismatch` | mapper | Trạm AGV khác line của phiếu WMS |
| `pick_not_in_wms` / `duplicate_pick` | mapper | Dòng Excel không nối được / nối trùng |

### journey.parquet (mỗi phiếu WMS 1 dòng, tại as_of)
`request_id, origin_id, line_id, material_id, quantity, created_at, due_at,
amr_task_id, amr_id, amr_dispatch_at, amr_arrive_gate_at, packer_start_at, packer_end_at, worker_id,
agv_job_id, agv_id, agv_load_at, delivered_at, current_stage,
amr_wait_min, amr_travel_min, gate_wait_min, pack_min, agv_wait_min, agv_travel_min, lead_time_min, as_of`

- Không có sự kiện "soạn hàng" riêng trong feed: phiếu WMS được tạo là coi như sẵn sàng cho AMR.
- `packer_start_at` = `ts` của AMR `RELEASE` (chính xác tới giây). `packer_end_at` = Excel `end` (chính xác tới phút, trễ tới 2 giờ).
- `current_stage`: `amr_queued` → `amr_active` → `packer_queued` → `packer_active` → `agv_queued` → `agv_active` → `delivered`,
  suy ra từ mốc quan sát gần nhất. Không có Excel thì đơn đã RELEASE mà chưa LOAD vẫn là `packer_active`.

### order_status_HHMM.parquet (mỗi đơn mở 1 dòng)
Đơn mở = `created_at <= as_of` và chưa thấy `delivered_at` tại as_of (đơn giao sau as_of vẫn là mở).

| Cột | Ý nghĩa |
|---|---|
| `request_id, origin_id, line_id, material_id, quantity` | Thông tin đơn (ID chuẩn) |
| `priority` | Trống — WMS không có |
| `created_at`, `due_at` | Lúc tạo, hạn (`need_by`) |
| `current_stage`, `stage`, `stage_status` | vd. `packer_queued` = `packer` + `queued` |
| `stage_is_estimated` | `true` nếu khâu là suy ra (xem dưới), không quan sát trực tiếp |
| `stage_entered_at` | Lúc vào khâu hiện tại |
| `queue_position` | Vị trí FIFO trong hàng đợi của khâu (chỉ đơn `queued`) |
| `wait_min` | Phút từ lúc tạo đơn tới as_of |
| `time_in_stage_min` | Phút đã ở khâu hiện tại |
| `est_remaining_min` | **Ước lượng** phút còn lại (chỉ đơn `active`); 0 nếu overdue |
| `is_estimated`, `estimate_method` | Cờ + cách tính cho mọi giá trị ước lượng của dòng |
| `overdue` | Đã quá thời gian dự kiến mà chưa thấy sự kiện kết thúc khâu |
| `late` | `as_of > due_at` |
| `amr_id, agv_id, worker_id` | Xe/người đã gắn với đơn (nếu đã biết) |
| `last_update_at`, `age_min`, `stale` | Sự kiện mới nhất của đơn, cách as_of bao lâu, quá `order_stale_after_min` chưa |

### Trường ước lượng (luôn có `is_estimated = true` + `estimate_method`)
1. `current_state.active_jobs[].remaining_min` và `order_status.est_remaining_min`:
   `trung vị thời lượng khâu trên các đơn đã xong khâu đó trước as_of − thời gian đã chạy`.
   Thời lượng khâu: AMR = ARRIVE_GATE − DISPATCH; packer = Excel end − RELEASE; AGV = deliver − load.
   Ít hơn `estimate.min_samples` mẫu thì dùng `estimate.fallback_min` trong config (ghi rõ trong `estimate_method`).
   **Định nghĩa overdue:** `est_remaining_min = max(trung vị − thời gian đã chạy, 0)`.
   Nếu thời gian ước lượng đã vượt quá (trung vị − đã chạy ≤ 0) thì `est_remaining_min = 0` và `overdue = true`.
   Ngược lại `overdue = false` và `est_remaining_min > 0`. Tức là luôn có `overdue ⇔ est_remaining_min = 0`
   (contract_check kiểm). Áp dụng cho `order_status.est_remaining_min` và `current_state.active_jobs[].est_remaining_min`.
   `active_jobs[].remaining_min` là trường contract v0.1, bắt buộc > 0, nên đơn overdue ghi giá trị sàn
   `estimate.overdue_remaining_min` (0.5). Đây chỉ là giá trị kỹ thuật để Twin chạy được, **không phải ước lượng**.
   Dashboard/KPI đọc `est_remaining_min` + `overdue`, không đọc `remaining_min` để hiển thị thời gian còn lại.
   Đơn không overdue mà làm tròn ra 0.00 thì `remaining_min` ghi 0.01 (vẫn > 0).
3. `active_jobs[].completion_unconfirmed` (bool): `true` khi job `overdue` **và** mốc kết thúc khâu đó đến từ
   nguồn `manual` (theo `feeds.<nguồn>.type` trong config). Mốc kết thúc: AMR = ARRIVE_GATE (feed amr, stream),
   packer = Excel `end` (feed excel, manual), AGV = deliver (feed agv, stream). Hiện chỉ khâu packer có thể `true`.
   Nghĩa: job có thể đã xong nhưng chưa được ghi nhận (Excel nhập trễ tới 2 giờ), không chắc là đang trễ thật.
4. `queued_jobs[].created_offset_min`, `active_jobs[].created_offset_min`: phút từ `as_of` tới lúc tạo đơn
   (WMS `created_at`), làm tròn 2 chữ số. Đơn tạo trước `as_of` thì **âm**, ví dụ tạo lúc 13:40 với as_of 14:10 là −30.
   Đây là quan sát, không phải ước lượng.
   `request.arrival_min` (contract) vẫn **luôn = 0** cho WIP. Theo spec v0.1 mục 2, `as_of` là phút 0 của Twin
   và WIP đã có mặt trước đó, nên `arrival_min = 0` là đúng định nghĩa chứ không phải lỗi.
   Muốn biết đơn đã chờ bao lâu thì đọc `created_offset_min` (hoặc `order_status.wait_min = −created_offset_min`).
2. Khâu suy ra (`stage_is_estimated = true`): Excel trễ nên thùng đã đóng xong, đang chờ AGV trông giống
   "đang đóng gói". Một slot chỉ đóng 1 thùng, nên nếu số đơn `packer_active` > số slot thì các đơn RELEASE sớm nhất
   được chuyển sang `agv_queued`, `stage_entered_at` = RELEASE + trung vị đóng gói.

### Trạng thái tài nguyên (`resource_detail` trong state)
- AMR: `resource_detail.amr = {busy, blocked, idle, offline}` (list robot_id), theo sự kiện gần nhất
  (`amr_status_by_event` trong config): DISPATCH → busy (đang chở), ARRIVE_GATE → **blocked** (đứng chờ ở cổng),
  RELEASE → busy (đang chạy về), RETURNED/RECOVER/chưa có sự kiện → idle (thật sự rảnh), FAULT → offline.
  `len(busy) + len(blocked) + len(idle) + len(offline) = resources.amr.total` (contract_check kiểm).
  `resource_detail.amr.blocked_jobs`: list `{"amr_id", "request_id"}`, mỗi xe blocked một phần tử, cùng thứ tự với
  `blocked`. `request_id` lấy từ sự kiện ARRIVE_GATE gần nhất của xe (nối qua task_id). Không nối được thì `null`.
  `blocked` vẫn là list robot_id như cũ.
  Hai sự kiện cùng giây (RETURNED rồi DISPATCH) giữ đúng thứ tự dòng trong file.
- AGV: job gần nhất đã LOAD mà chưa thấy deliver → busy, còn lại idle (feed không có hỏng / chuyến về).
- Packer: công nhân sẵn sàng = trong ca và không nghỉ, theo `labor.csv` (kế hoạch).

### Số đếm tài nguyên (`resource_counts` trong state)
Số đếm theo trạng thái **vật lý**, khớp với `resource_detail`:
- `amr`: `{total, busy, blocked, idle, offline}`, mỗi số = độ dài list tương ứng trong `resource_detail.amr`.
- `agv`: `{total, busy, idle, offline}`, mỗi số = độ dài list tương ứng trong `resource_detail.agv`.
- `packer`: `{total, busy, idle, offline}`, với `busy` = số active job packer, `offline` = như `resources.packer.offline`,
  `idle = total − busy − offline`. Không có danh sách slot rảnh vì Excel trễ nên không biết slot nào đang làm.
- Luôn có `busy + blocked (nếu có) + idle + offline = total`. `total` và `offline` bằng `resources.<loại>`.

Khác `resources.<loại>.busy` (contract): `resources.amr.busy` = số **job** đang ở khâu AMR, không tính xe đang chạy
về kho (RELEASE). Vì vậy `resources.amr.total − busy − offline` **không** bằng số AMR rảnh. Ngày 21 seed 42,
đẳng thức `total − busy − blocked − offline = len(idle)` tính trên `resources.amr` sai ở 29/31 mốc mỗi 30 phút.
Muốn biết số xe rảnh thật thì đọc `resource_counts.<loại>.idle`.

### data_quality_HHMM.json
`feed_rows` (mỗi feed: `raw_rows`, `future_rows_cut`, `future_cells_masked`, `duplicates_dropped`, `rows_at_as_of`, `quarantined_rows`,
`clean_rows_after_validate`), `quarantine_total`, `quarantine_by_reason`, `quarantine_by_feed_reason`,
`link_rate` (amr/agv/excel/overall), `estimated` (danh sách trường ước lượng, tỉ lệ đơn có ước lượng, số đơn khâu suy ra),
`overdue_orders`, `late_orders`, `stale_sources`, `stale_source_count`, `stale_orders`.
