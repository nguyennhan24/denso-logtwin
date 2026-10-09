# Phase 4 — Unified Data Layer: tổng kết

**Người phụ trách:** Trung · **Cập nhật:** 2026-10-09 · **Contract:** v0.1 (không đổi trường nào)
**Gate Phase 4:** Twin và Dashboard chỉ đọc `CurrentLogisticsState` / file gold, không đọc feed thô. **Đã đạt.**

Tài liệu chi tiết đi kèm:
- `docs/data_dictionary.md`: schema 6 feed, chuỗi nối ID, quy tắc cắt theo `as_of`, định nghĩa mọi cột output.
- `docs/contract_change_proposal.md`: trường đã thêm và các đề xuất contract v0.2 cần nhóm duyệt.

---

## 1. Đề bài

Đề D2 (DENSO): Digital Twin Control Tower cho luồng **Kho → AMR → Packer (cổng) → AGV → Line**.
Kiến trúc DATA → STATE → TWIN → KPI → DECISION. Phase 4 là tầng **DATA → STATE**.

Generator (Phase 1) sinh 6 feed "bẩn" giống nhà máy thật:

| Feed | Loại | Độ bẩn cố ý |
|---|---|---|
| `wms.csv` | batch | Không có cột kho, không có priority |
| `amr.csv` | stream | Chỉ dòng DISPATCH mang mã phiếu, các sự kiện sau chỉ có `task_id` |
| `agv.csv` | stream | Giờ dạng epoch giây UTC, nhãn `PK12` thay vì `PICK-00012`, mã trạm riêng |
| `production.csv`, `labor.csv` | batch đầu ngày | Chỉ là kế hoạch, giờ dạng `HH:MM`, không có nghỉ thực tế |
| `excel.csv` | manual | Nhập tay mỗi 2 giờ (trễ tới 2 giờ), thiếu ~5% dòng, số phiếu trần `12`, giờ cắt phút |

Thư mục `_truth/` là đáp án của Generator. Data Layer **không được đọc**, chỉ test được dùng để đo độ đúng.

## 2. Mục tiêu

Biến 6 feed bẩn thành dữ liệu sạch, **đúng tại bất kỳ thời điểm `as_of` nào**:
1. `CurrentLogisticsState`: ảnh chụp nhà máy tại `as_of`, là phút 0 của Twin và tab NOW của Dashboard.
2. Bảng hành trình `journey`: mỗi lượt giao một dòng, mọi mốc đã biết. Dùng cho calibrate và backtest.
3. Bảng `order_status`: mỗi đơn đang mở một dòng, kèm khâu, vị trí hàng đợi, ước lượng, cờ trễ.
4. Báo cáo chất lượng dữ liệu: số dòng lỗi, tỉ lệ nối ID, độ tươi từng nguồn, phần nào là ước lượng.

## 3. Yêu cầu bắt buộc

| # | Yêu cầu | Cách đáp ứng |
|---|---|---|
| R1 | Không nhìn tương lai | Mọi bước nhận `as_of`. Dòng có sự kiện sau `as_of` bị bỏ, ô sự kiện sau `as_of` thành rỗng. Excel cắt theo `entered_at` (lúc nhập), không theo giờ đóng gói |
| R2 | Không đọc `_truth/` | Chỉ đọc 6 feed + `master.csv`. Có test theo dõi mọi lần mở file |
| R3 | Không vứt dữ liệu lỗi im lặng | Dòng lỗi vào `quarantine.csv` kèm `feed`, `row_ref`, `check`, `reason`, và được đếm vào báo cáo |
| R4 | Không biết thì ước lượng từ dữ liệu, ghi rõ là ước lượng | Ước lượng = trung vị lịch sử trước `as_of`. Luôn kèm `is_estimated = true` + `estimate_method` |
| R5 | Đúng contract v0.1 | Bước cuối kiểm bằng pydantic + validator chung của nhóm. Sai thì **không ghi gold** |
| R6 | Tái lập | Hàm thuần, không biến toàn cục, cùng input cùng output |
| R7 | Theo quy tắc repo | Giờ ra ngoài là ISO 8601 +07:00. Ngưỡng/giả định nằm trong `config/data_layer.yaml`, không viết cứng |

## 4. Những gì đã đạt được

- **Pipeline 7 bước chạy end-to-end** bằng một lệnh CLI, khoảng 0,2 giây cho một `as_of`.
- **Nối ID 100%** trên dữ liệu thật ngày 21 seed 42. Chịu được thêm các biến thể bẩn hơn Generator: chữ thường, khoảng trắng, `PK-12`, `12.0`.
- **Tái dựng khâu đúng 99,6%** so với truth. Chi tiết ở mục 8.
- **State hợp lệ contract v0.1** ở mọi mốc thử. Nạp được bằng dataclass của Twin mà không sửa code Twin.
- **Xử lý Excel trễ**: nếu số đơn "đang đóng gói" vượt số slot, các đơn bắt đầu sớm nhất được suy ra là đã đóng xong và đang chờ AGV, có gắn cờ `stage_is_estimated`.
- **Thấy được AMR bị giữ ở cổng (blocked)**. Contract v0.1 không biểu diễn được trạng thái này, nên Data Layer ghi riêng kèm đơn mà xe đang giữ. Đây là bằng chứng dữ liệu cho hiện tượng nghẽn ở packer.
- **Độ tươi hai mức**: theo nguồn (stream/batch/manual, tuổi, stale) và theo đơn (lâu không có sự kiện mới).
- **Cổng contract duy nhất vào gold**: không có đường nào ghi gold mà bỏ qua bước kiểm.

Các trường thêm vào state, không thuộc contract v0.1 (Twin bỏ qua được):

| Trường | Ý nghĩa |
|---|---|
| `active_jobs[].is_estimated`, `estimate_method` | `remaining_min` là ước lượng và cách tính |
| `active_jobs[].overdue`, `est_remaining_min` | Quá thời gian ước lượng thì `est_remaining_min = 0`, `overdue = true` |
| `active_jobs[].completion_unconfirmed` | Quá hạn mà mốc kết thúc khâu đến từ Excel: có thể đã xong nhưng chưa được ghi |
| `*_jobs[].created_offset_min` | Phút lúc tạo đơn so với `as_of`, âm. `arrival_min` contract vẫn = 0 |
| `resource_counts` | Số xe theo trạng thái vật lý: busy + blocked + idle + offline = total |
| `resource_detail` | Danh sách ID xe theo trạng thái, `blocked_jobs` (xe nào giữ đơn nào), ca làm packer |
| `open_request_count`, `source_freshness` | Số đơn mở, độ tươi từng nguồn |

## 5. Cấu trúc thư mục

```
config/data_layer.yaml          mọi ngưỡng, danh sách xe, map sự kiện -> trạng thái, freshness, ước lượng
src/data_layer/
  __init__.py                   FEEDS, STAGES, load_config
  __main__.py                   CLI: python -m src.data_layer --day 21 --seed 42 --as-of 14:10
  ingest.py                     bước 1: đọc, đổi giờ, cắt theo as_of, bỏ dòng trùng
  validate.py                   bước 2: kiểm từng dòng, cách ly dòng lỗi, ghi silver
  mapper.py                     bước 3: chuẩn hóa ID, nối feed, dựng journey
  state_builder.py              bước 4: order_status, trạng thái xe, ước lượng, CurrentLogisticsState
  freshness.py                  bước 5: độ tươi theo nguồn và theo đơn
  pipeline.py                   chạy 7 bước, bước 6 báo cáo chất lượng
  contract_check.py             bước 7: kiểm contract, đúng mới ghi gold
tests/
  test_data_layer.py            61 test (fixture viết tay + dữ liệu Generator thật)
  fixtures/raw_small/           CSV nhỏ viết tay, có lỗi cố ý
docs/
  data_dictionary.md            từ điển dữ liệu
  contract_change_proposal.md   đề xuất v0.2
  phase4_data_layer.md          tài liệu này

data/                           (không commit, nằm trong .gitignore)
  raw/<ngày>/                   6 feed + master.csv + _truth/ (do Generator sinh)
  silver/<ngày>_seed<seed>/     6 bảng sạch .parquet + quarantine.csv
  gold/<ngày>_seed<seed>/       state_HHMM.json, order_status_HHMM.parquet, journey.parquet,
                                quarantine.csv, data_quality_HHMM.json
  gold/latest/                  state.json, order_status.parquet, data_quality.json (lần chạy gần nhất)
```

Phụ thuộc mới trong `requirements.txt`: `pydantic>=2.5` (kiểm contract), `pyarrow>=15` (ghi parquet).

## 6. Flow dữ liệu

```
data/raw/<ngày>/*.csv                          BRONZE  nguyên gốc, không sửa
   │  1 ingest      giờ -> Asia/Ho_Chi_Minh, cắt theo as_of, bỏ dòng trùng
   │  2 validate    thiếu giá trị, sai kiểu, ngoài khoảng, sự kiện lạ -> quarantine
   ▼
data/silver/<ngày>_seed<seed>/                 SILVER  sạch, đúng kiểu
   │  3 mapper      PICK-00012 / PK12 / 12 / task_id -> request_id; mã line, vật tư qua master
   │  4 state       journey -> order_status -> trạng thái xe -> CurrentLogisticsState
   │  5 freshness   độ tươi nguồn + đơn
   │  6 quality     báo cáo chất lượng
   │  7 contract    pydantic + contracts/validate_contracts.py; sai -> ContractError, không ghi
   ▼
data/gold/<ngày>_seed<seed>/  +  data/gold/latest/      GOLD  theo contract
   ├─► Twin (Nhân): current_state làm phút 0 của mô phỏng
   ├─► Dashboard (Hải Anh): tab NOW + chất lượng dữ liệu, đọc data/gold/latest/
   └─► Backtest / calibrate: journey.parquet
```

Chuỗi nối ID:

```
wms.pick_no "PICK-00012" = request_id
  ├─ amr.wms_ref (dòng DISPATCH) -> amr.task_id -> ARRIVE_GATE / RELEASE / RETURNED
  ├─ agv.label "PK12"
  └─ excel.pick 12
line: LINE_A, ST-A1 -> L1 · material: MAT-02 -> M02 · origin: STORE-1 (config)
```

## 7. Pipeline: logic chính ở từng bước

**Bước 1, ingest.** Cột giờ chia hai loại. Cột sự kiện thực tế chỉ được biết khi đã xảy ra, nên giá trị sau `as_of` bị xóa. Cột kế hoạch hoặc hạn, như `need_by` hay ca làm, được giữ nguyên. Mỗi dòng có `row_ref` để truy ngược về file gốc.

**Bước 2, validate.** Có ba nhóm kiểm: cột bắt buộc, kiểu dữ liệu, khoảng giá trị trong config. Bước này cố ý không kiểm liên kết giữa các feed. Lúc này ID chưa chuẩn hóa, kiểm sớm sẽ cách ly nhầm hàng loạt.

**Bước 3, mapper.** Chuẩn hóa ID bằng regex rồi nối mọi feed về `request_id`. Dòng không nối được vào quarantine với lý do cụ thể, ví dụ `task_not_in_wms` hay `label_not_in_wms`. Kết quả là `journey` với mọi mốc, thời lượng từng khâu và `current_stage`.

**Bước 4, state.** Có bốn phần:
- **Khâu hiện tại** lấy theo mốc quan sát gần nhất: `amr_queued` → `amr_active` → `packer_queued` → `packer_active` → `agv_queued` → `agv_active` → `delivered`.
- **Hàng đợi** sắp FIFO theo mốc vào khâu. Thứ tự này đã kiểm trên dữ liệu: AMR nhận đơn đúng thứ tự tạo 430/430 lần, packer nhận thùng đúng thứ tự tới cổng 429/429 lần.
- **Ước lượng thời gian còn lại** = trung vị thời lượng khâu trong lịch sử trước `as_of`, trừ thời gian đã chạy. Dưới 5 mẫu thì dùng giá trị mặc định trong config.
- **Trạng thái xe** lấy theo sự kiện gần nhất. AMR: DISPATCH và RELEASE là busy, ARRIVE_GATE là blocked, RETURNED và RECOVER là idle, FAULT là offline. AGV: đã LOAD mà chưa giao là busy. Packer: người trong ca và không nghỉ theo lịch kế hoạch.

**Bước 5, freshness.** Mỗi nguồn có `last_update`, `age_min` và `stale` theo ngưỡng riêng: stream 5–15 phút, batch 15 phút hoặc 1 ngày, Excel 150 phút. Đơn mở quá 30 phút không có sự kiện mới thì bị đánh dấu stale.

**Bước 6, quality.** Báo cáo gồm số dòng từng feed, số dòng cách ly theo lý do, tỉ lệ nối ID, tỉ lệ đơn phải ước lượng, số đơn overdue, late và stale.

**Bước 7, contract.** Kiểm schema và kiểu theo spec v0.1, quy tắc `busy` = số active job, các trường thêm khớp nhau, và validator chung của nhóm. Chỉ khi tất cả đúng mới ghi silver, gold và `latest/`.

## 8. Kiểm thử và kết quả

Chạy: `.venv/Scripts/python.exe -m pytest -q`. Lệnh `python` mặc định trên máy Trung là 3.12, thiếu pydantic.

**Kết quả toàn repo: 68 passed, 1 skipped.** Gồm 61 test Data Layer và 8 test Generator.

| Nhóm test | Dữ liệu | Kiểm gì |
|---|---|---|
| Ingest (8) | fixture | Không mở `_truth/`; không còn dòng tương lai; AGV giữa LOAD và giao; giữ cột kế hoạch; đọc mọi định dạng giờ; dòng trùng được ghi lại |
| Validate (3) | fixture | Dòng lỗi bị cách ly đúng lý do; ID viết sai không bị cách ly nhầm ở bước này |
| Mapper (4) | fixture | Mọi biến thể ID nối đúng; dòng không nối được vào quarantine; tổng số dòng khớp báo cáo |
| State (8) | fixture | busy + blocked + idle + offline = total; blocked_jobs đúng thùng chờ cổng; `arrival_min = 0` và `created_offset_min` khác nhau; FIFO; overdue thì `est_remaining_min = 0`; ước lượng có cờ |
| Freshness (3) | fixture | Đơn stale, nguồn stale, `feed_updated_at` không vượt `as_of` |
| Contract (18) | fixture | Gold chỉ ghi khi đúng; 16 kiểu làm hỏng state đều bị chặn và không ghi file nào; state nạp được bằng dataclass Twin |
| Dữ liệu thật (17) | ngày 21 seed 42 | Nối ID ≥ 95%; bất biến state ở 5 mốc; mốc thời gian khớp truth; khâu khớp truth ở 4 mốc; AMR hỏng thì offline; state dựng từ raw đầy đủ **giống hệt** raw đã xóa mọi thứ sau `as_of` ở 3 mốc |

Bài bị skip: đề nêu kịch bản AMR-03 hỏng 14:05–14:40, nhưng Generator hỏng ngẫu nhiên. Ngày 21 seed 42 AMR-03 hỏng lúc 06:47–06:57, nên test tự bỏ qua.

**Đo trên dữ liệu thật ngày 21 seed 42:**

| Chỉ số | Kết quả |
|---|---|
| Số phiếu WMS cả ngày | 430 |
| Tỉ lệ nối ID cả ngày | 2551 / 2551 dòng = 100% |
| Dòng bị cách ly | 0 (Generator chỉ làm lệch ID, không sinh giá trị hỏng) |
| Độ phủ Excel | 406 / 429 thùng đã đóng gói có dòng Excel (~5% thiếu, đúng thiết kế) |
| Khâu khớp truth, 31 mốc 06:30–21:30 mỗi 30 phút | 280 / 281 lượt đơn mở = **99,6%** |
| Thời gian chạy | ~0,2 giây / mốc |

Lượt lệch duy nhất: PICK-00015 lúc 06:30. Theo truth, xe tới cổng lúc 06:30:00,6. Feed AMR cắt phần lẻ giây nên ghi 06:30:00, và Data Layer coi là đã tới. Đây là giới hạn độ phân giải của feed, không phải lỗi logic.

**Ảnh chụp 14:10 ngày 21 seed 42** (dùng để review):
- 13 đơn mở: 6 đang xếp hàng, 7 đang được phục vụ.
- AMR: 4 xe busy, 2 blocked (AMR-02 giữ PICK-00215, AMR-03 giữ PICK-00214), 0 idle, trong khi 4 đơn chờ AMR.
- Packer: 3/3 slot bận. PICK-00210 overdue, có `completion_unconfirmed = true`.
- AGV: 4/4 rảnh. Mọi nguồn đều tươi.

## 9. Lưu ý cho các phase sau

**Cho Twin (Nhân):**
- `resources.amr.busy` theo contract chỉ đếm **job** ở khâu AMR. Nó không tính xe đứng ở cổng hay xe đang chạy về. Vì vậy `total − busy − offline` cao hơn số AMR rảnh thật ở 29/31 mốc trong ngày. Muốn khởi tạo đúng thì đọc `resource_counts.amr.idle`, hoặc duyệt đề xuất B1 trong `contract_change_proposal.md`.
- Job overdue có `remaining_min = 0.5`. Đây là giá trị sàn kỹ thuật vì contract đòi > 0, không phải ước lượng (đề xuất B7).
- `arrival_min` của WIP luôn = 0 theo spec. Lead time trong Twin chỉ đo từ snapshot (đề xuất B8).

**Cho Dashboard (Hải Anh):**
- Đọc `data/gold/latest/`, không đọc `data/raw/` hay `data/unified/`.
- Hiển thị thời gian còn lại bằng `est_remaining_min` kèm `overdue`, không dùng `remaining_min`.
- Đơn có `completion_unconfirmed = true` nên hiện là "có thể đã xong, chờ Excel", không hiện là trễ.
- `source_freshness` và `data_quality.json` đủ để làm panel chất lượng dữ liệu.

**Cho Scenario và Backtest (Cường, Nhân):**
- `journey.parquet` có đủ thời lượng từng khâu (`amr_travel_min`, `gate_wait_min`, `pack_min`, `agv_travel_min`, …) để calibrate phân phối.
- `pack_min` chỉ chính xác tới phút vì Excel ghi `HH:MM`.

**Giới hạn đã biết của dữ liệu:**
- AGV không có sự kiện hỏng và chuyến về, nên `resources.agv.offline` luôn 0 và AGV được coi là rảnh ngay sau khi giao.
- Giờ nghỉ packer lấy theo kế hoạch. Thực tế người chỉ đi nghỉ khi xong thùng đang làm.
- Mốc "đóng gói xong" chỉ có trong Excel, trễ tới 2 giờ. Đề xuất hệ thống thật thêm sự kiện stream `PACK_DONE`.
- `origin_id` lấy từ config vì WMS không có cột kho. Khi có nhiều kho phải bổ sung nguồn.

**Quy trình khi đổi contract:** mọi đề xuất ở mục B của `contract_change_proposal.md` đang chờ nhóm duyệt. Muốn áp dụng thì tăng lên v0.2, sửa spec, fixture, `contracts/validate_contracts.py`, `contract_check.py` và dataclass Twin **cùng lúc**, rồi chạy lại toàn bộ pytest.

**Việc còn mở:**
- Toàn bộ code Phase 4 chưa được commit.
- Mới đo độ đúng trên ngày 21 seed 42. Nên chạy thêm vài ngày hoặc seed khác trước demo.
- Generator chưa sinh kịch bản AMR-03 hỏng 14:05–14:40 như đề, nên test kịch bản đó đang skip.
