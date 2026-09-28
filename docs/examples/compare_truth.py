"""So state do Data Layer dựng với ĐÁP ÁN (_truth/) tại vài mốc giờ.

Dùng (PowerShell, đứng ở gốc repo):
    python docs/examples/compare_truth.py
    python docs/examples/compare_truth.py --as-of 09:30 --as-of 15:45

Chỉ script kiểm tra này (và tests/) được đọc _truth/. Code trong src/data_layer/ thì KHÔNG.
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from src.data_layer.ingest import load_config, load_day, parse_as_of  # noqa: E402
from tests.test_data_layer import state_at, state_sets, truth_stage_sets  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--date", default="2026-10-01")
p.add_argument("--raw-dir", default="data/raw")
p.add_argument("--as-of", dest="as_of", action="append")
a = p.parse_args()

cfg = load_config()
day = Path(a.raw_dir) / a.date
raw = load_day(day, cfg)
truth = pd.read_csv(day / "_truth" / "requests.csv")

print(f"{'as_of':6s} {'ô của state':18s} {'ta':>4s} {'đáp án':>7s}  kết quả")
n_ok = n = 0
for hhmm in a.as_of or ["08:00", "10:00", "14:00", "18:00"]:
    t = parse_as_of(hhmm, a.date, cfg)
    ours, real = state_sets(state_at(raw, t)[2]), truth_stage_sets(truth, t)
    for (stage, status), ids in real.items():
        ok = ours[(stage, status)] == ids
        n_ok, n = n_ok + ok, n + 1
        diff = "" if ok else f"  lệch: {sorted(ours[(stage, status)] ^ ids)}"
        print(f"{hhmm:6s} {stage + ' ' + status:18s} {len(ours[(stage, status)]):4d} {len(ids):7d}  "
              f"{'KHỚP' if ok else 'LỆCH'}{diff}")
print(f"\nKhớp chính xác {n_ok}/{n} ô.")
