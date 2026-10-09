import json
import pandas as pd
from pathlib import Path

GOLD = Path("data/gold/2026-10-21_seed42")  
HHMM = "1410"

state = json.loads((GOLD / f"state_{HHMM}.json").read_text(encoding="utf-8"))["current_state"]
as_of = pd.Timestamp(state["as_of"])
print("as_of =", as_of)

# 1. Không đơn nào được tạo sau as_of
jobs = state["queued_jobs"] + state["active_jobs"]
future_jobs = [j["request"]["request_id"] for j in jobs if j["created_offset_min"] > 0]
print("Đơn tạo sau as_of:", future_jobs or "không có ✅")

# 2. Mốc cập nhật của các feed không được sau as_of
for feed, ts in state["feed_updated_at"].items():
    flag = "❌ TƯƠNG LAI" if pd.Timestamp(ts) > as_of else "✅"
    print(f"  {feed:11s} {ts}  {flag}")

# 3. Mọi cột thời gian trong các bảng parquet đều <= as_of
for f in GOLD.glob(f"*{HHMM}*.parquet"):
    df = pd.read_parquet(f)
    for col in df.select_dtypes(include=["datetimetz", "datetime"]).columns:
        n_future = (df[col] > as_of).sum()
        print(f"  {f.name}.{col}: {n_future} giá trị sau as_of", "❌" if n_future else "✅")