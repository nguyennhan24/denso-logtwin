"""CLI Demand (Phase 6): production plan -> MaterialRequest tương lai cho NEXT.

    python -m src.demand --day 21 --seed 42 --as-of 14:10 --plan spike_14_16_x3
    python -m src.demand --day 21 --seed 42 --as-of 14:10 --plan baseline --mode poisson --horizon 60

--day/--seed giống Data Layer: chọn raw data/raw/<ngày>_seed<seed>/ (đọc production.csv + master.csv).
--seed cũng là seed của phần ngẫu nhiên trong quy đổi.
Ghi: data/gold/<ngày>_seed<seed>/demand_<plan>[_poisson]_HHMM.parquet  (6 trường contract + cột phụ)
                                   demand_<plan>[_poisson]_HHMM.json     (list 6 trường, cho runner --demand)
"""
import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from src.data_layer import load_config, resolve_raw_dir

from .conversion import MODES, build_demand_table, load_bom, write_demand
from .planner import HOUR_MIN, PlanError, load_plan

# Console Windows mặc định cp1258 -> in tiếng Việt bị lỗi
sys.stdout.reconfigure(encoding="utf-8")

p = argparse.ArgumentParser(description="Demand: production plan -> MaterialRequest tương lai")
p.add_argument("--day", type=int, required=True, help="ngày thứ mấy (1 = base_date trong config Data Layer)")
p.add_argument("--seed", type=int, required=True, help="seed của raw và của phần ngẫu nhiên khi quy đổi")
p.add_argument("--as-of", required=True, help="giờ bắt đầu dự báo, HH:MM (= phút 0 của Twin)")
p.add_argument("--plan", default="baseline", help="tên trong config/plans/ hoặc đường dẫn YAML")
p.add_argument("--mode", choices=MODES, default="expected")
p.add_argument("--horizon", type=float, default=140.0, help="độ dài cửa sổ, phút (mặc định 140 = 14:10 -> 16:30)")
p.add_argument("--raw-dir", default=None, help="bỏ trống = data/raw/<ngày>_seed<seed>")
p.add_argument("--plans-dir", default="config/plans")
p.add_argument("--config", default="config/data_layer.yaml")
p.add_argument("--world-config", default="config/generator.yaml", help="nguồn BOM / packs_per_trip")
a = p.parse_args()

cfg = load_config(a.config)
day = date.fromisoformat(cfg["base_date"]) + timedelta(days=a.day - 1)
if a.raw_dir:
    raw_dir = Path(a.raw_dir)
else:
    raw_dir, legacy = resolve_raw_dir(cfg["raw_root"], day.isoformat(), a.seed)
    if legacy:
        print(f"CẢNH BÁO: không có {raw_dir}_seed{a.seed}, dùng thư mục cũ {raw_dir} (không rõ seed thật)")
as_of = f"{day}T{a.as_of}:00"

try:
    plan = load_plan(a.plan, as_of, raw_dir=raw_dir if raw_dir.exists() else None,
                     plans_dir=a.plans_dir, tz=cfg["timezone"])
    table = build_demand_table(plan, load_bom(a.world_config), as_of, a.horizon, a.seed, a.mode, cfg["timezone"])
except (PlanError, FileNotFoundError, ValueError) as e:
    sys.exit(f"Lỗi: {e}")

plan_id = plan["plan_id"].iloc[0] if len(plan) else Path(a.plan).stem
hhmm = a.as_of.replace(":", "")
stem = f"demand_{plan_id}{'' if a.mode == 'expected' else '_' + a.mode}_{hhmm}"
pq, js = write_demand(table, Path(cfg["gold_root"]) / f"{day}_seed{a.seed}", stem)

print(f"plan {plan_id} | mode {a.mode} | as_of {as_of} | horizon {a.horizon:g} phút | {len(table)} request")
if len(table):
    by = (table.assign(gio=table["created_at"].dt.strftime("%H:00"))
          .pivot_table(index="gio", columns="destination_line_id", values="request_id",
                       aggfunc="count", fill_value=0))
    print(by.to_string())
if len(plan):
    plan_end = plan["hour_start"].max() + pd.Timedelta(minutes=HOUR_MIN)
    if pd.Timestamp(as_of).tz_localize(cfg["timezone"]) + pd.Timedelta(minutes=a.horizon) > plan_end:
        print(f"Lưu ý: plan chỉ phủ tới {plan_end.strftime('%H:%M')}, phần cửa sổ sau đó không có demand")
print("Đã ghi:", pq)
print("       ", js)
