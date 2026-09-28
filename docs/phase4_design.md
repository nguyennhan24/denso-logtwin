# Phase 4 — Unified Data Layer: đề xuất thiết kế

**Người phụ trách:** Trung · **Thời gian:** ngày 8–10 · **Gate:** Dashboard chỉ đọc `CurrentLogisticsState`, không đọc feed thô.

## 1. Mục tiêu

Biến 6 feed "bẩn" (lệch ID, lệch định dạng giờ, Excel trễ và thiếu dòng) thành hai contract sạch, **đúng tại bất kỳ thời điểm `as_of` nào**:

- `CurrentLogisticsState`: ảnh chụp nhà máy tại `as_of`, cho tab NOW và làm điểm xuất phát cho Twin.
- Bảng hành trình lượt giao (journey): mỗi lượt giao một dòng, mọi mốc thời gian đã biết. Đây là nguồn cho `MaterialRequest` và cho calibrate/backtest.

Kèm theo một **báo cáo chất lượng dữ liệu**, vì đây là thứ nhà máy thật đang thiếu.

## 2. Năm nguyên tắc thiết kế

1. **Không nhìn vào tương lai.** Mọi hàm nhận `as_of` và chỉ dùng dòng mà hệ thống *đã biết* tại thời điểm đó: `ts ≤ as_of` với stream, `entered_at ≤ as_of` với Excel, `deliver_epoch` sau `as_of` coi như chưa có.
2. **Không đọc `_truth/` trong `src/data_layer/`.** Truth chỉ được dùng trong `tests/` để đo độ chính xác.
3. **Không vứt dữ liệu lỗi.** Dòng không hợp lệ được đưa vào bảng cách ly (quarantine) kèm lý do, và được đếm vào báo cáo chất lượng.
4. **Không biết thì ước lượng từ dữ liệu, và ghi rõ là ước lượng.** Ví dụ: thời gian đóng gói còn lại được ước lượng từ lịch sử Excel, không lấy từ config của Generator (Generator là "thế giới", Data Layer không được biết bí mật của nó).
5. **Hàm thuần.** Cùng đầu vào thì cùng đầu ra, không có biến toàn cục. Dễ test, dễ tái lập.

## 3. Kiến trúc (medallion gọn nhẹ)

```
data/raw/<ngày>/*.csv                        BRONZE: nguyên gốc, không sửa
      │  ingest.py     load_day() + snapshot(as_of)
      │  schemas.py    kiểm tra 3 tầng → clean + quarantine
      ▼
data/unified/<ngày>/silver/*.parquet         SILVER: sạch, giờ đã chuẩn hóa về t_min
      │  mapper.py     ghép ID → bảng journey
      │  state_builder.py  dựng CurrentLogisticsState
      │  freshness.py  feed_updated_at + độ trễ từng nguồn
      │  quality.py    báo cáo chất lượng dữ liệu
      ▼
data/unified/<ngày>/gold/                    GOLD: theo contract
      journey.parquet, state_<HHMM>.json, dq_<HHMM>.json
      │  validate_contracts.py (đã có)
      ▼
Twin (Nhân) · Dashboard (Hải Anh)
```

Công cụ: **pandas**, **pandera** (kiểm tra schema), **pyarrow** (Parquet). Không cần Hadoop, Spark hay Kafka: toàn bộ 21 ngày dữ liệu chỉ vài chục MB.

## 4. Chi tiết từng module

### `ingest.py`: đọc và "cắt" theo thời điểm
- `load_day(day_dir)` đọc 6 feed và `master.csv`, **bỏ qua `_truth/`**.
- `snapshot(raw, as_of)` trả về đúng phần dữ liệu đã biết tại `as_of`. Đây là cơ chế **event replay**: gọi với `as_of` tăng dần là có luồng giả lập thời gian thực.
- Chuẩn hóa mọi loại giờ (ISO, epoch giây, `HH:MM` + ngày) về một trục chung: `t_min` = số phút tính từ đầu ngày làm việc.

### `schemas.py`: kiểm tra 3 tầng
| Tầng | Ví dụ |
| --- | --- |
| Schema | Đủ cột, đúng kiểu; `qty_packs` nguyên dương; giờ đọc được |
| Ngữ nghĩa | `event` thuộc danh sách cho phép; mã line/vật tư có trong master; `deliver ≥ load` |
| Tham chiếu | Mỗi `label` AGV tìm được phiếu WMS; mỗi `task_id` có dòng DISPATCH |

Đầu ra: `(clean_df, quarantine_df)`, trong đó quarantine có cột `reason`.

### `mapper.py`: ghép ID thành hành trình
Chuỗi ghép: `wms.pick_no` ← `amr.wms_ref` (dòng DISPATCH) → `task_id` → mọi sự kiện của task; `agv.label "PK12"` → 12 → `PICK-00012`; `excel.pick 12` → `PICK-00012`; mã line/vật tư → ID chuẩn qua `master.csv`.

Bảng `journey` (mỗi `request_id` một dòng): `request_id, line_id, material_id, quantity, need_by_min, created_min, amr_dispatch_min, amr_arrive_gate_min, packer_start_min, packer_finish_min, agv_load_min, delivered_min`, cùng các ID phụ (`robot_id, task_id, agv_id, job_id, worker`). Mốc nào chưa biết tại `as_of` thì để trống.

### `state_builder.py`: phần khó nhất
**Suy ra stage của mỗi lượt giao tại `as_of`:**

| Mốc cuối cùng đã biết | Stage | Trạng thái |
| --- | --- | --- |
| Chỉ có `created` | amr | queued |
| `amr_dispatch`, chưa `arrive_gate` | amr | active |
| `arrive_gate`, chưa `packer_start` | packer | queued |
| `packer_start`, chưa có kết thúc đóng gói | packer | active *(xem ước lượng bên dưới)* |
| Đã đóng gói xong, chưa `agv_load` | agv | queued |
| `agv_load`, chưa `delivered` | agv | active |
| `delivered` | — | đã xong, không đưa vào state |

**Ước lượng khi Excel chưa nhập** (thời điểm kết thúc đóng gói chỉ có trong Excel, mà Excel trễ tới 2 giờ):
- Ước lượng thời gian đóng gói kỳ vọng = `setup + phút_mỗi_pack × quantity`, **học từ các dòng Excel đã nhập** (lấy trung vị). Nếu chưa đủ dữ liệu thì dùng giá trị mặc định trong `config/data_layer.yaml`.
- Nếu đã quá thời gian kỳ vọng mà vẫn chưa lên AGV: nếu tại `as_of` **có AGV đang rảnh**, lượt này nhiều khả năng *vẫn đang đóng gói* (vì nếu đã xong thì AGV rảnh đã nhận rồi), nên vẫn tính là packer active. Nếu không có AGV rảnh thì tính là agv queued.
- `remaining_min` = max(kỳ vọng − đã trôi qua, 0,5 phút). Contract yêu cầu > 0.
- AMR và AGV cũng ước lượng `remaining_min` bằng trung vị thời gian di chuyển đã quan sát.

**Tài nguyên:**
- `total` lấy từ `config/data_layer.yaml`, tức sổ đăng ký thiết bị của nhà máy.
- `offline` AMR = robot có `FAULT` mà chưa `RECOVER` tại `as_of`.
- `busy` = số active job ở stage đó, **đúng theo quy tắc của contract**.

**Giới hạn đã biết của contract v0.1 (quan trọng, ghi vào báo cáo):** trong thực tế AMR còn "bận" khi *đang chờ ở cổng* hoặc *đang chạy về kho*, nhưng contract v0.1 không có chỗ biểu diễn hai trạng thái này. Data Layer **không sửa contract**; thay vào đó ghi các số liệu này vào file chẩn đoán riêng (`amr_held_at_gate`, `amr_returning`) và đề xuất đưa vào contract v0.2. Đây chính là bằng chứng dữ liệu cho hiện tượng blocking.

**Thứ tự hàng đợi:** theo thời điểm vào stage, trùng thì theo `request_id`. Mọi WIP có `arrival_min = 0` theo spec.

### `freshness.py`
Với mỗi feed: timestamp mới nhất đã biết tại `as_of`, độ trễ tính bằng phút, và loại nguồn (`stream` với amr/agv, `event` với wms, `manual` với excel, `plan` với production/labor). Xuất đúng 6 key của `feed_updated_at`.

### `quality.py`: báo cáo chất lượng dữ liệu
Số dòng đọc vào và số dòng bị cách ly theo từng feed; tỷ lệ ghép thành công (AMR→WMS, AGV→WMS, Excel→WMS); độ phủ Excel; số lượt đang phải dùng ước lượng; độ trễ từng feed. Dashboard hiển thị báo cáo này, là điểm cộng lớn khi pitch.

### `pipeline.py` + CLI
`python -m src.data_layer --date 2026-10-01 --as-of 10:00 [--as-of 14:30 ...]` chạy cả chuỗi và ghi Silver/Gold.

## 5. Cách chứng minh Data Layer đúng (dùng truth, chỉ trong test)

| Test | Ý nghĩa |
| --- | --- |
| Ghép ID: AMR, AGV ≥ 99%; Excel khớp đúng mọi dòng có mặt | Mapper đúng |
| State tại 08:00, 10:00, 14:00, 18:00 so với truth: AMR queued/active **khớp chính xác**; packer/AGV trong sai số cho phép | State builder đúng, và đo được sai số do Excel trễ |
| **Không nhìn tương lai:** state dựng từ feed đầy đủ **giống hệt** state dựng từ feed bị cắt bỏ mọi dòng sau `as_of` | Chứng minh không leakage |
| Output qua `validate_contracts.check_bundle` | Đúng contract |
| Chạy hai lần cho cùng kết quả | Tái lập |

### Kết quả đo được (ngày 10, `tests/test_data_layer.py` + quét 10 seed)

**Ghép ID (seed 42, cuối ngày):** AMR→WMS 430/430 (100%), AGV→WMS 428/428 (100%), Excel→WMS 406/406 (100%). Mọi mốc `amr_dispatch, arrive_gate, packer_start, agv_load, delivered` lệch truth < 1 giây (do feed cắt xuống giây); `packer_finish` từ Excel khớp truth ở mức phút. Độ phủ Excel (lượt đã lên AGV có log) ≈ 95-96%.

**State so với truth:** quét seed 40–49, mỗi 5 phút từ 06:05 tới 21:55 (1.910 snapshot), 0 lỗi contract.

| Ô của state | Khớp chính xác tập request_id | Sai số số lượng trung bình |
| --- | --- | --- |
| amr queued / amr active | 100% / 100% | 0 |
| packer queued | 100% | 0 |
| packer active (phải ước lượng) | 98,5% | 0,013 lượt |
| agv queued (phải ước lượng) | 98,5% | 0,013 lượt |
| agv active | 100% | 0 |

Seed 42 tại 08:00, 10:00, 14:00, 18:00: khớp chính xác cả 6 ô. Test assert: AMR, packer queued, agv active khớp chính xác; packer active / agv queued lệch ≤ 1 lượt.

**Không nhìn tương lai:** state, diagnostics và journey dựng từ feed đầy đủ **giống hệt** khi dựng từ CSV đã bị xóa mọi dòng sau `as_of` (07:30, 10:00, 14:10, 18:00). **Tái lập:** chạy pipeline 2 lần cho file silver/gold giống nhau từng byte.

Các con số sai số này nên đưa lên trang Validation, dưới dạng: *"Tầng dữ liệu tái dựng đúng X% trạng thái nhà máy dù Excel trễ 2 giờ và thiếu 5% dòng."* Với số đo trên: *"đúng 100% hàng đợi AMR/packer và AGV đang chạy, 98,5% trạng thái đóng gói, dù Excel trễ 2 giờ và thiếu 5% dòng."*

## 6. Lịch 3 ngày

| Ngày | Việc | Xong khi |
| --- | --- | --- |
| 8 | `ingest.py`, `schemas.py`, `config/data_layer.yaml` | Đọc được 6 feed, có quarantine, test no-look-ahead cho snapshot |
| 9 | `mapper.py`, `freshness.py` | Bảng journey + tỷ lệ ghép ID đo bằng truth |
| 10 | `state_builder.py`, `quality.py`, `pipeline.py` | State qua validator, test so truth, Hải Anh đọc được file Gold → **gate Phase 4** |

Rủi ro: `state_builder` là phần dễ trễ nhất. Nếu cuối ngày 9 chưa xong mapper, báo ngay ở Sync 2 và giảm phạm vi: bỏ ước lượng thông minh, dùng quy tắc đơn giản "chưa có Excel thì coi là đang đóng gói".
