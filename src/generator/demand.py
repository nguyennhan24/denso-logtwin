"""demand.py — Sinh kế hoạch sản xuất và các lượt yêu cầu vật tư.

Chuỗi logic (cũng là thứ Phase 6 sẽ "làm ngược lại"):
    kế hoạch sản xuất (sản phẩm/giờ)
      x BOM (pack vật tư / sản phẩm)
      = số pack cần mỗi giờ
      / số pack trung bình mỗi lượt
      = tốc độ lượt giao mỗi giờ (lambda)
    -> Poisson: số lượt thực tế trong giờ đó ngẫu nhiên quanh lambda,
       thời điểm trong giờ rải ngẫu nhiên.
"""
from __future__ import annotations

import pandas as pd

from .world import World


def make_plan(w: World) -> pd.DataFrame:
    """Kế hoạch sản xuất theo giờ cho từng line."""
    cfg = w.cfg
    rows = []
    for hour_idx, factor in enumerate(cfg["hour_profile"]):
        for product, p in cfg["products"].items():
            noise = w.rng.normal(1.0, cfg["plan_noise_cv"])
            qty = max(0, round(p["base_units_per_hour"] * factor * noise))
            rows.append(
                {"hour_idx": hour_idx, "line_id": p["line"], "product": product, "planned_qty": qty}
            )
    return pd.DataFrame(rows)


def make_requests(w: World, plan: pd.DataFrame) -> pd.DataFrame:
    """Biến kế hoạch thành danh sách lượt giao (ground truth, chưa bị làm 'bẩn')."""
    cfg = w.cfg
    ppt = cfg["packs_per_trip"]
    mean_packs = (ppt["min"] + ppt["max"]) / 2

    rows = []
    for r in plan.itertuples():
        bom = cfg["products"][r.product]["bom_packs_per_unit"]
        for material_id, packs_per_unit in bom.items():
            lam = r.planned_qty * packs_per_unit / mean_packs     # số lượt kỳ vọng trong giờ
            n = w.rng.poisson(lam)
            offsets = w.rng.uniform(0, 60, size=n)
            for off in offsets:
                arrival = r.hour_idx * 60 + float(off)
                rows.append(
                    {
                        "arrival_min": arrival,
                        "origin_id": cfg["origin_id"],
                        "line_id": r.line_id,
                        "material_id": material_id,
                        "packs": int(w.rng.integers(ppt["min"], ppt["max"] + 1)),
                        "need_by_min": arrival + cfg["need_by_buffer_min"],
                    }
                )

    df = pd.DataFrame(rows).sort_values("arrival_min", kind="stable").reset_index(drop=True)
    # Đánh số phiếu theo thứ tự thời gian -> pick_no là khóa gốc của mỗi lượt
    df.insert(0, "pick_no", [f"PICK-{i:05d}" for i in range(1, len(df) + 1)])
    return df
