"""feeds.py — Chiếu "sự thật" thành 6 feed như các hệ thống thật trong nhà máy.

Cố ý làm "bẩn" giống ngoài đời (Data Layer ở Phase 4 phải xử lý):
  - Mỗi hệ thống một bộ mã: WMS dùng LINE_A/MAT-01, AGV dùng ST-A1, chuẩn là L1/M01.
  - Mỗi hệ thống một cách ghi ID lượt giao: PICK-00012 / task T-0007 / nhãn PK12 / số 12.
  - Mỗi hệ thống một định dạng giờ: ISO, epoch giây, "HH:MM".
  - Excel nhập tay: trễ theo chu kỳ, thiếu dòng.
Ngoài 6 feed còn xuất:
  - master.csv: bảng mapping mã (dữ liệu tham chiếu tĩnh, không phải feed).
  - _truth/: đáp án cho test & Backtest. Data Layer TUYỆT ĐỐI KHÔNG đọc thư mục này.
"""
from __future__ import annotations

import math

import pandas as pd

from .world import World


def _pick_num(pick_no: str) -> int:
    return int(pick_no.split("-")[1])


def build_feeds(w: World, plan: pd.DataFrame, requests: pd.DataFrame, events: pd.DataFrame) -> dict:
    cfg = w.cfg
    line_wms = {k: v["wms_code"] for k, v in cfg["lines"].items()}
    line_st = {k: v["agv_station"] for k, v in cfg["lines"].items()}
    mat_wms = {k: v["wms_code"] for k, v in cfg["materials"].items()}
    ev = events.sort_values("t_min", kind="stable")

    # 1) WMS — phiếu xuất kho
    wms = pd.DataFrame({
        "pick_no": requests["pick_no"],
        "created_at": requests["arrival_min"].map(w.iso),
        "material_code": requests["material_id"].map(mat_wms),
        "line_code": requests["line_id"].map(line_wms),
        "qty_packs": requests["packs"],
        "need_by": requests["need_by_min"].map(w.iso),
    })

    # 2) AMR — luồng sự kiện của fleet manager (stream). Task ID riêng, chỉ DISPATCH có wms_ref.
    amr_rows, task_of_robot, task_of_pick, n_task = [], {}, {}, 0
    for e in ev[ev["stage"] == "amr"].itertuples():
        if e.event == "DISPATCH":
            n_task += 1
            task = f"T-{n_task:04d}"
            task_of_robot[e.unit] = task
            task_of_pick[e.pick_no] = task
        task = None if e.event in ("FAULT", "RECOVER") else task_of_robot.get(e.unit)
        amr_rows.append({
            "ts": w.iso(e.t_min), "robot_id": e.unit, "task_id": task, "event": e.event,
            "wms_ref": e.pick_no if e.event == "DISPATCH" else None,
        })
    amr = pd.DataFrame(amr_rows)

    # 3) AGV — mỗi job một dòng, giờ dạng epoch giây, lượt giao chỉ có "nhãn" PK<số>
    loads = ev[(ev["stage"] == "agv") & (ev["event"] == "LOAD")]
    delivered = ev[ev["event"] == "DELIVERED"].set_index("pick_no")["t_min"]
    line_of_pick = requests.set_index("pick_no")["line_id"]
    agv_rows = []
    for i, e in enumerate(loads.itertuples(), start=1):
        d = delivered.get(e.pick_no)
        agv_rows.append({
            "job_id": f"J-{i:04d}", "agv_id": e.unit,
            "label": f"PK{_pick_num(e.pick_no)}",
            "station_code": line_st[line_of_pick[e.pick_no]],
            "load_epoch": int(w.ts(e.t_min).timestamp()),
            "deliver_epoch": None if d is None else int(w.ts(d).timestamp()),
        })
    agv = pd.DataFrame(agv_rows).astype({"deliver_epoch": "Int64"})

    # 4) Production — kế hoạch theo giờ
    production = pd.DataFrame({
        "line_code": plan["line_id"].map(line_wms),
        "hour_start": (plan["hour_idx"] * 60).map(w.iso),
        "product": plan["product"],
        "planned_qty": plan["planned_qty"],
    })

    # 5) Labor — bảng phân ca (KẾ HOẠCH nghỉ; thực tế có thể lệch, xem _truth)
    lab = cfg["labor"]
    labor_rows = []
    for shift in lab["shifts"]:
        s0 = w.clock_to_min(shift["start"])
        for k, slot in enumerate(w.packer_slots):
            b0 = s0 + lab["break_offsets_min"][k % len(lab["break_offsets_min"])]
            labor_rows.append({
                "date": w.date, "worker_id": w.worker_at(slot, s0), "slot": slot,
                "shift": shift["name"], "shift_start": shift["start"], "shift_end": shift["end"],
                "break_start": w.ts(b0).strftime("%H:%M"),
                "break_end": w.ts(b0 + lab["break_min"]).strftime("%H:%M"),
            })
    labor = pd.DataFrame(labor_rows)

    # 6) Excel — log đóng gói do tổ trưởng nhập tay: giờ "HH:MM", số phiếu trần, nhập trễ, thiếu dòng
    ex = cfg["excel"]
    starts = ev[(ev["stage"] == "packer") & (ev["event"] == "START")].set_index("pick_no")["t_min"]
    excel_rows = []
    for e in ev[(ev["stage"] == "packer") & (ev["event"] == "FINISH")].itertuples():
        if w.rng.random() < ex["missing_row_prob"]:
            continue
        entered = math.ceil(e.t_min / ex["entry_every_min"]) * ex["entry_every_min"]
        excel_rows.append({
            "worker": e.worker, "pick": _pick_num(e.pick_no),
            "start": w.ts(starts[e.pick_no]).strftime("%H:%M"),
            "end": w.ts(e.t_min).strftime("%H:%M"),
            "entered_at": w.iso(entered),
        })
    excel = pd.DataFrame(excel_rows)

    # Master mapping (tham chiếu)
    master = pd.DataFrame(
        [{"kind": "line", "system": "wms", "code": v, "canonical_id": k} for k, v in line_wms.items()]
        + [{"kind": "line", "system": "agv", "code": v, "canonical_id": k} for k, v in line_st.items()]
        + [{"kind": "material", "system": "wms", "code": v, "canonical_id": k} for k, v in mat_wms.items()]
    )

    # Ground truth: mốc thời gian từng stage cho mỗi lượt
    piv = (ev.dropna(subset=["pick_no"])
             .assign(key=lambda d: d["stage"] + "_" + d["event"])
             .pivot_table(index="pick_no", columns="key", values="t_min", aggfunc="first"))
    truth_requests = requests.merge(piv, left_on="pick_no", right_index=True, how="left")
    truth_requests["amr_task_id"] = truth_requests["pick_no"].map(task_of_pick)

    return {
        "wms": wms, "amr": amr, "agv": agv, "production": production,
        "labor": labor, "excel": excel, "master": master,
        "_truth/requests": truth_requests, "_truth/events": events,
    }
