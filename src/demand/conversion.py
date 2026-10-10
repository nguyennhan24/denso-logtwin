"""conversion.py — Plan x BOM -> danh sách MaterialRequest (Phase 6, Trung).

CHỈ sinh tải đầu vào cho Twin. Không đoán nghẽn, không tính KPI, không gán bottleneck.

Nguồn định mức: config/generator.yaml (products.*.bom_packs_per_unit, packs_per_trip, need_by_buffer_min,
origin_id) — cùng một nguồn với Generator, không có bản số liệu thứ hai.

Quy tắc (mỗi ô = một giờ plan x line x product x material):
  packs  = planned_qty x bom_packs_per_unit           (số pack cần trong giờ)
  lam    = packs / mean(packs_per_trip)                (số lượt kỳ vọng; mean = (2 + 6) / 2 = 4)

  Bước ĐẾM (count_trips) — khác nhau giữa 2 mode:
    expected (mặc định, tất định): n = round(cumsum(lam)) - round(cumsum(lam) giờ trước), cộng dồn phần dư theo
             (line, product, material) qua các giờ trong ngày -> tổng cả ngày = round(tổng lam), không lệch hệ thống.
             Tổng pack của giờ cũng cộng dồn y như vậy.
    poisson  (giống src/generator/demand.py): n ~ Poisson(lam).
  Bước RẢI (place_trips) — dùng chung khung, khác cách rải:
    expected: chia giờ thành n khoảng bằng nhau, lượt i rơi ngẫu nhiên trong khoảng thứ i (jitter có seed);
              quantity = chia đều tổng pack của giờ cho n lượt, kẹp trong [min, max] của packs_per_trip.
    poisson : n thời điểm Uniform(0, 60) trong giờ; quantity ~ số nguyên đều trong [min, max].
  need_by_at = created_at + need_by_buffer_min (chỉ là cột phụ trong parquet, KHÔNG vào request — xem B10).

Ngẫu nhiên: mỗi ô có rng riêng = default_rng([seed, crc32(khóa ô)]). Cùng (plan, seed) -> cùng kết quả;
đổi plan ở một giờ không làm xê dịch số ngẫu nhiên của giờ khác; kết quả không phụ thuộc as_of
(sinh cả ngày rồi mới cắt cửa sổ [as_of, as_of + horizon)).

Thời gian: arrival_min = phút từ as_of (contract v0.1 mục 2: as_of = phút 0), làm tròn 3 chữ số thập phân.
request_id = DMD-<HHMM của as_of>-<00001>, đánh số theo (arrival_min, line, material) — khác dạng PICK-xxxxx của WIP.
"""
from __future__ import annotations

import json
import zlib
from pathlib import Path

import numpy as np
import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, NonNegativeFloat, PositiveInt, ValidationError, model_validator

from contracts.validate_contracts import check_bundle
from src.data_layer.ingest import to_as_of

from .planner import HOUR_MIN, PlanError

MODES = ("expected", "poisson")
CONTRACT_FIELDS = ["request_id", "arrival_min", "origin_id", "destination_line_id", "material_id", "quantity"]
SIDECAR_FIELDS = ["created_at", "need_by_at", "hour_start", "product", "plan_id", "mode", "seed"]
CELL_KEY = ["line_id", "product", "material_id"]


class ProductBom(BaseModel):
    model_config = ConfigDict(extra="allow")       # generator.yaml còn base_units_per_hour (không dùng ở đây)
    line: str
    bom_packs_per_unit: dict[str, NonNegativeFloat]


class PacksPerTrip(BaseModel):
    min: PositiveInt
    max: PositiveInt

    @model_validator(mode="after")
    def _order(self):
        if self.min > self.max:
            raise ValueError("packs_per_trip.min > max")
        return self


class Bom(BaseModel):
    """Phần định mức đọc từ config/generator.yaml."""
    origin_id: str = Field(min_length=1)
    products: dict[str, ProductBom]
    packs_per_trip: PacksPerTrip
    need_by_buffer_min: NonNegativeFloat

    @property
    def mean_packs(self) -> float:
        return (self.packs_per_trip.min + self.packs_per_trip.max) / 2

    @property
    def lines(self) -> set[str]:
        return {p.line for p in self.products.values()}


def load_bom(path: str | Path = "config/generator.yaml") -> Bom:
    with open(path, encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    try:
        return Bom.model_validate({k: cfg.get(k) for k in Bom.model_fields})
    except ValidationError as e:
        raise PlanError(f"{path}: định mức sai: {e}") from e


def _cell_rng(seed: int, *key) -> np.random.Generator:
    """rng riêng cho một ô; crc32 ổn định giữa các lần chạy (hash() của Python thì không)."""
    return np.random.default_rng([int(seed), zlib.crc32("|".join(map(str, key)).encode("utf-8"))])


def _round_half_up(x: np.ndarray) -> np.ndarray:
    return np.floor(np.asarray(x, dtype=float) + 0.5).astype(int)


def _carry_round(values: pd.Series) -> np.ndarray:
    """Làm tròn có cộng dồn phần dư: tổng các phần = round(tổng)."""
    cum = _round_half_up(values.cumsum().to_numpy())
    return np.diff(np.concatenate([[0], cum]))


def count_trips(plan: pd.DataFrame, bom: Bom, mode: str = "expected", seed: int = 42) -> pd.DataFrame:
    """Hàm ĐẾM: mỗi ô (hour_start, line, product, material) -> lam, packs, n_trips (+ packs_total khi expected)."""
    if mode not in MODES:
        raise ValueError(f"mode phải là một trong {MODES}")
    unknown = sorted(set(plan["product"]) - set(bom.products))
    if unknown:
        raise PlanError(f"Plan có product không có BOM trong config/generator.yaml: {unknown}")
    bad_line = sorted(set(plan["line_id"]) - bom.lines)
    if bad_line:
        raise PlanError(f"Plan có line không phải ID chuẩn {sorted(bom.lines)}: {bad_line}")

    rows = []
    for r in plan.itertuples(index=False):
        for material_id, ppu in bom.products[r.product].bom_packs_per_unit.items():
            packs = float(r.planned_qty) * ppu
            rows.append({"hour_start": r.hour_start, "line_id": r.line_id, "product": r.product,
                         "material_id": material_id, "plan_id": r.plan_id,
                         "packs": packs, "lam": packs / bom.mean_packs})
    cells = pd.DataFrame(rows, columns=["hour_start", *CELL_KEY, "plan_id", "packs", "lam"])
    cells = cells.sort_values([*CELL_KEY, "hour_start"], kind="stable").reset_index(drop=True)

    if mode == "expected":
        cells["n_trips"] = 0
        cells["packs_total"] = 0
        for _, idx in cells.groupby(CELL_KEY, sort=False).groups.items():
            cells.loc[idx, "n_trips"] = _carry_round(cells.loc[idx, "lam"])
            cells.loc[idx, "packs_total"] = _carry_round(cells.loc[idx, "packs"])
    else:
        cells["n_trips"] = [
            int(_cell_rng(seed, "count", c.hour_start.isoformat(), c.line_id, c.product, c.material_id).poisson(c.lam))
            for c in cells.itertuples(index=False)
        ]
        cells["packs_total"] = pd.NA
    return cells


def _split_even(total: int, n: int, lo: int, hi: int) -> np.ndarray:
    """Chia `total` pack cho n lượt càng đều càng tốt (phần dư cho các lượt đầu), rồi kẹp [lo, hi]."""
    q = np.full(n, total // n) + (np.arange(n) < total % n)
    return np.clip(q, lo, hi)


def place_trips(cells: pd.DataFrame, bom: Bom, mode: str = "expected", seed: int = 42) -> pd.DataFrame:
    """Hàm RẢI: mỗi ô có n_trips lượt -> mỗi lượt một dòng (created_at, quantity, ...)."""
    lo, hi = bom.packs_per_trip.min, bom.packs_per_trip.max
    out = []
    for c in cells.itertuples(index=False):
        n = int(c.n_trips)
        if n <= 0:
            continue
        rng = _cell_rng(seed, "place", c.hour_start.isoformat(), c.line_id, c.product, c.material_id)
        if mode == "expected":
            offsets = (np.arange(n) + rng.uniform(0, 1, size=n)) * (HOUR_MIN / n)
            qty = _split_even(int(c.packs_total), n, lo, hi)
        else:
            offsets = np.sort(rng.uniform(0, HOUR_MIN, size=n))
            qty = rng.integers(lo, hi + 1, size=n)
        for off, q in zip(offsets, qty):
            out.append({"created_at": c.hour_start + pd.Timedelta(minutes=float(off)),
                        "hour_start": c.hour_start, "line_id": c.line_id, "product": c.product,
                        "material_id": c.material_id, "plan_id": c.plan_id, "quantity": int(q)})
    return pd.DataFrame(out, columns=["created_at", "hour_start", *CELL_KEY, "plan_id", "quantity"])


def build_demand_table(plan: pd.DataFrame, bom: Bom, as_of, horizon_min: float, seed: int,
                       mode: str = "expected", tz: str = "Asia/Ho_Chi_Minh") -> pd.DataFrame:
    """Plan -> bảng demand trong [as_of, as_of + horizon): 6 trường contract + cột phụ (SIDECAR_FIELDS)."""
    if not horizon_min > 0:
        raise ValueError("horizon_min phải > 0")
    if isinstance(seed, bool) or not isinstance(seed, (int, np.integer)) or seed < 0:
        raise ValueError("seed phải là số nguyên >= 0")
    as_of = to_as_of(as_of, tz)

    trips = place_trips(count_trips(plan, bom, mode, seed), bom, mode, seed)
    exact = (trips["created_at"] - as_of).dt.total_seconds() / 60.0
    trips["arrival_min"] = exact.round(3)
    # Lọc trên giá trị chưa làm tròn ở mép dưới (không nhận lượt tạo trước as_of), giá trị đã làm tròn ở mép trên
    df = trips[(exact >= 0) & (trips["arrival_min"] < horizon_min)].copy()
    df = df.sort_values(["arrival_min", "line_id", "material_id", "product"], kind="stable").reset_index(drop=True)

    hhmm = as_of.strftime("%H%M")
    df.insert(0, "request_id", [f"DMD-{hhmm}-{i:05d}" for i in range(1, len(df) + 1)])
    df["origin_id"] = bom.origin_id
    df["destination_line_id"] = df["line_id"]
    df["need_by_at"] = df["created_at"] + pd.Timedelta(minutes=bom.need_by_buffer_min)
    df["mode"] = mode
    df["seed"] = int(seed)
    df = df[CONTRACT_FIELDS + SIDECAR_FIELDS].astype({"quantity": "int64", "arrival_min": "float64"})

    errors = check_bundle({"demand": to_contract_records(df)}, horizon_min=horizon_min)
    if errors:   # không được xảy ra; nếu có là lỗi code -> chặn trước khi giao cho Twin
        raise ValueError("Demand sinh ra sai contract v0.1: " + "; ".join(errors[:5]))
    return df


def to_contract_records(table: pd.DataFrame) -> list[dict]:
    """Chỉ 6 trường contract v0.1, kiểu Python thuần (JSON được, MaterialRequest(**r) được)."""
    return [{"request_id": str(r.request_id), "arrival_min": float(r.arrival_min), "origin_id": str(r.origin_id),
             "destination_line_id": str(r.destination_line_id), "material_id": str(r.material_id),
             "quantity": int(r.quantity)}
            for r in table[CONTRACT_FIELDS].itertuples(index=False)]


def build_demand(plan: pd.DataFrame, bom: Bom, as_of, horizon_min: float, seed: int,
                 mode: str = "expected") -> list:
    """Hàm chính: -> list[src.twin.entities.MaterialRequest] để gọi thẳng twin.run(config, demand, state)."""
    from src.twin.entities import MaterialRequest   # chỉ đọc dataclass của Twin, không sửa
    table = build_demand_table(plan, bom, as_of, horizon_min, seed, mode)
    return [MaterialRequest(**r) for r in to_contract_records(table)]


def write_demand(table: pd.DataFrame, out_dir: str | Path, stem: str) -> tuple[Path, Path]:
    """Ghi <stem>.parquet (contract + cột phụ) và <stem>.json (list 6 trường, đọc được bằng runner --demand)."""
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    pq, js = out_dir / f"{stem}.parquet", out_dir / f"{stem}.json"
    table.to_parquet(pq, index=False)
    js.write_text(json.dumps(to_contract_records(table), ensure_ascii=False, indent=1, allow_nan=False),
                  encoding="utf-8")
    return pq, js
