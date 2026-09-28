"""ids.py — Quy đổi các kiểu ID lượt giao / mã line / mã vật tư về ID chuẩn.

Vì sao tách riêng: cả schemas.py (kiểm tra tham chiếu) lẫn mapper.py (ghép hành trình) đều cần
cùng một quy tắc đổi ID. Để ở một chỗ thì hai bên không thể lệch nhau.

Chuỗi ghép (xem docs/feeds.md):
  wms.pick_no "PICK-00012" = request_id chuẩn
  agv.label   "PK12"   -> 12 -> "PICK-00012"
  excel.pick   12      ->       "PICK-00012"
"""
from __future__ import annotations

import pandas as pd


def pick_no_from_num(n: pd.Series) -> pd.Series:
    """Số phiếu (Int64) -> "PICK-00012". NA giữ NA."""
    n = pd.to_numeric(n, errors="coerce").astype("Int64")
    out = "PICK-" + n.astype("string").str.zfill(5)
    return out.where(n.notna() & (n > 0))


def pick_no_from_label(label: pd.Series) -> pd.Series:
    """Nhãn AGV "PK12" -> "PICK-00012". Nhãn sai dạng -> NA."""
    num = label.astype("string").str.extract(r"^PK(\d+)$")[0]
    return pick_no_from_num(num)


def master_map(master: pd.DataFrame, kind: str, system: str) -> dict[str, str]:
    """Bảng đổi mã của một hệ thống sang ID chuẩn, ví dụ ("line", "agv"): ST-A1 -> L1."""
    m = master[(master["kind"] == kind) & (master["system"] == system)]
    return dict(zip(m["code"], m["canonical_id"]))
