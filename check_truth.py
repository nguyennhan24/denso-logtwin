"""Đối chiếu bảng journey của Data Layer với ground truth của Generator.

Cách dùng (từ thư mục gốc project, trong .venv):
    python check_truth.py
    python check_truth.py data/raw/2026-10-21_seed42 data/gold/2026-10-21_seed42

Lưu ý: journey dựng tại as_of nào thì chỉ chứa đơn đến as_of đó.
Để so toàn ngày, chạy pipeline với as_of cuối ngày (ví dụ 23:59) trước.
"""
import re
import sys
from pathlib import Path

import pandas as pd

# Mặc định raw có seed trong tên; máy còn layout cũ (data/raw/<ngày>/) thì lùi về đó
RAW = Path(sys.argv[1]) if len(sys.argv) > 1 else next(
    (p for p in (Path("data/raw/2026-10-21_seed42"), Path("data/raw/2026-10-21")) if p.exists()),
    Path("data/raw/2026-10-21_seed42"))
GOLD = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("data/gold/2026-10-21_seed42")

# Ưu tiên cột mã đơn theo thứ tự này
ID_CANDIDATES = ["request_id", "pick_id", "pick_no", "order_id", "request_no"]


def pick_file(folder, pattern, prefer_word, what):
    found = sorted(folder.glob(pattern))
    if not found:
        sys.exit(f"Không tìm thấy {what} ({folder / pattern}).")
    preferred = [f for f in found if prefer_word in f.name.lower()]
    chosen = (preferred or found)[0]
    print(f"{what}: dùng {chosen.name}  (có: {[f.name for f in found]})")
    return chosen


def id_column(df, name):
    for c in ID_CANDIDATES:
        if c in df.columns:
            return c
    loose = [c for c in df.columns if "request" in c.lower() or "pick" in c.lower()]
    if loose:
        return loose[0]
    sys.exit(f"Không thấy cột mã đơn trong {name}. Các cột: {list(df.columns)}\n"
             f"Sửa ID_CANDIDATES ở đầu file cho đúng tên cột.")


def to_number(x):
    """'PICK-00216', 'pick216', 216, '216.0' -> 216. Để so hai bên dù định dạng khác nhau."""
    if pd.isna(x):
        return None
    if isinstance(x, (int, float)):
        return int(x)
    digits = re.findall(r"\d+", str(x))
    return int(digits[0]) if digits else None


truth_file = pick_file(RAW / "_truth", "*.csv", "request", "File truth")
journey_file = pick_file(GOLD, "*journey*.parquet", "journey", "File journey")

truth = pd.read_csv(truth_file)
journey = pd.read_parquet(journey_file)
t_col, j_col = id_column(truth, truth_file.name), id_column(journey, journey_file.name)
print(f"\nCột mã đơn: truth = {t_col}, journey = {j_col}")
print(f"Ví dụ truth:   {truth[t_col].head(3).tolist()}")
print(f"Ví dụ journey: {journey[j_col].head(3).tolist()}\n")

t_ids = {v for v in truth[t_col].map(to_number) if v is not None}
j_ids = {v for v in journey[j_col].map(to_number) if v is not None}
both = t_ids & j_ids
missing = sorted(t_ids - j_ids)
extra = sorted(j_ids - t_ids)

print(f"Đơn trong truth:                  {len(t_ids)}")
print(f"Đơn Data Layer dựng lại được:     {len(both)} ({len(both) / max(len(t_ids), 1):.1%})  — mục tiêu ≥ 95%")
print(f"Đơn truth có mà journey thiếu:    {len(missing)}  ví dụ {missing[:5]}")
print(f"Đơn journey có mà truth không có: {len(extra)}  ví dụ {extra[:5]}  — phải bằng 0")
print("\nNếu nhiều đơn bị thiếu: kiểm journey có dựng ở as_of cuối ngày không,")
print("rồi xem các đơn thiếu có trong quarantine.csv không, với reason gì.")