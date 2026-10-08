# Scenario & Ranking Design — Cường (v0.1)

Tài liệu thiết kế cho `src/scenario/scenarios.py` và `src/scenario/ranking.py`.
Đọc cùng `docs/kpi_bottleneck_spec.md`.

---

## 1. ScenarioConfig — recap từ contract v0.1

```python
ScenarioConfig = {
    "scenario_id":       str,    # tên duy nhất, ví dụ "BASELINE", "+2AMR"
    "horizon_min":       float,  # > 0, cửa sổ mô phỏng
    "sample_every_min":  float,  # > 0, chia hết horizon_min
    "random_seed":       int,    # seed để tái lập
    "capacity":          {"amr": int, "packer": int, "agv": int},
    "service_min":       {"amr": float, "packer": float, "agv": float},
}
```

**Quy tắc bất biến:**
- `capacity[stage] ≥ busy[stage]` tại snapshot (không được giảm dưới số đang bận)
- Baseline: `capacity = total - offline` tại snapshot
- WHAT-IF: sao chép baseline config → chỉnh capacity/service_min → **không sửa config gốc**
- `service_min` là thời gian phục vụ cố định trong v0.1 (không phụ thuộc quantity)

---

## 2. Danh sách scenario mặc định

Lấy từ `config/scenarios.yaml`. Thiết kế theo nguyên tắc:
- **Tầng 1**: Thay đổi đơn lẻ → hiểu impact của từng resource
- **Tầng 2**: Kết hợp → tìm phương án tối ưu tổng thể
- **Tầng 3** (tùy chọn): Service time improvement → đầu tư cải tiến quy trình

### Bảng scenario

| scenario_id | Ý nghĩa | capacity delta | service_min delta |
|---|---|---|---|
| `BASELINE` | Hiện trạng | +0 / +0 / +0 | không đổi |
| `+1AMR` | Thêm 1 AMR | **+1** / +0 / +0 | không đổi |
| `+2AMR` | Thêm 2 AMR | **+2** / +0 / +0 | không đổi |
| `+1WORKER` | Thêm 1 packer | +0 / **+1** / +0 | không đổi |
| `+2WORKER` | Thêm 2 packer | +0 / **+2** / +0 | không đổi |
| `+1AGV` | Thêm 1 AGV | +0 / +0 / **+1** | không đổi |
| `+2AGV` | Thêm 2 AGV | +0 / +0 / **+2** | không đổi |
| `+2AMR+1WORKER` | Combo: AMR + packer | **+2** / **+1** / +0 | không đổi |
| `+1AMR+1AGV` | Combo: AMR + AGV | **+1** / +0 / **+1** | không đổi |
| `+1ALL` | Thêm mỗi loại 1 | **+1** / **+1** / **+1** | không đổi |
| `FAST_PACK` | Cải tiến đóng gói | +0 / +0 / +0 | packer × 0.75 |

> [!NOTE]
> Scenario tự động xây từ baseline capacity tại runtime, **không hardcode capacity tuyệt đối**.
> Ví dụ: baseline AMR = 5 (6 total - 1 offline), thì `+2AMR` có capacity.amr = 7.

---

## 3. Cách sinh ScenarioConfig — `scenarios.py`

### 3.1 API

```python
def build_baseline(
    state: CurrentLogisticsState,
    horizon_min: float = 60.0,
    sample_every_min: float = 5.0,
    seed: int = 42,
    service_min: dict | None = None,
) -> ScenarioConfig:
    """Tạo baseline config từ CurrentLogisticsState.

    capacity = total - offline cho mỗi stage.
    service_min mặc định đọc từ config/scenarios.yaml nếu không truyền.
    """

def build_whatif_scenarios(
    baseline: ScenarioConfig,
    scenario_defs: list[dict],
) -> list[ScenarioConfig]:
    """Tạo list ScenarioConfig WHAT-IF từ baseline + danh sách scenario definition.

    Mỗi scenario_def chứa:
      - scenario_id: str
      - capacity_delta: {amr: +N, packer: +N, agv: +N}  (mặc định +0)
      - service_min_factor: {amr: x, packer: x, agv: x}  (mặc định 1.0, nhân vào baseline)

    Trả về list config mới, KHÔNG sửa baseline.
    """
```

### 3.2 Logic chi tiết

```python
def build_whatif_scenarios(baseline, scenario_defs):
    results = []
    for sd in scenario_defs:
        cfg = copy.deepcopy(baseline)  # KHÔNG sửa baseline
        cfg["scenario_id"] = sd["scenario_id"]

        for stage in ("amr", "packer", "agv"):
            # Tăng capacity
            delta = sd.get("capacity_delta", {}).get(stage, 0)
            cfg["capacity"][stage] += delta

            # Nhân service_min
            factor = sd.get("service_min_factor", {}).get(stage, 1.0)
            cfg["service_min"][stage] *= factor
            cfg["service_min"][stage] = round(cfg["service_min"][stage], 2)

        results.append(cfg)
    return results
```

### 3.3 Validation trước khi chạy Twin

```python
def validate_scenario(cfg: ScenarioConfig, state: CurrentLogisticsState) -> list[str]:
    """Kiểm tra scenario hợp lệ trước khi đưa vào Twin.run().

    Lỗi nếu:
    - capacity[stage] < busy[stage] tại snapshot
    - service_min[stage] <= 0
    - horizon_min <= 0
    - sample_every_min không chia hết horizon_min
    """
```

---

## 4. Ranking — `ranking.py`

### 4.1 API

```python
@dataclass
class RankedScenario:
    rank: int
    scenario_id: str
    total_score: float                  # 0.0 → 1.0
    kpi_scores: dict[str, float]        # score chuẩn hóa từng KPI
    kpi_deltas: dict[str, float]        # delta vs baseline
    kpi_delta_pcts: dict[str, float]    # delta % vs baseline
    bottleneck: BottleneckResult        # từ kpi_bottleneck_spec
    explanation: str                    # giải thích tự nhiên bằng tiếng Việt


def rank(
    results: dict[str, SimulationResult],  # key = scenario_id
    weights: dict[str, float] | None = None,
) -> list[RankedScenario]:
    """Xếp hạng scenario dựa trên weighted normalized score.

    results phải chứa key "BASELINE".
    weights mặc định lấy từ config. Tổng weights không cần = 1, sẽ tự chuẩn hóa.
    Trả về list sắp theo rank (1 = tốt nhất).
    """
```

### 4.2 Thuật toán ranking

```
Bước 1: Thu thập KPI values từ tất cả scenario
         kpi_values[scenario_id][kpi_name] = value

Bước 2: Với mỗi KPI, tính best và worst trong batch:
         - throughput_count:   best = max, worst = min
         - avg_lead_time_min:  best = min, worst = max
         - avg_total_wait_min: best = min, worst = max
         - unfinished_count:   best = min, worst = max
         - peak_queue_max:     best = min(max across stages), worst = max(max across stages)

Bước 3: Chuẩn hóa mỗi KPI về [0, 1]:
         score = (value - worst) / (best - worst) cho hướng ↑
         score = (worst - value) / (worst - best) cho hướng ↓
         (nếu best == worst → score = 1.0)

Bước 4: Tính total_score = Σ (weight_i × score_i) / Σ weight_i

Bước 5: Sắp xếp theo total_score giảm dần → gán rank

Bước 6: Với mỗi scenario, chạy bottleneck detection (xem kpi_bottleneck_spec)

Bước 7: Sinh explanation text
```

### 4.3 Explanation generation

Template giải thích cho từng scenario (tiếng Việt):

```python
def generate_explanation(ranked: RankedScenario, baseline_result: SimulationResult) -> str:
    """Sinh giải thích tự nhiên cho 1 scenario."""
    parts = []

    # 1. Tóm tắt thay đổi capacity
    parts.append(f"Kịch bản {ranked.scenario_id}:")

    # 2. So sánh throughput
    tp_delta = ranked.kpi_deltas["throughput_count"]
    if tp_delta > 0:
        parts.append(f"  • Giao thêm được {tp_delta} request (+{ranked.kpi_delta_pcts['throughput_count']:.0f}%)")
    elif tp_delta == 0:
        parts.append(f"  • Throughput không đổi — thêm tài nguyên ở stage này không tăng năng suất")

    # 3. So sánh lead time
    lt_delta = ranked.kpi_deltas["avg_lead_time_min"]
    if lt_delta < 0:
        parts.append(f"  • Lead time giảm {abs(lt_delta):.1f} phút ({ranked.kpi_delta_pcts['avg_lead_time_min']:.0f}%)")

    # 4. Bottleneck insight
    bn = ranked.bottleneck
    if bn.primary:
        parts.append(f"  • Bottleneck: {bn.primary} (utilization {bn.utilizations[bn.primary]:.0%})")
        if bn.primary == baseline_bottleneck:
            parts.append(f"    → Bottleneck chưa thay đổi, cần thêm capacity {bn.primary}")
        else:
            parts.append(f"    → Bottleneck chuyển từ {baseline_bottleneck} sang {bn.primary}")
    else:
        parts.append(f"  • Không có bottleneck rõ ràng — hệ thống cân bằng")

    # 5. Unfinished
    uf = ranked.kpi_deltas["unfinished_count"]
    if uf < 0:
        parts.append(f"  • Giảm {abs(int(uf))} request không kịp giao")

    return "\n".join(parts)
```

### 4.4 Output cho UI WHAT-IF

`ranking.rank()` trả về `list[RankedScenario]` — Hải Anh dùng trực tiếp:

```
┌─────┬──────────────┬───────┬──────────┬──────────┬──────────┬───────────┐
│ Rank│ Scenario     │ Score │Throughput│Lead Time │ Wait     │Unfinished │
│     │              │       │  Δ       │  Δ       │  Δ       │  Δ        │
├─────┼──────────────┼───────┼──────────┼──────────┼──────────┼───────────┤
│ 1   │ +2AMR+1WORKER│ 0.92  │ +5 (+25%)│ -4.2 min │ -3.1 min │ -3        │
│ 2   │ +2AMR        │ 0.78  │ +3 (+15%)│ -2.8 min │ -2.0 min │ -2        │
│ 3   │ BASELINE     │ 0.45  │ —        │ —        │ —        │ —         │
│ ... │              │       │          │          │          │           │
└─────┴──────────────┴───────┴──────────┴──────────┴──────────┴───────────┘
```

---

## 5. Forecast interface — `demand/forecast.py`

### 5.1 Scope v0.1

Rule-based: đọc production plan + BOM → tính demand tương lai. Phát hiện spike bằng ngưỡng.
ML (Prophet/XGBoost) chỉ thêm nếu còn giờ — **không nằm trong scope thiết kế này**.

### 5.2 API

```python
def forecast_demand(
    plan: pd.DataFrame,          # production plan (từ Trung: planner.py)
    bom: dict,                   # BOM config (từ generator.yaml hoặc riêng)
    horizon_hours: int = 4,      # dự báo trước bao nhiêu giờ
    packs_per_trip: dict = {"min": 2, "max": 6},
    start_min: float = 0.0,      # mốc thời gian bắt đầu (phút từ đầu ngày)
) -> list[MaterialRequest]:
    """Dự báo demand từ production plan × BOM.

    Logic tương tự generator/demand.py nhưng CHẠY NGƯỢC:
    - Đọc production plan (planned_qty per hour per line)
    - Nhân BOM → số pack cần
    - Chia cho packs_per_trip trung bình → số lượt giao
    - Rải đều trong từng giờ (deterministic, không random)
    - Trả về list[MaterialRequest] đúng contract

    Trả về chỉ các request có arrival_min trong [start_min, start_min + horizon_hours * 60).
    """


def detect_spikes(
    forecast: list[MaterialRequest],
    window_min: float = 30.0,
    spike_threshold: float = 2.0,
) -> list[dict]:
    """Phát hiện khoảng thời gian có demand cao bất thường.

    Logic:
    - Chia timeline thành window (mặc định 30 phút)
    - Đếm số request mỗi window
    - Tính trung bình và std
    - Window nào có count > mean + spike_threshold × std → spike

    Trả về list dict:
      {"window_start_min": float, "window_end_min": float,
       "request_count": int, "avg_count": float, "severity": "warning" | "critical"}

    severity = "critical" nếu count > mean + 3*std, else "warning"
    """
```

### 5.3 Risk timeline (cho UI NEXT)

```python
def build_risk_timeline(
    forecast: list[MaterialRequest],
    spikes: list[dict],
    state: CurrentLogisticsState,
    interval_min: float = 15.0,
) -> list[dict]:
    """Xây timeline rủi ro cho tab NEXT.

    Mỗi điểm = 1 interval, chứa:
      - time_min: mốc thời gian
      - expected_requests: số request dự kiến đến trong interval
      - risk_level: "low" | "medium" | "high"
      - note: giải thích (ví dụ "demand spike dự kiến", "packer break trùng giờ cao điểm")
    """
```

---

## 6. Data flow tổng hợp

```
CurrentLogisticsState (Trung)
       │
       ├──► build_baseline()           ──► ScenarioConfig BASELINE
       │         │
       │         └──► build_whatif_scenarios()  ──► [ScenarioConfig +1AMR, +2AMR, ...]
       │
ProductionPlan (Trung)
       │
       └──► forecast_demand()          ──► list[MaterialRequest] (demand tương lai)
                │
                └──► detect_spikes()   ──► spike warnings
                │
                └──► build_risk_timeline()  ──► risk timeline cho UI NEXT

ScenarioConfig + demand ──► twin.run() (Nhân) ──► SimulationResult
                                                       │
                                                       └──► rank() ──► list[RankedScenario]
                                                                            │
                                                                            └──► UI WHAT-IF (Hải Anh)
```

---

## Changelog

| Ngày | Thay đổi |
|---|---|
| 2026-10-05 | v0.1 — bản thiết kế đầu tiên |
