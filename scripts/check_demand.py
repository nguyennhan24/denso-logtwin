"""So demand quy đổi từ plan (src/demand) với request thật của Generator (_truth) theo line x giờ.

Cách dùng (từ thư mục gốc project, trong .venv):
    python scripts/check_demand.py                           # ngày 21 seed 42, cả hai mode
    python scripts/check_demand.py data/raw/2026-10-21_seed42 42

Script kiểm chứng nằm ngoài src/demand nên được đọc _truth/. Không chỉnh tham số để ép khớp:
truth là MỘT lần rút Poisson quanh cùng kỳ vọng, nên lệch từng ô line x giờ là bình thường.
"""
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8")

from src.demand.conversion import build_demand_table, load_bom  # noqa: E402
from src.demand.planner import load_plan  # noqa: E402

RAW = Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "data/raw/2026-10-21_seed42"
SEED = int(sys.argv[2]) if len(sys.argv) > 2 else 42


def compare(raw_dir: Path, seed: int, mode: str) -> dict:
    """Cả ngày (as_of = 06:00, horizon = 16 giờ): đếm request theo (line, giờ) của demand và truth."""
    day = pd.read_csv(raw_dir / "production.csv")["hour_start"].min()[:10]
    as_of = f"{day}T06:00:00+07:00"
    plan = load_plan("baseline", as_of, raw_dir=raw_dir, plans_dir=ROOT / "config/plans")
    dem = build_demand_table(plan, load_bom(ROOT / "config/generator.yaml"), as_of, 16 * 60, seed, mode)
    dem = dem.assign(line=dem["destination_line_id"], hour=6 + (dem["arrival_min"] // 60).astype(int))

    truth = pd.read_csv(raw_dir / "_truth" / "requests.csv")
    truth = truth.assign(line=truth["line_id"], hour=6 + (truth["arrival_min"] // 60).astype(int))

    idx = pd.MultiIndex.from_product([sorted(truth["line"].unique()), range(6, 22)], names=["line", "hour"])
    cell = pd.DataFrame({"demand": dem.groupby(["line", "hour"]).size(),
                         "truth": truth.groupby(["line", "hour"]).size()}).reindex(idx, fill_value=0)
    cell["diff"] = cell["demand"] - cell["truth"]
    by_line = cell.groupby("line")[["demand", "truth"]].sum()
    by_hour = cell.groupby("hour")[["demand", "truth"]].sum()
    for t in (by_line, by_hour):
        t["lệch_%"] = (100 * (t["demand"] - t["truth"]) / t["truth"]).round(1)
    nz = cell[cell["truth"] > 0]
    return {
        "mode": mode, "cell": cell, "by_line": by_line, "by_hour": by_hour,
        "total": (int(cell["demand"].sum()), int(cell["truth"].sum())),
        "cell_mae": float(cell["diff"].abs().mean()),
        "cell_mape": float((nz["diff"].abs() / nz["truth"]).mean() * 100),
        "hour_mape": float((by_hour["demand"] - by_hour["truth"]).abs().div(by_hour["truth"]).mean() * 100),
        "qty_mean": (float(dem["quantity"].mean()), float(truth["packs"].mean())),
    }


if __name__ == "__main__":
    print(f"Raw: {RAW} | seed quy đổi {SEED}\n")
    for mode in ("expected", "poisson"):
        r = compare(RAW, SEED, mode)
        d, t = r["total"]
        print(f"===== mode {mode}: tổng {d} vs truth {t} ({100 * (d - t) / t:+.1f}%) =====")
        print("Theo line:\n" + r["by_line"].to_string())
        print("Theo giờ:\n" + r["by_hour"].T.to_string())
        print(f"Ô line x giờ (32 ô): MAE {r['cell_mae']:.2f} request/ô | MAPE {r['cell_mape']:.1f}% "
              f"| MAPE theo giờ {r['hour_mape']:.1f}%")
        print(f"Pack trung bình mỗi lượt: demand {r['qty_mean'][0]:.2f} vs truth {r['qty_mean'][1]:.2f}\n")
    # Mức lệch "tự nhiên": độ lệch chuẩn Poisson của mỗi ô ~ sqrt(kỳ vọng)
    cell = compare(RAW, SEED, "expected")["cell"]
    print(f"Tham chiếu: sqrt(kỳ vọng) trung bình mỗi ô ~ {np.sqrt(cell['demand'].clip(lower=0)).mean():.2f} request "
          "(độ lệch Poisson tự nhiên của truth quanh kỳ vọng)")
