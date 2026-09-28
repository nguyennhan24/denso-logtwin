# Data Layer (Phase 4): hướng dẫn sử dụng và tự kiểm tra

Tài liệu này dành cho người **dùng** và **kiểm tra** `src/data_layer/`. Lý do thiết kế và số đo độ chính xác nằm ở `docs/phase4_design.md`; schema 6 feed ở `docs/feeds.md`.

Mọi lệnh bên dưới viết cho **Windows PowerShell**, chạy ở **thư mục gốc repo**, và không dùng `&&`.

---

## 1. Data Layer làm gì, trong một phút

Nhà máy có 6 hệ thống rời rạc (WMS, AMR, AGV, kế hoạch sản xuất, phân ca, Excel nhập tay). Mỗi hệ thống dùng ID riêng, ghi giờ theo kiểu riêng, và Excel còn nhập trễ 2 giờ, thiếu khoảng 5% dòng. Data Layer biến 6 feed đó thành:

- **`CurrentLogisticsState`** (contract v0.1): ảnh chụp nhà máy tại một thời điểm `as_of`, gồm ai đang chờ, ai đang được phục vụ, còn bao lâu. Tab NOW của Dashboard và Twin của Nhân dùng file này.
- **Journey**: mỗi lượt giao một dòng, chứa mọi mốc thời gian đã biết.
- **Báo cáo chất lượng dữ liệu**: bao nhiêu dòng lỗi, ghép ID được bao nhiêu, dữ liệu trễ bao lâu.

```
data/raw/<ngày>/*.csv
   │ ingest.py      load_day() đọc 6 feed, đổi mọi giờ về t_min; snapshot(as_of) cắt phần "chưa biết"
   │ schemas.py     validate_feeds(): kiểm tra 3 tầng → clean + quarantine
   │ mapper.py      build_journey(): ghép ID → 1 dòng / lượt giao
   │ state_builder  build_state(): suy stage, ước lượng → CurrentLogisticsState
   │ freshness.py   feed_updated_at(), freshness_report()
   │ quality.py     dq_report()
   │ pipeline.py    run(): chạy cả chuỗi, kiểm contract, ghi file
   ▼
data/unified/<ngày>/silver/*.parquet   data/unified/<ngày>/gold/{state,dq,diag}_<HHMM>.json, journey.parquet
```

**Ba quy tắc bất di bất dịch** (có test canh giữ):

1. `src/data_layer/` **không bao giờ đọc** `data/raw/*/_truth/`.
2. **Không nhìn tương lai.** Kết quả tại `as_of` chỉ dùng dữ liệu đã biết tại `as_of`.
3. **Không vứt dữ liệu lỗi.** Dòng lỗi được đưa vào `quarantine` kèm lý do.

---

## 2. Từng module: nhận gì, trả gì

| File | Hàm chính | Nhận | Trả |
| --- | --- | --- | --- |
| `ingest.py` | `load_day(day_dir, cfg)` | thư mục `data/raw/<ngày>` | `RawFeeds`: 6 DataFrame + `master`, có thêm cột `*_min` |
| | `snapshot(raw, as_of_min)` | `RawFeeds`, phút | `RawFeeds` chỉ còn dòng đã biết tại `as_of` |
| | `parse_as_of("10:00", date, cfg)` | `"HH:MM"` hoặc ISO | số phút tính từ 06:00 (ví dụ `240.0`) |
| `schemas.py` | `validate_feeds(raw)` | `RawFeeds` | `(clean, quarantine)`; quarantine có cột `feed, row_index, reason` |
| `ids.py` | `pick_no_from_label`, `pick_no_from_num`, `master_map` | mã thô | ID chuẩn (`PK12` → `PICK-00012`, `LINE_A` → `L1`) |
| `mapper.py` | `build_journey(clean, as_of_min)` | feed sạch | DataFrame journey (cột ở mục 4) |
| | `match_stats(clean, journey)` | | tỷ lệ ghép AMR/AGV/Excel → WMS và danh sách không ghép được |
| `state_builder.py` | `build_state(clean, journey, as_of_min, cfg)` | | dict `CurrentLogisticsState` |
| | `build_diagnostics(...)` | | dict chẩn đoán ngoài contract |
| `freshness.py` | `feed_updated_at`, `freshness_report` | | 6 timestamp; và thêm độ trễ + loại nguồn |
| `quality.py` | `dq_report(snap, clean, quarantine, journey, as_of_min, cfg)` | | dict báo cáo chất lượng |
| `pipeline.py` | `run(date, as_of_list, raw_dir, out_dir)` | | ghi file, trả tóm tắt |

**Thời gian nội bộ:** `t_min` là số phút (float) tính từ `day_start` (06:00). Ví dụ `t_min = 240` là 10:00, `t_min = 128.4167` là 08:08:25.

**Config:** `config/data_layer.yaml`. Đây là tham số của Data Layer, **không phải** của Generator:

| Khóa | Ý nghĩa |
| --- | --- |
| `day_start`, `tz_offset_hours` | mốc `t_min = 0` và múi giờ +07:00 |
| `origin_id` | kho duy nhất, `STORE-1` |
| `resources_total` | sổ đăng ký thiết bị: 6 AMR, 3 packer, 4 AGV |
| `fallback_estimates` | thời gian mặc định (phút) khi chưa đủ dữ liệu để tự học |
| `min_remaining_min` | `remaining_min` nhỏ nhất (contract yêu cầu > 0) |
| `estimate_min_samples` | số mẫu tối thiểu để tin ước lượng học từ dữ liệu |

---

## 3. Suy stage của một lượt giao (tóm tắt)

| Mốc **cuối cùng** đã biết tại `as_of` | Stage / trạng thái | Nguồn |
| --- | --- | --- |
| chỉ có phiếu WMS | amr / queued | wms |
| DISPATCH | amr / active | amr |
| ARRIVE_GATE | packer / queued | amr |
| RELEASE, chưa có Excel, chưa lên AGV | packer / active **hoặc** agv / queued (**ước lượng**) | amr + ước lượng |
| có `end` trong Excel | agv / queued | excel |
| load AGV | agv / active | agv |
| deliver AGV | xong, không đưa vào state | agv |

Cách ước lượng khi Excel chưa nhập:

- Thời gian đóng gói kỳ vọng bằng `setup + per_unit × quantity`, học bằng trung vị từ các dòng Excel đã nhập.
- Chưa quá kỳ vọng: tính là packer active.
- Đã quá kỳ vọng mà vẫn còn AGV rảnh: vẫn là packer active (nếu xong rồi thì AGV rảnh đã nhận).
- Đã quá kỳ vọng và không còn AGV rảnh: tính là agv queued.

Chi tiết xem `docs/phase4_design.md`.

---

## 4. File đầu ra

Sau `python -m src.data_layer --date 2026-10-01 --as-of 10:00 --as-of 14:30`:

```
data/unified/2026-10-01/
├── silver/                  feed sạch tại as_of MUỘN NHẤT (14:30)
│   ├── wms.parquet  amr.parquet  agv.parquet  production.parquet  labor.parquet  excel.parquet
│   ├── master.parquet
│   └── quarantine.parquet   dòng bị cách ly: feed, row_index, reason
└── gold/
    ├── journey.parquet      hành trình tại 14:30
    ├── state_1000.json      CurrentLogisticsState lúc 10:00  ← Dashboard/Twin đọc file này
    ├── state_1430.json
    ├── dq_1000.json         báo cáo chất lượng dữ liệu
    ├── dq_1430.json
    ├── diag_1000.json       chẩn đoán ngoài contract (AMR bị giữ ở cổng, AMR đang về...)
    └── diag_1430.json
```

- **`state_*.json`:** đúng contract v0.1, đã qua `check_bundle` trước khi ghi. Nếu sai contract thì pipeline dừng và **không ghi file nào**.
- **`dq_*.json`:** mục `metrics` là danh sách phẳng `{key, label, value, unit}`, trong đó `label` là tiếng Việt để Dashboard hiển thị thẳng.
- **`diag_*.json`:** gồm `amr_held_at_gate`, `amr_returning`, `amr_offline_robots`, `agv_status_est`, `packer_on_planned_break`, `estimated_jobs`, `estimates` (các thời gian đã học và nguồn của chúng).
- **Cột journey:** `request_id, line_id, material_id, quantity, need_by_min, created_min, amr_dispatch_min, amr_arrive_gate_min, packer_start_min, packer_finish_min, agv_load_min, delivered_min, robot_id, task_id, agv_id, job_id, worker, amr_returned_min`. Mốc nào chưa biết thì để trống (NaN).
- **`row_index` trong quarantine:** số thứ tự dòng dữ liệu, bắt đầu từ 0. Khi mở CSV bằng Notepad (dòng 1 là header), `row_index = 2` nằm ở **dòng 4**.

---

## 5. Tự kiểm tra từng bước

### Bước 0: Cài đặt và sinh dữ liệu (một lần)

```powershell
pip install -r requirements.txt
python -m src.generator --seed 42 --date 2026-10-01
```

Kiểm tra thư mục `data\raw\2026-10-01\` có 7 file CSV và thư mục `_truth\`.

### Bước 1: Chạy test tự động

```powershell
pytest -q
```

Kết quả mong đợi: tất cả đều pass (hiện có 44 test). Muốn chạy riêng một nhóm và xem chữ in ra thì thêm `-s`:

```powershell
pytest -q tests/test_data_layer.py -k "snapshot"
pytest -q tests/test_data_layer.py -k "quarantine or dirty or orphan or tier1"
pytest -q -s tests/test_data_layer.py -k "match_rates"
pytest -q -s tests/test_data_layer.py -k "error_table"
pytest -q tests/test_data_layer.py -k "look_ahead"
pytest -q tests/test_data_layer.py -k "pipeline"
```

Mỗi test chứng minh điều gì:

| Test | Chứng minh |
| --- | --- |
| `test_load_day_reads_six_feeds_without_truth` | đọc đủ 6 feed; chặn mọi lần đọc `_truth` mà vẫn chạy được |
| `test_t_min_*`, `test_parse_as_of` | quy đổi giờ đúng, không âm, đúng thứ tự (deliver ≥ load...) |
| `test_snapshot_*` | cắt theo `as_of` đúng, Excel cắt theo lúc **nhập** |
| `test_clean_seed_has_empty_quarantine` | dữ liệu sạch thì không có dòng nào bị cách ly |
| `test_dirty_rows_are_quarantined_with_reason` | làm bẩn 3 dòng: đúng 3 dòng bị cách ly, đúng lý do, các dòng khác giữ nguyên |
| `test_journey_matches_truth` | mọi mốc khớp đáp án (< 1 giây); Excel khớp ở mức phút; không tự điền dòng Excel thiếu |
| `test_match_rates` | tỷ lệ ghép AMR/AGV ≥ 99% (seed 42 đạt 100%) |
| `test_state_passes_contract` | state tại 08:00/10:00/14:00/18:00 qua validator |
| `test_state_amr_matches_truth_exactly` | hàng đợi AMR/packer và AGV đang chạy khớp đáp án 100% |
| `test_state_packer_agv_error_table` | phần phải ước lượng lệch ≤ 1 lượt; in bảng sai số |
| `test_no_look_ahead` | state từ feed đầy đủ **giống hệt** state từ CSV đã xóa mọi dòng sau `as_of` |
| `test_pipeline_end_to_end` | ghi đủ file, hợp lệ; chạy 2 lần cho kết quả giống từng byte |
| `test_pipeline_stops_on_contract_error` | state sai thì dừng, không ghi gì |
| `test_docs_*` | các script trong `docs/examples/` vẫn chạy được |

### Bước 2: Chạy pipeline và đọc kết quả

```powershell
python -m src.data_layer --date 2026-10-01 --as-of 10:00 --as-of 14:30
python docs/examples/check_state_file.py data/unified/2026-10-01/gold/state_1000.json
```

Script thứ hai kiểm tra contract và in state dễ đọc, ví dụ:

```
amr    total=6 busy=2 offline=0  chờ=8 ['PICK-00105', ...]
       đang làm PICK-00103, còn ~0.87 phút
packer total=3 busy=3 offline=0  chờ=3 ['PICK-00100', 'PICK-00101', 'PICK-00102']
...
HỢP LỆ theo contract v0.1
```

> ⚠️ Đừng dùng `python contracts/validate_contracts.py <file state>`. Lệnh đó chờ file có key `current_state`, nên gặp file state trần sẽ in "HỢP LỆ" mà không kiểm tra gì.

Xem báo cáo chất lượng và chẩn đoán:

```powershell
Get-Content data/unified/2026-10-01/gold/dq_1000.json -Encoding UTF8
Get-Content data/unified/2026-10-01/gold/diag_1000.json -Encoding UTF8
```

Tự kiểm tra các điểm sau:

- [ ] `busy` của mỗi stage bằng số dòng "đang làm" của stage đó.
- [ ] Mọi `feed_updated_at` ≤ `as_of`; `excel` là `10:00` (đợt nhập Excel lúc 10:00); `production`/`labor` là `06:00`.
- [ ] `diag.amr_held_at_gate.count` bằng số thùng đang chờ ở packer. Đây chính là hiện tượng AMR bị giữ ở cổng.
- [ ] `dq.metrics`: `match_*` = 1.0, `rows_quarantined` = 0 với dữ liệu sạch.

### Bước 3 (bài tập 1): Lần theo một lượt giao bằng tay

```powershell
python docs/examples/trace_request.py --request PICK-00050
```

Script in ra lần lượt: dòng WMS, các sự kiện AMR của task, job AGV (kèm epoch đã đổi sang giờ +07:00), dòng Excel, rồi **dòng journey** Data Layer ghép được (mỗi mốc có kèm giờ đồng hồ). Hãy so từng mốc:

| Mốc journey | Phải bằng |
| --- | --- |
| `created_min` | `wms.created_at` |
| `amr_dispatch_min`, `amr_arrive_gate_min` | `ts` của DISPATCH, ARRIVE_GATE |
| `packer_start_min` | `ts` của **RELEASE** |
| `packer_finish_min` | `excel.end` (tròn phút) |
| `agv_load_min`, `delivered_min` | `load_epoch`, `deliver_epoch` |
| `line_id` / `material_id` | `LINE_B` → `L2`, `MAT-06` → `M06` (theo `master.csv`) |

Sau đó xem cùng lượt đó **ở một thời điểm sớm hơn**:

```powershell
python docs/examples/trace_request.py --request PICK-00050 --as-of 08:15
```

Lúc 08:15, AMR mới tới cổng (08:13:32), nên `packer_start_min` trở về trạng thái **trống**. `packer_finish_min` cũng trống, vì dòng Excel mãi 10:00 mới được nhập. Đó là "không nhìn tương lai". Thử thêm vài `PICK-xxxxx` khác, nhất là lượt có dòng Excel bị thiếu (script sẽ in "Excel thiếu").

### Bước 4 (bài tập 2): Làm bẩn dữ liệu, xem quarantine

1. Chép một bản riêng để không hỏng dữ liệu gốc:

   ```powershell
   New-Item -ItemType Directory -Force data/raw_dirty
   Copy-Item -Recurse -Force data/raw/2026-10-01 data/raw_dirty/
   ```

2. Mở bằng **Notepad hoặc VS Code** (đừng dùng Excel, vì Excel tự đổi định dạng giờ và số) và sửa 3 chỗ:
   - `data/raw_dirty/2026-10-01/wms.csv`, **dòng 4**: đổi `LINE_A`/`LINE_B` thành `LINE_Z`.
   - `amr.csv`: đổi chữ `RETURNED` **đầu tiên** thành `FLY`.
   - `agv.csv`, **dòng 6**: đổi nhãn `PKxx` thành `PK999999`.

3. Chạy và xem kết quả:

   ```powershell
   python -m src.data_layer --date 2026-10-01 --as-of 10:00 --raw-dir data/raw_dirty --out-dir data/unified_dirty
   python -c "import pandas as pd; print(pd.read_parquet('data/unified_dirty/2026-10-01/silver/quarantine.parquet'))"
   ```

Kết quả mong đợi (row_index = số dòng trong Notepad − 2):

```
  feed  row_index                                        reason
0  agv          4        tầng 3: label không tìm được phiếu WMS
1  amr         11  tầng 2: event không thuộc danh sách cho phép
2  wms          2       tầng 2: line_code không có trong master
```

Trong `data/unified_dirty/2026-10-01/gold/dq_1000.json`:

- `rows_quarantined = 3`.
- `match_amr`, `match_agv`, `match_excel` giảm xuống dưới 1. Phiếu WMS bị cách ly nên task AMR và dòng Excel của nó không ghép được.
- CLI báo `queued=12` thay vì `11`. Job AGV bị cách ly nên lượt đó trông như "đóng gói xong, chờ AGV". Đây là ví dụ cho thấy dữ liệu bẩn làm lệch state, và vì sao `dq` phải hiển thị cạnh tab NOW.

Thử thêm: xóa trống ô `qty_packs` (lỗi tầng 1), hoặc ghi `end` là `25:99` trong `excel.csv`. Cuối bài, xóa thư mục thử:

```powershell
Remove-Item -Recurse -Force data/raw_dirty, data/unified_dirty
```

### Bước 5 (bài tập 3): So với đáp án

```powershell
python docs/examples/compare_truth.py
python docs/examples/compare_truth.py --as-of 09:30 --as-of 12:10 --as-of 20:45
```

Script dựng state rồi so **từng tập request_id** của 6 ô (amr/packer/agv × chờ/đang làm) với `_truth/requests.csv`. Mong đợi: hầu hết là `KHỚP`. Theo số đo trên 1.910 thời điểm, khoảng 1,5% thời điểm có lệch 1 lượt ở `packer active`/`agv queued`. Đó là giới hạn của việc ước lượng khi Excel chưa nhập. Muốn dò một ca lệch, lấy `request_id` được in ra rồi chạy `trace_request.py` với `--as-of` đó.

### Bước 6 (bài tập 4): Tái lập

```powershell
python -m src.data_layer --date 2026-10-01 --as-of 10:00 --out-dir data/u1
python -m src.data_layer --date 2026-10-01 --as-of 10:00 --out-dir data/u2
Compare-Object (Get-Content data/u1/2026-10-01/gold/state_1000.json) (Get-Content data/u2/2026-10-01/gold/state_1000.json)
```

Không in gì nghĩa là hai file giống hệt nhau. Xóa thư mục thử bằng `Remove-Item -Recurse -Force data/u1, data/u2`.

### Checklist trước khi giao file cho Nhân / Hải Anh

- [ ] `pytest -q` pass hết.
- [ ] `check_state_file.py` báo HỢP LỆ cho mọi `state_*.json`.
- [ ] `compare_truth.py` khớp tại các mốc sẽ demo.
- [ ] `dq_*.json`: `rows_quarantined` và `match_*` hợp lý; nếu có dòng bị cách ly thì đọc `quarantine_reasons`.

---

## 6. Dùng từ code khác

```python
import json
state = json.load(open("data/unified/2026-10-01/gold/state_1000.json", encoding="utf-8"))
state["resources"]["packer"]          # {"total": 3, "busy": 3, "offline": 0}
[q for q in state["queued_jobs"] if q["stage"] == "amr"]   # giữ nguyên thứ tự mảng = thứ tự FIFO

# Hoặc chạy trực tiếp trong Python, không ghi file:
from src.data_layer.ingest import load_config, load_day, parse_as_of
from src.data_layer.pipeline import run_as_of
cfg = load_config()
raw = load_day("data/raw/2026-10-01", cfg)
r = run_as_of(raw, parse_as_of("10:00", "2026-10-01", cfg), cfg)
r["state"], r["dq"], r["diag"], r["journey"], r["quarantine"]
```

Lưu ý cho Twin: **không sắp xếp lại** `queued_jobs`, vì thứ tự trong mảng là thứ tự FIFO (spec mục 3). Mọi WIP có `arrival_min = 0`.

---

## 7. Lỗi thường gặp

| Hiện tượng | Nguyên nhân và cách xử lý |
| --- | --- |
| `Không thấy data\raw\<ngày>` | Chưa sinh dữ liệu: `python -m src.generator --date <ngày>` |
| `ModuleNotFoundError: pandera / pyarrow` | `pip install -r requirements.txt` |
| `ModuleNotFoundError: src` | Chạy lệnh ở **gốc repo**, dùng `python -m src.data_layer`, không `cd` vào `src` |
| `ContractError: ...busy...` | State sai contract; pipeline đã dừng và không ghi file. Đọc từng dòng lỗi, dò bằng `trace_request.py` |
| `ValueError: Feed 'x' sai cấu trúc` | CSV thiếu hẳn một cột, hoặc sai tên header; không cách ly từng dòng được |
| Tiếng Việt bị lỗi font khi `Get-Content` | Thêm `-Encoding UTF8`, hoặc chạy `chcp 65001` trước |
| State rỗng hoặc giờ lệch cả ngày | Thư mục phải đặt tên đúng ngày của dữ liệu (`2026-10-01`), vì `t_min` tính từ 06:00 của ngày lấy từ tên thư mục |
