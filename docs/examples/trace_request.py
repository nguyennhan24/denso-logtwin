"""Lần theo MỘT lượt giao qua 4 hệ thống, rồi so với dòng journey mà Data Layer ghép ra.

Dùng (PowerShell, đứng ở gốc repo):
    python docs/examples/trace_request.py --request PICK-00050
    python docs/examples/trace_request.py --request PICK-00050 --as-of 08:15

Mục đích: tự kiểm tra bằng mắt rằng mapper ghép đúng. Script chỉ đọc 6 feed (không đọc _truth/).
"""
import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pandas as pd  # noqa: E402

from src.data_layer.ingest import load_config, load_day, parse_as_of  # noqa: E402
from src.data_layer.mapper import build_journey  # noqa: E402
from src.data_layer.schemas import validate_feeds  # noqa: E402

p = argparse.ArgumentParser()
p.add_argument("--request", required=True, help="pick_no, ví dụ PICK-00050")
p.add_argument("--date", default="2026-10-01")
p.add_argument("--raw-dir", default="data/raw")
p.add_argument("--as-of", default="23:59", help='mặc định "23:59" = cuối ngày')
a = p.parse_args()

cfg = load_config()
raw = load_day(Path(a.raw_dir) / a.date, cfg)
rid = a.request
num = int(rid.split("-")[1])
pd.set_option("display.width", 200)

print(f"=== 1) WMS: dòng có pick_no = {rid}")
print(raw.wms[raw.wms["pick_no"] == rid].drop(columns=["created_min", "need_by_min"]).to_string())

print(f"\n=== 2) AMR: tìm DISPATCH có wms_ref = {rid} -> lấy task_id -> mọi sự kiện của task")
disp = raw.amr[(raw.amr["event"] == "DISPATCH") & (raw.amr["wms_ref"] == rid)]
if len(disp):
    task = disp["task_id"].iloc[0]
    print(raw.amr[raw.amr["task_id"] == task].drop(columns=["ts_min"]).to_string())
else:
    print("(chưa có DISPATCH)")

print(f"\n=== 3) AGV: nhãn PK{num}  (epoch giây, múi giờ UTC)")
agv = raw.agv[raw.agv["label"] == f"PK{num}"]
print(agv.drop(columns=["load_min", "deliver_min"]).to_string() if len(agv) else "(chưa load)")
for c in ("load_epoch", "deliver_epoch"):
    if len(agv) and pd.notna(agv[c].iloc[0]):
        ts = pd.Timestamp(int(agv[c].iloc[0]), unit="s", tz="UTC").tz_convert("+07:00")
        print(f"   {c} = {ts.isoformat()}")

print(f"\n=== 4) Excel: pick = {num}  (end chỉ có độ phân giải phút; entered_at = lúc nhập)")
ex = raw.excel[raw.excel["pick"] == num]
print(ex.drop(columns=["start_min", "end_min", "entered_min"]).to_string() if len(ex) else "(không có dòng — Excel thiếu)")

t = parse_as_of(a.as_of, a.date, cfg)
clean, _ = validate_feeds(raw)
j = build_journey(clean, t)
print(f"\n=== 5) Journey tại {a.as_of} (t_min = phút kể từ {cfg['day_start']})")
row = j[j["request_id"] == rid]
if len(row):
    r = row.iloc[0]
    for c, v in r.items():
        extra = ""
        if c.endswith("_min") and pd.notna(v):
            extra = f"   = {(pd.Timestamp(raw.day_start) + pd.Timedelta(minutes=float(v))).round('s').strftime('%H:%M:%S')}"
        print(f"   {c:22s} {v}{extra}")
else:
    print("(phiếu chưa tồn tại tại thời điểm này)")
