"""Bước 3 — mapper: chuẩn hóa ID, nối sự kiện các feed về 1 request_id, dựng bảng journey.

Chuỗi nối (xem docs/data_dictionary.md mục 3):
    wms.pick_no "PICK-00012"  = request_id chuẩn
    amr.wms_ref (dòng DISPATCH) -> amr.task_id -> các dòng ARRIVE_GATE/RELEASE/RETURNED
    agv.label "PK12", excel.pick 12   -> số phiếu -> "PICK-00012"
Dòng không nối được KHÔNG bỏ im lặng: vào quarantine với reason cụ thể.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .validate import QUARANTINE_COLUMNS, RESOURCE_EVENTS

# Bắt mọi biến thể Generator tạo ra (PICK-00012, PK12, 12) và vài kiểu bẩn hơn (pick-12, PK-12, " pk 012 ", 12.0)
_PICK_RE = re.compile(r"^(?:PICK|PK)?[-_]?0*(\d+)(?:\.0+)?$")
_TASK_RE = re.compile(r"^T[-_]?0*(\d+)$")

# Thứ tự khâu tại as_of (từ đầu tới cuối luồng)
STAGE_ORDER = ["amr_queued", "amr_active", "packer_queued", "packer_active", "agv_queued", "agv_active", "delivered"]


def _number(value, pattern: re.Pattern) -> int | None:
    if value is None or (not isinstance(value, str) and pd.isna(value)):
        return None
    if isinstance(value, (int, np.integer)):
        n = int(value)
    else:
        m = pattern.match(str(value).strip().upper().replace(" ", ""))
        if not m:
            return None
        n = int(m.group(1))
    return n if n > 0 else None


def normalize_request_id(value) -> str | None:
    """'PICK-00012' / 'PK12' / 12 / 'pick-12' -> 'PICK-00012'. Không hiểu được -> None."""
    n = _number(value, _PICK_RE)
    return None if n is None else f"PICK-{n:05d}"


def normalize_task_id(value) -> str | None:
    """'T-0007' / 't7' / 'T0007' -> 'T-0007'."""
    n = _number(value, _TASK_RE)
    return None if n is None else f"T-{n:04d}"


def code_maps(master: pd.DataFrame) -> dict[tuple[str, str], dict[str, str]]:
    """{(kind, system): {code: canonical_id}}, ví dụ ("line", "agv"): {"ST-A1": "L1"}."""
    out = {}
    for (kind, system), g in master.groupby(["kind", "system"]):
        out[(kind, system)] = dict(zip(g["code"].str.strip(), g["canonical_id"].str.strip()))
    return out


@dataclass
class LinkResult:
    linked: dict[str, pd.DataFrame]     # wms, amr, agv, excel đã có cột request_id
    quarantine: pd.DataFrame
    stats: dict


def _reject(df: pd.DataFrame, mask: pd.Series, feed: str, check: str, reason: str, sink: list) -> pd.DataFrame:
    """Đưa các dòng `mask` vào quarantine, trả về phần còn lại."""
    sink += [{"feed": feed, "row_ref": r, "check": check, "reason": reason} for r in df.loc[mask, "row_ref"]]
    return df[~mask]


def link_feeds(clean: dict[str, pd.DataFrame], master: pd.DataFrame, cfg: dict) -> LinkResult:
    maps = code_maps(master)
    q: list[dict] = []

    # --- WMS: gốc của mọi request ---
    wms = clean["wms"].copy()
    wms["request_id"] = wms["pick_no"].map(normalize_request_id)
    wms = _reject(wms, wms["request_id"].isna(), "wms", "link:pick_no_format", "bad_request_id", q)
    wms = _reject(wms, wms["request_id"].duplicated(), "wms", "link:unique_pick_no", "duplicate_pick_no", q)
    wms["line_id"] = wms["line_code"].str.strip().map(maps.get(("line", "wms"), {}))
    wms = _reject(wms, wms["line_id"].isna(), "wms", "link:master_line", "unknown_line_code", q)
    wms["material_id"] = wms["material_code"].str.strip().map(maps.get(("material", "wms"), {}))
    wms = _reject(wms, wms["material_id"].isna(), "wms", "link:master_material", "unknown_material_code", q)
    known = set(wms["request_id"])
    line_of = wms.set_index("request_id")["line_id"]

    # --- AMR: DISPATCH mang wms_ref; các sự kiện sau chỉ mang task_id ---
    amr = clean["amr"].copy()
    resource = amr[amr["event"].isin(RESOURCE_EVENTS)]        # FAULT/RECOVER: của robot, không cần nối
    task_ev = amr[~amr["event"].isin(RESOURCE_EVENTS)].copy()
    task_ev["task_id"] = task_ev["task_id"].map(normalize_task_id)
    task_ev = _reject(task_ev, task_ev["task_id"].isna(), "amr", "link:task_id_format", "bad_task_id", q)

    disp = task_ev[task_ev["event"] == "DISPATCH"].copy()
    disp["request_id"] = disp["wms_ref"].map(normalize_request_id)
    disp = _reject(disp, disp["request_id"].isna(), "amr", "link:wms_ref_format", "bad_request_id", q)
    disp = _reject(disp, ~disp["request_id"].isin(known), "amr", "link:wms_ref_in_wms", "ref_not_in_wms", q)
    disp = _reject(disp, disp["task_id"].duplicated(), "amr", "link:unique_task", "duplicate_task", q)
    disp = _reject(disp, disp["request_id"].duplicated(), "amr", "link:unique_dispatch", "duplicate_dispatch", q)
    task_to_request = disp.set_index("task_id")["request_id"]

    other = task_ev[task_ev["event"] != "DISPATCH"].copy()
    other["request_id"] = other["task_id"].map(task_to_request)
    other = _reject(other, other["request_id"].isna(), "amr", "link:task_dispatched", "task_not_in_wms", q)
    # Sắp lại theo thứ tự dòng gốc trước rồi mới theo ts: Generator ghi RETURNED rồi DISPATCH
    # CÙNG một giây, mất thứ tự này thì trạng thái robot sẽ sai (tưởng rảnh trong khi vừa nhận task)
    amr_linked = pd.concat([disp, other, resource]).sort_index().sort_values("ts", kind="stable")

    # --- AGV: label "PK12" ---
    agv = clean["agv"].copy()
    agv["request_id"] = agv["label"].map(normalize_request_id)
    agv = _reject(agv, agv["request_id"].isna(), "agv", "link:label_format", "bad_label", q)
    agv = _reject(agv, ~agv["request_id"].isin(known), "agv", "link:label_in_wms", "label_not_in_wms", q)
    agv = _reject(agv, agv["request_id"].duplicated(), "agv", "link:unique_label", "duplicate_label", q)
    agv["line_id"] = agv["station_code"].str.strip().map(maps.get(("line", "agv"), {}))
    agv = _reject(agv, agv["line_id"].isna(), "agv", "link:master_station", "unknown_station_code", q)
    agv = _reject(agv, agv["line_id"] != agv["request_id"].map(line_of), "agv", "link:station_matches_wms_line",
                  "station_line_mismatch", q)

    # --- Excel: số phiếu trần ---
    excel = clean["excel"].copy()
    excel["request_id"] = excel["pick"].map(normalize_request_id)
    excel = _reject(excel, excel["request_id"].isna(), "excel", "link:pick_format", "bad_pick", q)
    excel = _reject(excel, ~excel["request_id"].isin(known), "excel", "link:pick_in_wms", "pick_not_in_wms", q)
    excel = _reject(excel, excel["request_id"].duplicated(), "excel", "link:unique_pick", "duplicate_pick", q)

    # --- Tỉ lệ nối: trên các dòng cần nối (bỏ FAULT/RECOVER), đã qua validate ---
    totals = {
        "amr": int((~clean["amr"]["event"].isin(RESOURCE_EVENTS)).sum()),
        "agv": len(clean["agv"]),
        "excel": len(clean["excel"]),
    }
    linked_n = {"amr": len(disp) + len(other), "agv": len(agv), "excel": len(excel)}
    stats = {n: {"rows": totals[n], "linked": linked_n[n],
                 "rate": round(linked_n[n] / totals[n], 4) if totals[n] else None} for n in totals}
    all_rows, all_linked = sum(totals.values()), sum(linked_n.values())
    stats["overall"] = {"rows": all_rows, "linked": all_linked,
                        "rate": round(all_linked / all_rows, 4) if all_rows else None}
    stats["wms"] = {"rows": len(clean["wms"]), "valid_requests": len(wms)}

    return LinkResult(
        linked={"wms": wms, "amr": amr_linked, "agv": agv, "excel": excel},
        quarantine=pd.DataFrame(q, columns=QUARANTINE_COLUMNS),
        stats=stats,
    )


def _minutes(a: pd.Series, b: pd.Series) -> pd.Series:
    return (b - a).dt.total_seconds() / 60


def build_journey(linked: dict[str, pd.DataFrame], as_of, cfg: dict) -> pd.DataFrame:
    """Mỗi phiếu WMS 1 dòng: mốc thời gian từng khâu (chỉ dữ liệu đã biết tại as_of) + khâu hiện tại."""
    wms, amr, agv, excel = linked["wms"], linked["amr"], linked["agv"], linked["excel"]
    j = pd.DataFrame({
        "request_id": wms["request_id"],
        "origin_id": cfg["origin_id"],
        "line_id": wms["line_id"],
        "material_id": wms["material_id"],
        "quantity": wms["qty_packs"],
        "created_at": wms["created_at"],
        "due_at": wms["need_by"],
    }).reset_index(drop=True)

    def lookup(table: pd.DataFrame, col: str) -> pd.Series:
        """Lấy table[col] theo request_id của j. Dùng reindex thay vì map để giữ kiểu datetime khi bảng rỗng."""
        return table.set_index("request_id")[col].reindex(j["request_id"]).set_axis(j.index)

    def amr_event(name: str) -> pd.DataFrame:
        return amr[amr["event"] == name].drop_duplicates("request_id")

    disp = amr_event("DISPATCH")
    j["amr_task_id"] = lookup(disp, "task_id")
    j["amr_id"] = lookup(disp, "robot_id")
    j["amr_dispatch_at"] = lookup(disp, "ts")
    j["amr_arrive_gate_at"] = lookup(amr_event("ARRIVE_GATE"), "ts")
    # RELEASE = thùng được lấy khỏi AMR = packer bắt đầu (chính xác tới giây, tốt hơn excel.start)
    j["packer_start_at"] = lookup(amr_event("RELEASE"), "ts")
    j["packer_end_at"] = lookup(excel, "end")
    j["worker_id"] = lookup(excel, "worker")
    j["agv_job_id"] = lookup(agv, "job_id")
    j["agv_id"] = lookup(agv, "agv_id")
    j["agv_load_at"] = lookup(agv, "load_epoch")
    j["delivered_at"] = lookup(agv, "deliver_epoch")

    # Khâu hiện tại = mốc quan sát được xa nhất trong luồng (np.select lấy điều kiện đúng đầu tiên)
    j["current_stage"] = np.select(
        [j["delivered_at"].notna(), j["agv_load_at"].notna(), j["packer_end_at"].notna(),
         j["packer_start_at"].notna(), j["amr_arrive_gate_at"].notna(), j["amr_dispatch_at"].notna()],
        ["delivered", "agv_active", "agv_queued", "packer_active", "packer_queued", "amr_active"],
        default="amr_queued",
    )

    # Thời lượng (phút); trống nếu chưa có đủ 2 mốc
    j["amr_wait_min"] = _minutes(j["created_at"], j["amr_dispatch_at"])
    j["amr_travel_min"] = _minutes(j["amr_dispatch_at"], j["amr_arrive_gate_at"])
    j["gate_wait_min"] = _minutes(j["amr_arrive_gate_at"], j["packer_start_at"])
    j["pack_min"] = _minutes(j["packer_start_at"], j["packer_end_at"])
    j["agv_wait_min"] = _minutes(j["packer_end_at"], j["agv_load_at"])
    j["agv_travel_min"] = _minutes(j["agv_load_at"], j["delivered_at"])
    j["lead_time_min"] = _minutes(j["created_at"], j["delivered_at"])
    j["as_of"] = pd.Timestamp(as_of)
    return j.sort_values(["created_at", "request_id"], kind="stable").reset_index(drop=True)


def print_link_stats(stats: dict) -> None:
    o = stats["overall"]
    print(f"Tỉ lệ nối ID: {o['linked']}/{o['rows']} = {o['rate']:.2%}" if o["rate"] is not None
          else "Tỉ lệ nối ID: không có dòng cần nối")
    for n in ("amr", "agv", "excel"):
        s = stats[n]
        if s["rate"] is not None:
            print(f"  {n:6s}: {s['linked']}/{s['rows']} = {s['rate']:.2%}")
