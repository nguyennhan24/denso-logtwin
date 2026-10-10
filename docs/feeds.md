# 6 feed của World Generator (Trung)

Mỗi ngày sinh ra một thư mục `data/raw/<date>_seed<seed>/` (layout cũ không có seed: `data/raw/<date>/`, xem `docs/data_dictionary.md` mục 1). Các feed cố ý **không dùng chung ID chuẩn**, giống các hệ thống rời rạc trong nhà máy thật. Phase 4 (Data Layer) có nhiệm vụ ghép chúng lại thành `MaterialRequest` và `CurrentLogisticsState`.

| Feed | Nguồn giả lập | Kiểu cập nhật | Định dạng giờ | Khóa lượt giao |
| --- | --- | --- | --- | --- |
| `wms.csv` | Phần mềm kho | batch | ISO 8601 | `pick_no` = `PICK-00012` |
| `amr.csv` | Fleet manager AMR | stream (mỗi sự kiện 1 dòng) | ISO 8601 | `task_id` = `T-0007`; chỉ dòng `DISPATCH` có `wms_ref` |
| `agv.csv` | Hệ thống AGV | mỗi job 1 dòng | epoch giây | `label` = `PK12` |
| `production.csv` | Kế hoạch sản xuất | đầu ngày | ISO 8601 | — |
| `labor.csv` | Bảng phân ca | đầu ngày | `HH:MM` | — |
| `excel.csv` | Log đóng gói nhập tay | trễ 2 giờ, thiếu ~5% dòng | `HH:MM` + `entered_at` ISO | `pick` = `12` |

Ngoài ra:
- `master.csv`: bảng mapping mã (LINE_A / ST-A1 → L1, MAT-01 → M01).
- `_truth/`: đáp án cho test và Backtest. **Data Layer không được đọc.**

## Cột chi tiết

**wms.csv**: `pick_no, created_at, material_code, line_code, qty_packs, need_by`

**amr.csv**: `ts, robot_id, task_id, event, wms_ref`
Các giá trị của `event`:
- `DISPATCH`: nhận task, rời kho.
- `ARRIVE_GATE`: tới cổng, bắt đầu chờ người nhận thùng.
- `RELEASE`: thùng được lấy ra. Thời điểm này cũng là lúc packer bắt đầu.
- `RETURNED`: về tới kho, rảnh.
- `FAULT` / `RECOVER`: hỏng / sửa xong. Hai dòng này không có `task_id`.

**agv.csv**: `job_id, agv_id, label, station_code, load_epoch, deliver_epoch`. Cột `deliver_epoch` để trống nếu đến cuối ngày chưa giao.

**production.csv**: `line_code, hour_start, product, planned_qty`

**labor.csv**: `date, worker_id, slot, shift, shift_start, shift_end, break_start, break_end` (giờ nghỉ theo **kế hoạch**)

**excel.csv**: `worker, pick, start, end, entered_at`

## Chuỗi ghép ID (việc của `mapper.py`)

```
wms.pick_no  "PICK-00012"
   │  amr.wms_ref (dòng DISPATCH)
   ▼
amr.task_id  "T-0007"   ← các sự kiện sau của task chỉ mang task_id
   │  chuẩn hóa số phiếu: "PICK-00012" → 12
   ├──────────────► agv.label  "PK12"
   └──────────────► excel.pick 12
line: wms.line_code / agv.station_code ──master.csv──► L1, L2
material: wms.material_code ──master.csv──► M01..M06
request_id chuẩn = pick_no
```

## Suy ra stage từ feed (gợi ý cho `state_builder.py`, tại thời điểm `as_of`)

| Tình trạng lượt giao tại `as_of` | Stage | Trạng thái |
| --- | --- | --- |
| Đã có trong WMS, chưa `DISPATCH` | amr | queued |
| `DISPATCH` rồi, chưa `ARRIVE_GATE` | amr | active |
| `ARRIVE_GATE` rồi, chưa `RELEASE` | packer | queued (AMR vẫn bận!) |
| `RELEASE` rồi, chưa kết thúc đóng gói | packer | active |
| Đóng gói xong, chưa `load_epoch` | agv | queued |
| `load_epoch` rồi, chưa `deliver_epoch` | agv | active |

Có hai điểm khó cần chú ý:
1. **Thời điểm kết thúc đóng gói** chỉ có trong Excel, và Excel vừa trễ vừa thiếu. Tại `as_of`, nhiều lượt chưa được nhập. Cần một quy tắc ước lượng, ví dụ: coi là còn đang đóng gói nếu chưa quá thời gian trung bình.
2. **Chỉ dùng dòng mà hệ thống đã "biết" tại `as_of`**: `ts ≤ as_of` với stream, `entered_at ≤ as_of` với Excel. Không được nhìn vào tương lai.
