"""Kiểm tra MaterialRequest / CurrentLogisticsState theo spec v0.1 (mục 2, 3, 6).

Trung là chủ 2 contract này -> mọi thứ Trung xuất ra phải qua hàm này trước khi giao.
Dùng:  python contracts/validate_contracts.py contracts/mock_v0_1.json
Trả về danh sách lỗi (rỗng = hợp lệ).
"""
from __future__ import annotations

import json
import sys
from datetime import datetime

STAGES = ("amr", "packer", "agv")
FEEDS = ("wms", "amr", "agv", "production", "labor", "excel")


def check_request(r: dict, where: str, horizon_min: float | None = None) -> list[str]:
    errs = []
    for k in ("request_id", "origin_id", "destination_line_id", "material_id"):
        if not isinstance(r.get(k), str) or not r.get(k):
            errs.append(f"{where}: '{k}' phải là chuỗi không rỗng")
    a = r.get("arrival_min")
    if not isinstance(a, (int, float)) or isinstance(a, bool) or a < 0:
        errs.append(f"{where}: arrival_min phải là số >= 0")
    elif horizon_min is not None and a >= horizon_min:
        errs.append(f"{where}: arrival_min phải < horizon_min ({horizon_min})")
    q = r.get("quantity")
    if not isinstance(q, int) or isinstance(q, bool) or q <= 0:
        errs.append(f"{where}: quantity phải là số nguyên > 0")
    return errs


def check_state(s: dict) -> list[str]:
    errs = []
    try:
        as_of = datetime.fromisoformat(s["as_of"])
        if as_of.tzinfo is None:
            errs.append("as_of thiếu múi giờ")
    except (KeyError, ValueError):
        return ["as_of thiếu hoặc không phải ISO 8601"]

    res = s.get("resources", {})
    if set(res) != set(STAGES):
        errs.append(f"resources phải có đúng 3 key {STAGES}")

    active_count = {st: 0 for st in STAGES}
    for i, j in enumerate(s.get("active_jobs", [])):
        if j.get("stage") not in STAGES:
            errs.append(f"active_jobs[{i}]: stage không hợp lệ")
            continue
        active_count[j["stage"]] += 1
        rm = j.get("remaining_min")
        if not isinstance(rm, (int, float)) or rm <= 0:
            errs.append(f"active_jobs[{i}]: remaining_min phải > 0")
        errs += check_request(j.get("request", {}), f"active_jobs[{i}]")
        if j.get("request", {}).get("arrival_min") != 0:
            errs.append(f"active_jobs[{i}]: WIP phải có arrival_min = 0")

    for i, j in enumerate(s.get("queued_jobs", [])):
        if j.get("stage") not in STAGES:
            errs.append(f"queued_jobs[{i}]: stage không hợp lệ")
        errs += check_request(j.get("request", {}), f"queued_jobs[{i}]")
        if j.get("request", {}).get("arrival_min") != 0:
            errs.append(f"queued_jobs[{i}]: WIP phải có arrival_min = 0")

    for st in STAGES:
        r = res.get(st, {})
        t, b, o = r.get("total"), r.get("busy"), r.get("offline")
        if not all(isinstance(x, int) and x >= 0 for x in (t, b, o)):
            errs.append(f"resources.{st}: total/busy/offline phải là số nguyên >= 0")
            continue
        if b + o > t:
            errs.append(f"resources.{st}: busy + offline > total")
        if b != active_count[st]:
            errs.append(f"resources.{st}: busy={b} nhưng có {active_count[st]} active job")

    fu = s.get("feed_updated_at", {})
    if set(fu) != set(FEEDS):
        errs.append(f"feed_updated_at phải có đúng 6 key {FEEDS}")
    for k, v in fu.items():
        try:
            if datetime.fromisoformat(v) > as_of:
                errs.append(f"feed_updated_at.{k} vượt quá as_of")
        except ValueError:
            errs.append(f"feed_updated_at.{k} không phải ISO 8601")
    return errs


def check_bundle(doc: dict, horizon_min: float | None = None) -> list[str]:
    """Kiểm tra cả state + demand, gồm luật ID không trùng giữa WIP và demand."""
    errs = []
    state = doc.get("current_state")
    ids = []
    if state is not None:
        errs += check_state(state)
        ids += [j["request"]["request_id"] for j in state.get("queued_jobs", []) + state.get("active_jobs", [])]
    for i, r in enumerate(doc.get("demand", [])):
        errs += check_request(r, f"demand[{i}]", horizon_min)
        ids.append(r.get("request_id"))
    dup = {x for x in ids if ids.count(x) > 1}
    if dup:
        errs.append(f"request_id bị trùng: {sorted(dup)}")
    return errs


if __name__ == "__main__":
    path = sys.argv[1] if len(sys.argv) > 1 else "contracts/mock_v0_1.json"
    with open(path, encoding="utf-8") as f:
        problems = check_bundle(json.load(f))
    print("HỢP LỆ" if not problems else "\n".join(f"- {p}" for p in problems))
    sys.exit(1 if problems else 0)
