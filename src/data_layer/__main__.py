"""CLI: python -m src.data_layer --date 2026-10-01 --as-of 10:00 --as-of 14:30"""
import argparse
import sys
from pathlib import Path

from .pipeline import ContractError, run

p = argparse.ArgumentParser(description="Data Layer - dựng silver/gold + CurrentLogisticsState")
p.add_argument("--date", required=True, help="ngày dữ liệu, ví dụ 2026-10-01")
p.add_argument("--as-of", dest="as_of", action="append", required=True,
               help='"HH:MM" hoặc ISO; lặp lại để lấy nhiều mốc')
p.add_argument("--raw-dir", default="data/raw")
p.add_argument("--out-dir", default="data/unified")
p.add_argument("--config", default=None, help="mặc định config/data_layer.yaml")
a = p.parse_args()

day_dir = Path(a.raw_dir) / a.date
if not day_dir.exists():
    sys.exit(f"Không thấy {day_dir}. Sinh dữ liệu trước: python -m src.generator --date {a.date}")
try:
    s = run(a.date, a.as_of, raw_dir=a.raw_dir, out_dir=a.out_dir, config_path=a.config)
except ContractError as e:
    sys.exit(str(e))

print(f"Silver: {s['silver']}")
print(f"Gold:   {s['gold']}")
for r in s["as_of"]:
    print(f"  {r['as_of']}: queued={r['queued']} active={r['active']} "
          f"cách ly={r['quarantined_rows']} ước lượng={r['estimated_jobs']}")
