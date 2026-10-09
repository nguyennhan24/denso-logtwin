"""So sánh state ở nhiều mốc as_of trong cùng một ngày.

Cách dùng (từ thư mục gốc project, trong .venv):
    python check_times.py
    python check_times.py data/gold/2026-10-21_seed42

Trước đó chạy pipeline cho vài mốc, ví dụ 09:00, 14:10, 16:30.
"""
import json
import sys
from pathlib import Path

GOLD = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data/gold/2026-10-21_seed42")

files = sorted(GOLD.glob("state_*.json"))
if not files:
    sys.exit(f"Không thấy file state_*.json trong {GOLD}. Kiểm tra lại đường dẫn hoặc chạy pipeline trước.")

print(f"Thư mục: {GOLD}\n")
header = f"{'as_of':6s} {'mở':>4s} {'chờ AMR':>8s} {'chờ pack':>9s} {'AMR bận/chặn/rảnh/hỏng':>24s} {'pack bận/rảnh':>14s} {'overdue':>8s} {'tổng AMR':>9s}"
print(header)
print("-" * len(header))

problems = []
for f in files:
    s = json.loads(f.read_text(encoding="utf-8"))["current_state"]
    rc = s["resource_counts"]
    a, p = rc["amr"], rc["packer"]
    q = s["queued_jobs"]
    t = s["as_of"][11:16]

    amr_sum = a["busy"] + a.get("blocked", 0) + a["idle"] + a["offline"]
    ok_sum = "✅" if amr_sum == a["total"] else "❌"
    if amr_sum != a["total"]:
        problems.append(f"{t}: tổng AMR {amr_sum} ≠ total {a['total']}")

    future = [j["request"]["request_id"] for j in q + s["active_jobs"] if j.get("created_offset_min", 0) > 0]
    if future:
        problems.append(f"{t}: đơn tạo sau as_of {future}")

    for feed, ts in s["feed_updated_at"].items():
        if ts > s["as_of"]:
            problems.append(f"{t}: feed {feed} cập nhật lúc {ts}, sau as_of")

    amr_txt = f"{a['busy']}/{a.get('blocked', 0)}/{a['idle']}/{a['offline']}"
    pack_txt = f"{p['busy']}/{p['idle']}"
    print(f"{t:6s} {s['open_request_count']:4d} "
          f"{sum(j['stage'] == 'amr' for j in q):8d} {sum(j['stage'] == 'packer' for j in q):9d} "
          f"{amr_txt:>24s} {pack_txt:>14s} "
          f"{sum(j['overdue'] for j in s['active_jobs']):8d} {ok_sum:>9s}")

print()
if len(files) < 2:
    print("⚠ Chỉ có 1 mốc, chạy pipeline thêm vài mốc khác để so sánh.")
if problems:
    print("Vấn đề phát hiện:")
    for x in problems:
        print("  ❌", x)
else:
    print("Không phát hiện vấn đề về tổng tài nguyên, đơn tương lai hay mốc cập nhật feed.")
print("Tự kiểm thêm: các dòng phải KHÁC nhau và hợp lý theo giờ trong ngày.")