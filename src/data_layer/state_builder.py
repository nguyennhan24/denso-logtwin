"""Bước 4 — state_builder: dựng trạng thái nhà máy tại as_of từ journey.

Nguyên tắc: state mô tả cái QUAN SÁT được. Ước lượng (remaining_min) chỉ là phần phụ, luôn kèm
is_estimated = true + estimate_method. Ước lượng = trung vị thời lượng khâu đó trong lịch sử trước as_of,
không mô phỏng hàng đợi (việc của Twin). Quá thời gian dự kiến mà chưa thấy sự kiện -> overdue = true.
"""
from __future__ import annotations

import pandas as pd

from . import STAGES
from .mapper import STAGE_ORDER

# Mỗi khâu đang chạy: (cột thời lượng trong journey để lấy trung vị, mô tả cho estimate_method)
DURATION_OF_STAGE = {
    "amr": ("amr_travel_min", "ARRIVE_GATE - DISPATCH"),
    "packer": ("pack_min", "excel.end - RELEASE"),
    "agv": ("agv_travel_min", "deliver - load"),
}
# Mốc bắt đầu của từng khâu/trạng thái -> dùng để tính thời gian đã ở khâu và thứ tự FIFO
ENTERED_AT = {
    "amr_queued": "created_at",
    "amr_active": "amr_dispatch_at",
    "packer_queued": "amr_arrive_gate_at",
    "packer_active": "packer_start_at",
    "agv_queued": "packer_end_at",
    "agv_active": "agv_load_at",
}
# Mốc KẾT THÚC của từng khâu đến từ feed nào. Feed loại manual (Excel nhập tay, trễ tới 2 giờ) -> job
# overdue ở khâu đó có thể đã xong mà chưa được ghi nhận (completion_unconfirmed).
STAGE_END_FEED = {"amr": "amr", "packer": "excel", "agv": "agv"}


def _minutes_until(start: pd.Series, as_of: pd.Timestamp) -> pd.Series:
    """Số phút từ `start` tới as_of."""
    return (as_of - start).dt.total_seconds() / 60


# ---------- 4b. Tài nguyên ----------
def amr_fleet_status(amr: pd.DataFrame, cfg: dict) -> dict[str, str]:
    """Trạng thái mỗi AMR = sự kiện gần nhất trước as_of; chưa có sự kiện -> idle."""
    status_of = cfg["amr_status_by_event"]
    robots = sorted(set(cfg["fleet"]["amr"]) | set(amr["robot_id"].dropna()))
    status = {r: "idle" for r in robots}
    last = amr.sort_values("ts", kind="stable").drop_duplicates("robot_id", keep="last")
    for robot, event in zip(last["robot_id"], last["event"]):
        status[robot] = status_of.get(event, "idle")
    return status


def amr_current_request(amr: pd.DataFrame) -> dict[str, str | None]:
    """request_id gắn với sự kiện gần nhất của mỗi AMR (cùng cách sắp như amr_fleet_status).
    Dòng FAULT/RECOVER không thuộc request nào -> None."""
    last = amr.sort_values("ts", kind="stable").drop_duplicates("robot_id", keep="last")
    req = last["request_id"] if "request_id" in last else pd.Series(None, index=last.index, dtype=object)
    return {r: (q if pd.notna(q) else None) for r, q in zip(last["robot_id"], req)}


def agv_fleet_status(agv: pd.DataFrame, cfg: dict) -> dict[str, str]:
    """AGV: job gần nhất đã LOAD mà chưa thấy deliver -> busy; còn lại idle.
    Feed AGV không có sự kiện hỏng và không có chuyến về -> không biết offline / đang chạy về."""
    robots = sorted(set(cfg["fleet"]["agv"]) | set(agv["agv_id"].dropna()))
    status = {r: "idle" for r in robots}
    last = agv.sort_values("load_epoch", kind="stable").drop_duplicates("agv_id", keep="last")
    for robot, delivered in zip(last["agv_id"], last["deliver_epoch"]):
        status[robot] = "idle" if pd.notna(delivered) else "busy"
    return status


def packer_workers(labor: pd.DataFrame, as_of: pd.Timestamp) -> dict:
    """Công nhân sẵn sàng = đang trong ca và không nghỉ (theo bảng phân ca KẾ HOẠCH)."""
    on_shift = (labor["shift_start"] <= as_of) & (as_of < labor["shift_end"])
    on_break = on_shift & (labor["break_start"] <= as_of) & (as_of < labor["break_end"])
    return {
        "slots": sorted(labor["slot"].unique()),
        "on_shift": sorted(labor.loc[on_shift, "worker_id"]),
        "on_break": sorted(labor.loc[on_break, "worker_id"]),
        "available": sorted(labor.loc[on_shift & ~on_break, "worker_id"]),
    }


# ---------- 4a. Ước lượng ----------
def stage_estimates(journey: pd.DataFrame, cfg: dict) -> dict[str, dict]:
    """Trung vị thời lượng mỗi khâu trên các đơn đã xong khâu đó trước as_of.
    journey đã cắt theo as_of nên mọi thời lượng có trong journey đều là lịch sử trước as_of."""
    est = cfg["estimate"]
    out = {}
    for stage, (col, formula) in DURATION_OF_STAGE.items():
        samples = journey[col].dropna()
        if len(samples) >= est["min_samples"]:
            m = float(samples.median())
            method = f"median({formula}) trên {len(samples)} lượt trước as_of = {m:.2f} phút, trừ thời gian đã chạy"
        else:
            m = float(est["fallback_min"][stage])
            method = (f"chỉ có {len(samples)} lượt trước as_of (< {est['min_samples']}) -> "
                      f"dùng mặc định config {m:.2f} phút, trừ thời gian đã chạy")
        out[stage] = {"median_min": m, "samples": int(len(samples)), "method": method}
    return out


# ---------- 4c. Đơn mở + order_status ----------
def build_order_status(journey: pd.DataFrame, as_of: pd.Timestamp, estimates: dict, cfg: dict,
                       n_packer_slots: int) -> pd.DataFrame:
    """Mỗi đơn mở 1 dòng. Đơn mở = created_at <= as_of và chưa thấy giao tại as_of."""
    j = journey[(journey["created_at"] <= as_of) & (journey["current_stage"] != "delivered")].copy()
    entered = pd.Series(pd.NaT, index=j.index, dtype=j["created_at"].dtype)
    for key, col in ENTERED_AT.items():
        entered = entered.mask(j["current_stage"] == key, j[col])

    # Mốc "đóng gói xong" chỉ có trong Excel (trễ tới 2 giờ). Thùng đóng xong mà đang chờ AGV
    # trông giống "đang đóng gói". Một slot chỉ đóng 1 thùng một lúc, nên nếu số đơn đang đóng gói
    # > số slot thì các đơn bắt đầu sớm nhất chắc chắn đã xong -> chuyển sang chờ AGV, gắn cờ ước lượng.
    j["stage_is_estimated"] = False
    packing = j[j["current_stage"] == "packer_active"].sort_values(["packer_start_at", "request_id"])
    extra = packing.index[: max(len(packing) - n_packer_slots, 0)]
    pack_median = estimates["packer"]["median_min"]
    j.loc[extra, "current_stage"] = "agv_queued"
    j.loc[extra, "stage_is_estimated"] = True
    guess_end = (j["packer_start_at"] + pd.Timedelta(minutes=pack_median)).dt.floor("s").clip(upper=as_of)
    entered = entered.mask(j["stage_is_estimated"], guess_end)

    j["stage"] = j["current_stage"].str.split("_").str[0]
    j["stage_status"] = j["current_stage"].str.split("_").str[1]
    j["stage_entered_at"] = entered

    j["wait_min"] = _minutes_until(j["created_at"], as_of)          # tổng thời gian đơn đã mở
    j["time_in_stage_min"] = _minutes_until(j["stage_entered_at"], as_of)

    # Ước lượng chỉ cho đơn đang được phục vụ (active); đơn đang chờ thì phụ thuộc hàng đợi -> để Twin
    active = j["stage_status"] == "active"
    median = j["stage"].map({s: e["median_min"] for s, e in estimates.items()})
    remaining = median - j["time_in_stage_min"]
    j["is_estimated"] = active | j["stage_is_estimated"]
    j["estimate_method"] = j["stage"].map({s: e["method"] for s, e in estimates.items()}).where(active, "")
    j.loc[j["stage_is_estimated"], "estimate_method"] = (
        f"khâu suy ra: số đơn đang đóng gói > {n_packer_slots} slot, đơn bắt đầu sớm nhất coi như đã đóng gói xong "
        f"và đang chờ AGV; stage_entered_at = RELEASE + trung vị đóng gói {pack_median:.2f} phút")
    j["overdue"] = active & (remaining <= 0)
    # Quá hạn dự kiến thì báo 0 + overdue, KHÔNG đoán thêm
    j["est_remaining_min"] = remaining.clip(lower=0).where(active)
    j["late"] = as_of > j["due_at"]
    j["priority"] = None        # WMS không có cột ưu tiên

    # Thứ tự FIFO quan sát được từ dữ liệu: theo mốc vào khâu, hòa thì theo request_id
    j["_stage_rank"] = j["current_stage"].map({s: i for i, s in enumerate(STAGE_ORDER)})
    j = j.sort_values(["_stage_rank", "stage_entered_at", "request_id"], kind="stable")
    queued = j["stage_status"] == "queued"
    j["queue_position"] = pd.array([pd.NA] * len(j), dtype="Int64")
    j.loc[queued, "queue_position"] = j[queued].groupby("stage").cumcount() + 1

    cols = ["request_id", "origin_id", "line_id", "material_id", "quantity", "priority", "created_at", "due_at",
            "current_stage", "stage", "stage_status", "stage_is_estimated", "stage_entered_at", "queue_position",
            "wait_min", "time_in_stage_min", "est_remaining_min", "is_estimated", "estimate_method",
            "overdue", "late", "amr_id", "agv_id", "worker_id"]
    return j[cols].reset_index(drop=True)


# ---------- 4d. CurrentLogisticsState ----------
def _request(row) -> dict:
    """MaterialRequest đúng contract. WIP có sẵn trong state dùng arrival_min = 0 (spec mục 2):
    as_of là phút 0 của Twin, WIP đã có mặt trước đó; thời điểm tạo thật nằm ở created_offset_min."""
    return {
        "request_id": row.request_id,
        "arrival_min": 0,
        "origin_id": row.origin_id,
        "destination_line_id": row.line_id,
        "material_id": row.material_id,
        "quantity": int(row.quantity),
    }


def _created_offset_min(row, as_of: pd.Timestamp) -> float:
    """Phút từ as_of tới lúc tạo đơn (âm = tạo trước as_of). Trường thêm, KHÔNG thay arrival_min."""
    return round((row.created_at - as_of).total_seconds() / 60, 2)


def build_state(order_status: pd.DataFrame, amr_status: dict, agv_status: dict, workers: dict,
                as_of: pd.Timestamp, cfg: dict, amr_requests: dict | None = None) -> dict:
    """CurrentLogisticsState theo contract v0.1 + vài trường thêm (xem docs/contract_change_proposal.md).
    feed_updated_at và source_freshness được freshness.attach_source_freshness() gắn vào sau."""
    os_ = order_status
    queued, active = os_[os_["stage_status"] == "queued"], os_[os_["stage_status"] == "active"]
    floor = float(cfg["estimate"]["overdue_remaining_min"])

    queued_jobs = [{"stage": r.stage, "request": _request(r), "created_offset_min": _created_offset_min(r, as_of)}
                   for r in queued.itertuples()]   # đã sắp FIFO
    active_jobs = []
    for r in active.itertuples():
        # est_remaining_min: ước lượng thật, quá hạn dự kiến -> 0 (định nghĩa trong docs/data_dictionary.md).
        # remaining_min (contract) đòi > 0: overdue dùng giá trị sàn; không overdue mà làm tròn ra 0 thì lấy 0.01.
        est = 0.0 if r.overdue else round(float(r.est_remaining_min), 2)
        remaining = floor if r.overdue else max(est, 0.01)
        end_is_manual = cfg["feeds"][STAGE_END_FEED[r.stage]]["type"] == "manual"
        active_jobs.append({
            "stage": r.stage, "remaining_min": remaining, "request": _request(r),
            "is_estimated": True, "estimate_method": r.estimate_method, "overdue": bool(r.overdue),
            "est_remaining_min": est,
            # Quá hạn mà mốc kết thúc khâu là nguồn nhập tay -> có thể đã xong nhưng chưa được ghi nhận
            "completion_unconfirmed": bool(r.overdue) and end_is_manual,
            "created_offset_min": _created_offset_min(r, as_of),
        })

    busy = {s: int((active["stage"] == s).sum()) for s in STAGES}
    n_slots = len(workers["slots"])
    # Slot không có người (nghỉ / ngoài ca) = offline. Kẹp để busy + offline <= total:
    # theo Generator, người chỉ đi nghỉ sau khi làm xong lượt đang làm.
    packer_offline = max(min(n_slots - len(workers["available"]), n_slots - busy["packer"]), 0)

    def group(status: dict, kinds: tuple) -> dict:
        return {k: sorted(r for r, s in status.items() if s == k) for k in kinds}

    # AMR có thêm nhóm blocked (đứng chờ ở cổng); AGV không có trạng thái này
    amr_groups = group(amr_status, ("busy", "blocked", "idle", "offline"))
    agv_groups = group(agv_status, ("busy", "idle", "offline"))
    amr_requests = amr_requests or {}
    # Xe blocked đang giữ thùng của đơn nào (request_id của sự kiện ARRIVE_GATE gần nhất; không nối được -> None)
    blocked_jobs = [{"amr_id": a, "request_id": amr_requests.get(a)} for a in amr_groups["blocked"]]

    def counts(groups: dict, total: int) -> dict:
        return {"total": total, **{k: len(v) for k, v in groups.items()}}
    return {
        "as_of": as_of.isoformat(),
        "resources": {
            "amr": {"total": len(amr_status), "busy": busy["amr"], "offline": len(amr_groups["offline"])},
            "packer": {"total": n_slots, "busy": busy["packer"], "offline": packer_offline},
            "agv": {"total": len(agv_status), "busy": busy["agv"], "offline": len(agv_groups["offline"])},
        },
        "queued_jobs": queued_jobs,
        "active_jobs": active_jobs,
        "feed_updated_at": {},
        # ---- trường thêm (không thuộc contract v0.1) ----
        "open_request_count": int(len(os_)),
        # Số đếm theo trạng thái VẬT LÝ, khớp với resource_detail: busy + blocked + idle + offline = total.
        # Không đặt blocked vào resources.* vì contract v0.1 / Twin chỉ nhận total, busy, offline.
        # Packer: không biết slot nào đang làm (Excel trễ) -> idle chỉ có số đếm, không có danh sách slot.
        "resource_counts": {
            "amr": counts(amr_groups, len(amr_status)),
            "packer": {"total": n_slots, "busy": busy["packer"], "idle": n_slots - busy["packer"] - packer_offline,
                       "offline": packer_offline},
            "agv": counts(agv_groups, len(agv_status)),
        },
        "resource_detail": {
            # Trạng thái VẬT LÝ từng xe. Khác resources.amr.busy (contract = số job đang ở khâu AMR):
            # AMR đang chạy về là busy, đứng chờ ở cổng là blocked; chỉ idle mới nhận được việc mới.
            # Không đặt vào resources.amr vì Twin dựng ResourceStatus(**resources["amr"]).
            "amr": {**amr_groups, "blocked_jobs": blocked_jobs},
            "agv": agv_groups,
            "packer": {"slots": workers["slots"], "workers_on_shift": workers["on_shift"],
                       "workers_on_break": workers["on_break"], "workers_available": workers["available"]},
        },
    }
