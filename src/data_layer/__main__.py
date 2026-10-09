"""CLI Data Layer.

    python -m src.data_layer --day 21 --seed 42 --as-of 14:10

--day N  -> ngày base_date + (N-1) trong config/data_layer.yaml (mặc định 2026-10-21 cho N=21).
Raw đọc từ data/raw/<ngày>/ (sinh bằng: python -m src.generator --seed 42 --date 2026-10-21).
Output ghi vào data/silver/<ngày>_seed<seed>/ và data/gold/<ngày>_seed<seed>/.
"""
import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

from . import load_config
from .contract_check import ContractError
from .pipeline import run

# Console Windows mặc định cp1258 -> in tiếng Việt bị lỗi
sys.stdout.reconfigure(encoding="utf-8")

p = argparse.ArgumentParser(description="Data Layer: 6 feed thô -> state/order_status/journey tại as_of")
p.add_argument("--day", type=int, required=True, help="ngày thứ mấy (1 = base_date trong config)")
p.add_argument("--seed", type=int, required=True, help="seed đã dùng để sinh raw (chỉ để đặt tên thư mục output)")
p.add_argument("--as-of", required=True, help="giờ chụp trạng thái, HH:MM")
p.add_argument("--raw-dir", default=None, help="bỏ trống = data/raw/<ngày>")
p.add_argument("--config", default="config/data_layer.yaml")
a = p.parse_args()

cfg = load_config(a.config)
day = date.fromisoformat(cfg["base_date"]) + timedelta(days=a.day - 1)
raw_dir = Path(a.raw_dir) if a.raw_dir else Path(cfg["raw_root"]) / day.isoformat()
if not raw_dir.exists():
    sys.exit(f"Không thấy {raw_dir}. Sinh dữ liệu trước:  python -m src.generator --seed {a.seed} --date {day}")

try:
    res = run(raw_dir, f"{day}T{a.as_of}:00", cfg=cfg, out_name=f"{day}_seed{a.seed}")
except ContractError as e:
    sys.exit(str(e))

s = res.state_doc["current_state"]
q = res.quality
print(f"as_of {s['as_of']}  | đơn mở {s['open_request_count']} | queued {len(s['queued_jobs'])} "
      f"| active {len(s['active_jobs'])}")
print("resources:", json.dumps(s["resources"]))
print("quarantine:", q["quarantine_by_reason"] or "0 dòng")
print("nguồn stale:", q["stale_sources"] or "không", "| đơn stale:", q["stale_orders"],
      "| đơn overdue:", q["overdue_orders"])
print("Đã ghi:", res.gold_dir)
