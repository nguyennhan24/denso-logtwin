"""Kiểm tra một file state_<HHMM>.json (Gold) theo contract v0.1 và in tóm tắt dễ đọc.

Dùng (PowerShell, đứng ở gốc repo):
    python docs/examples/check_state_file.py data/unified/2026-10-01/gold/state_1000.json

Lưu ý: KHÔNG dùng `python contracts/validate_contracts.py <file state>` — lệnh đó chờ một
"bundle" có key current_state, gặp file state trần sẽ báo HỢP LỆ mà không kiểm tra gì.
"""
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from contracts.validate_contracts import check_bundle  # noqa: E402

path = Path(sys.argv[1])
state = json.loads(path.read_text(encoding="utf-8"))
errs = check_bundle({"current_state": state, "demand": []})

print(f"as_of: {state['as_of']}")
for stage, r in state["resources"].items():
    queued = [q["request"]["request_id"] for q in state["queued_jobs"] if q["stage"] == stage]
    active = [(x["request"]["request_id"], x["remaining_min"]) for x in state["active_jobs"] if x["stage"] == stage]
    print(f"  {stage:6s} total={r['total']} busy={r['busy']} offline={r['offline']}  "
          f"chờ={len(queued)} {queued}")
    for rid, rem in active:
        print(f"         đang làm {rid}, còn ~{rem} phút")
print("feed_updated_at:", state["feed_updated_at"])
print("\nHỢP LỆ theo contract v0.1" if not errs else "LỖI:\n" + "\n".join(f"- {e}" for e in errs))
sys.exit(1 if errs else 0)
