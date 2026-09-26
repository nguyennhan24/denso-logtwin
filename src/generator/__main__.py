import argparse

from . import generate_days

p = argparse.ArgumentParser(description="World Generator - sinh 6 feed nhà máy giả")
p.add_argument("--seed", type=int, default=42)
p.add_argument("--date", default="2026-10-01")
p.add_argument("--days", type=int, default=1)
p.add_argument("--out", default="data/raw")
a = p.parse_args()
generate_days(a.date, a.days, base_seed=a.seed, out_dir=a.out)
print(f"Đã sinh {a.days} ngày vào {a.out}/")
