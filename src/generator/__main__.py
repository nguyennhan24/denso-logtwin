import argparse
import sys

from . import generate_days

# Console Windows mặc định cp1258 -> in tiếng Việt bị lỗi
sys.stdout.reconfigure(encoding="utf-8")

p = argparse.ArgumentParser(description="World Generator - sinh 6 feed nhà máy giả")
p.add_argument("--seed", type=int, default=42)
p.add_argument("--date", default="2026-10-01")
p.add_argument("--days", type=int, default=1, help="ngày thứ i dùng seed = --seed + i")
p.add_argument("--out", default="data/raw")
p.add_argument("--legacy-dir", action="store_true",
               help="ghi vào <out>/<ngày>/ như cũ (không có seed trong tên, đổi seed sẽ bị ghi đè)")
a = p.parse_args()
generate_days(a.date, a.days, base_seed=a.seed, out_dir=a.out, seed_in_dirname=not a.legacy_dir)
layout = "<ngày>" if a.legacy_dir else "<ngày>_seed<seed>"
print(f"Đã sinh {a.days} ngày vào {a.out}/{layout}/")
