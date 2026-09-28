"""mapper.py — Ghép ID của 4 hệ thống thành bảng hành trình (journey), mỗi lượt giao một dòng.

Chuỗi ghép (docs/feeds.md):
  wms.pick_no ← amr.wms_ref (chỉ dòng DISPATCH) → task_id → mọi sự kiện sau của task
  agv.label "PK12" → PICK-00012 ;  excel.pick 12 → PICK-00012
  mã line/vật tư của WMS → ID chuẩn qua master.csv

Lưu ý: một robot làm nhiều task trong ngày, nên KHÔNG ghép theo robot_id mà theo task_id —
task_id là khóa duy nhất nối các sự kiện của cùng một chuyến.
"""
from __future__ import annotations

import pandas as pd

from .ids import master_map, pick_no_from_label, pick_no_from_num
from .ingest import RawFeeds, snapshot

JOURNEY_COLS = [
    "request_id", "line_id", "material_id", "quantity", "need_by_min", "created_min",
    "amr_dispatch_min", "amr_arrive_gate_min", "packer_start_min", "packer_finish_min",
    "agv_load_min", "delivered_min",
    "robot_id", "task_id", "agv_id", "job_id", "worker",
    # Thêm cho chẩn đoán (AMR đang chạy về kho); không thuộc contract.
    "amr_returned_min",
]
_TIME_COLS = [c for c in JOURNEY_COLS if c.endswith("_min")]


def _amr_by_pick(amr: pd.DataFrame) -> pd.DataFrame:
    """Sự kiện AMR -> một dòng mỗi pick_no: robot, task và 4 mốc thời gian của chuyến."""
    disp = amr[amr["event"] == "DISPATCH"]
    task_to_pick = dict(zip(disp["task_id"], disp["wms_ref"]))
    ev = amr[amr["task_id"].isin(task_to_pick)]
    # Mỗi (task, event) lấy mốc sớm nhất: dòng lặp do hệ thống gửi lại không làm lệch kết quả.
    piv = ev.pivot_table(index="task_id", columns="event", values="ts_min", aggfunc="min")
    piv = piv.reindex(columns=["DISPATCH", "ARRIVE_GATE", "RELEASE", "RETURNED"])
    out = pd.DataFrame({
        "request_id": piv.index.map(task_to_pick),
        "task_id": piv.index,
        "amr_dispatch_min": piv["DISPATCH"].to_numpy(),
        "amr_arrive_gate_min": piv["ARRIVE_GATE"].to_numpy(),
        "packer_start_min": piv["RELEASE"].to_numpy(),      # RELEASE = lúc packer nhận thùng
        "amr_returned_min": piv["RETURNED"].to_numpy(),
    })
    out["robot_id"] = out["task_id"].map(dict(zip(disp["task_id"], disp["robot_id"])))
    return out.drop_duplicates("request_id")


def build_journey(clean: RawFeeds, as_of_min: float) -> pd.DataFrame:
    """Bảng journey tại as_of: mỗi request_id (= pick_no) một dòng, mốc chưa biết để trống.

    Nhận: feed đã qua validate_feeds và thời điểm as_of (phút).
    Trả:  DataFrame cột JOURNEY_COLS, sắp theo created_min rồi request_id.
    Gọi lại snapshot() bên trong để hàm vẫn đúng kể cả khi lỡ đưa vào feed đầy đủ cả ngày.
    packer_finish_min lấy từ Excel (độ phân giải phút); Excel chưa nhập thì để trống,
    KHÔNG tự điền — việc ước lượng là của state_builder và phải được ghi rõ là ước lượng.
    """
    s = snapshot(clean, as_of_min)
    lines = master_map(s.master, "line", "wms")
    mats = master_map(s.master, "material", "wms")

    j = pd.DataFrame({
        "request_id": s.wms["pick_no"],
        "line_id": s.wms["line_code"].map(lines),
        "material_id": s.wms["material_code"].map(mats),
        "quantity": s.wms["qty_packs"].astype("Int64"),
        "need_by_min": s.wms["need_by_min"],
        "created_min": s.wms["created_min"],
    })

    j = j.merge(_amr_by_pick(s.amr), on="request_id", how="left")

    agv = pd.DataFrame({
        "request_id": pick_no_from_label(s.agv["label"]),
        "agv_load_min": s.agv["load_min"], "delivered_min": s.agv["deliver_min"],
        "agv_id": s.agv["agv_id"], "job_id": s.agv["job_id"],
    }).drop_duplicates("request_id")
    j = j.merge(agv, on="request_id", how="left")

    ex = pd.DataFrame({
        "request_id": pick_no_from_num(s.excel["pick"]),
        "packer_finish_min": s.excel["end_min"], "worker": s.excel["worker"],
        "_entered": s.excel["entered_min"],
    }).sort_values("_entered", kind="stable").drop_duplicates("request_id").drop(columns="_entered")
    j = j.merge(ex, on="request_id", how="left")

    for c in _TIME_COLS:
        j[c] = j[c].astype("float64")
    for c in ("robot_id", "task_id", "agv_id", "job_id", "worker", "line_id", "material_id"):
        j[c] = j[c].astype("string")
    return (j[JOURNEY_COLS]
            .sort_values(["created_min", "request_id"], kind="stable")
            .reset_index(drop=True))


def match_stats(clean: RawFeeds, journey: pd.DataFrame) -> dict:
    """Tỷ lệ ghép ID của từng hệ thống về phiếu WMS trong journey.

    Nhận: feed sạch (cùng snapshot đã dựng journey) và bảng journey.
    Trả:  dict {amr|agv|excel: {total, matched, rate, unmatched: [...]}}.
          amr đếm theo task (dòng DISPATCH); agv theo job; excel theo dòng nhập tay.
          rate = None khi feed chưa có dòng nào (ví dụ Excel trước lần nhập đầu tiên).
    """
    ids = set(journey["request_id"])

    def one(keys: pd.Series, picks: pd.Series) -> dict:
        ok = picks.isin(ids)
        total = int(len(keys))
        return {
            "total": total,
            "matched": int(ok.sum()),
            "rate": round(float(ok.mean()), 4) if total else None,
            "unmatched": sorted(map(str, keys[~ok].tolist())),
        }

    disp = clean.amr[clean.amr["event"] == "DISPATCH"]
    return {
        "amr": one(disp["task_id"], disp["wms_ref"]),
        "agv": one(clean.agv["job_id"], pick_no_from_label(clean.agv["label"])),
        "excel": one(clean.excel["pick"], pick_no_from_num(clean.excel["pick"])),
    }
