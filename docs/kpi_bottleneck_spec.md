# KPI Specification & Bottleneck Detection — Cường (v0.1)

Tài liệu thiết kế cho Decision Engine: định nghĩa cách dùng KPI để so sánh scenario và phát hiện bottleneck.
Nhân implement bottleneck logic trong `twin/metrics.py` dựa trên spec này.

---

## 1. KPI dùng để so sánh scenario

Tất cả KPI đều lấy từ `SimulationResult.metrics` (spec v0.1 mục 5). Bảng dưới bổ sung
**hướng tốt**, **ngưỡng cảnh báo**, và **trọng số mặc định** cho ranking.

| KPI | Hướng tốt | Ngưỡng CAUTION | Ngưỡng WARNING | Trọng số mặc định | Ghi chú |
|---|---|---|---|---|---|
| `throughput_count` | ↑ càng cao càng tốt | — | — | 0.30 | Ưu tiên số 1: giao được hàng cho line |
| `avg_lead_time_min` | ↓ càng thấp càng tốt | > 20 phút | > 30 phút | 0.25 | Thời gian trung bình từ lúc request → delivered |
| `avg_total_wait_min` | ↓ càng thấp càng tốt | > 10 phút | > 15 phút | 0.20 | Tổng thời gian chờ (queue) qua 3 stage |
| `unfinished_count` | ↓ càng thấp càng tốt | > 0 | > 5 | 0.15 | Số request không kịp giao trong horizon |
| `peak_queue` (max 3 stage) | ↓ càng thấp càng tốt | max > 5 | max > 10 | 0.10 | Hàng đợi cao nhất — chỉ dấu ùn tắc cục bộ |

### Cách tính điểm chuẩn hóa (normalized score)

Mỗi KPI được chuẩn hóa về khoảng [0, 1] so với **baseline** và kịch bản **tốt nhất** trong batch:

```
score_i = (value_i - worst_i) / (best_i - worst_i)   nếu best ≠ worst
        = 1.0                                         nếu best == worst (tất cả bằng nhau)
```

- Với KPI hướng ↑ (throughput): `best = max`, `worst = min`
- Với KPI hướng ↓ (lead_time, wait, unfinished, peak_queue): `best = min`, `worst = max`

**Tổng điểm scenario** = Σ (trọng số × score chuẩn hóa). Scenario có tổng cao nhất xếp hạng 1.

> [!NOTE]
> Trọng số mặc định lấy từ file `config/scenarios.yaml`. UI WHAT-IF có thể cho user kéo thanh
> thay đổi trọng số → ranking thay đổi realtime. Khi đó chỉ cần gọi lại `ranking.rank()`,
> không cần chạy lại simulation.

---

## 2. KPI delta — so sánh scenario vs baseline

Cho mỗi scenario, tính **delta** và **delta %** so với baseline:

```python
delta = scenario_value - baseline_value
delta_pct = delta / baseline_value * 100   # baseline_value ≠ 0
```

Hiển thị trong bảng compare (UI WHAT-IF):
- `delta > 0` cho KPI hướng ↑ → xanh (tốt hơn)
- `delta < 0` cho KPI hướng ↑ → đỏ (tệ hơn)
- Ngược lại cho KPI hướng ↓

---

## 3. Bottleneck Detection

### 3.1 Định nghĩa

**Bottleneck** là stage đang giới hạn throughput của toàn hệ thống. Tại bất kỳ thời điểm nào,
chỉ có **một** primary bottleneck (stage bận nhất) và có thể có secondary bottleneck.

### 3.2 Chỉ số phát hiện (3 tiêu chí)

| # | Tiêu chí | Công thức | Ngưỡng |
|---|---|---|---|
| 1 | **Utilization** (chính) | `utilization[stage]` từ SimulationResult | stage có utilization cao nhất VÀ ≥ 0.80 |
| 2 | **Peak queue** (phụ) | `peak_queue[stage]` từ SimulationResult | stage có peak_queue cao nhất VÀ ≥ 3 |
| 3 | **Wait contribution** (phụ) | Tính từ events: trung bình thời gian chờ (queued→start) per stage cho các request đã delivered | stage có wait contribution cao nhất |

### 3.3 Thuật toán xác định bottleneck

```
Bước 1: Tính utilization cho 3 stage (amr, packer, agv)
Bước 2: Nếu có stage nào utilization ≥ 0.80 → ghi nhận là "stressed"
Bước 3: Primary bottleneck = stage "stressed" có utilization CAO NHẤT
         Nếu không stage nào ≥ 0.80 → primary bottleneck = None
         (hệ thống đủ capacity, không nghẽn)
Bước 4: Secondary bottleneck = stage "stressed" có utilization cao thứ 2
         (nếu có, và cách primary ≤ 0.10)
Bước 5: Cross-check với peak_queue và wait contribution:
         Nếu stage khác có peak_queue > primary VÀ > 5 → ghi cảnh báo
         "peak_queue gợi ý bottleneck khác" (có thể do burst ngắn)
```

### 3.4 Output format

```python
@dataclass
class BottleneckResult:
    primary: str | None        # "amr" | "packer" | "agv" | None
    secondary: str | None
    utilizations: dict[str, float]      # {"amr": 0.92, "packer": 0.75, "agv": 0.45}
    peak_queues: dict[str, int]         # {"amr": 8, "packer": 2, "agv": 0}
    wait_per_stage: dict[str, float]    # {"amr": 3.2, "packer": 1.1, "agv": 0.5}
    explanation: str            # "AMR là bottleneck chính (utilization 92%, peak queue 8).
                                #  Thêm AMR sẽ giảm thời gian chờ ~3.2 phút/request."
```

### 3.5 Ngưỡng cấu hình

Tất cả ngưỡng nằm trong `config/scenarios.yaml` → dễ điều chỉnh không cần sửa code:

```yaml
bottleneck:
  utilization_stressed: 0.80    # stage bắt đầu "stressed"
  utilization_critical: 0.95    # stage nguy hiểm
  peak_queue_notable: 3         # peak queue đáng chú ý
  peak_queue_severe: 8          # peak queue nghiêm trọng
  secondary_gap: 0.10           # khoảng cách utilization tối đa để tính secondary
```

---

## 4. Exception Severity (NOW dashboard)

Dựa trên `CurrentLogisticsState` live (không phải KPI mô phỏng). Gợi ý ngưỡng cho Hải Anh:

| Severity | Điều kiện (bất kỳ stage nào) | Màu |
|---|---|---|
| 🟢 NORMAL | Tất cả utilization < 0.70 VÀ tổng queue ≤ 3 | Xanh |
| 🟡 WARNING | Bất kỳ utilization ≥ 0.70 HOẶC tổng queue > 3 | Vàng |
| 🔴 CRITICAL | Bất kỳ utilization ≥ 0.90 HOẶC có resource offline HOẶC tổng queue > 8 | Đỏ |

Utilization tại NOW = `busy / (total - offline)` cho mỗi stage.

> [!IMPORTANT]
> Exception severity dùng **dữ liệu thời gian thực** (`CurrentLogisticsState`), KHÔNG phải
> kết quả mô phỏng. Nó trả lời "bây giờ có ổn không?", trong khi bottleneck detection
> từ Twin trả lời "trong kịch bản này, đâu sẽ là nút thắt?".

---

## 5. Validation — tiêu chí bottleneck-agreement

So sánh bottleneck do Twin dự đoán vs thực tế (từ Generator ground truth):

```
agreement = 1  nếu primary_bottleneck_predicted == primary_bottleneck_actual
             0  nếu khác
```

**Bottleneck-agreement rate** = số ngày match / tổng số ngày test.

- **Mục tiêu**: agreement rate ≥ 80% trên ≥ 5 ngày test (seed khác nhau).
- Nếu < 80%: cần calibrate lại `service_min` trong ScenarioConfig hoặc xem lại Twin logic.

Bottleneck thực tế tính từ `_truth/events`: stage nào có tổng thời gian chờ cao nhất
hoặc utilization cao nhất trong ngày đó.

---

## Changelog

| Ngày | Thay đổi |
|---|---|
| 2026-10-05 | v0.1 — bản thiết kế đầu tiên, đồng bộ với contract v0.1 |
