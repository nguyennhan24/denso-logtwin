"""state_builder.py — Dựng CurrentLogisticsState (contract v0.1) tại thời điểm as_of.

Đầu vào là feed đã snapshot + validate và bảng journey tại cùng as_of, nên mọi thứ ở đây
chỉ dựa trên dữ liệu đã biết (không nhìn tương lai).

Suy stage theo mốc cuối cùng đã biết của mỗi lượt giao (docs/phase4_design.md):
  created                         -> amr    queued
  amr_dispatch                    -> amr    active
  amr_arrive_gate                 -> packer queued   (AMR vẫn bận ở cổng — xem diagnostics)
  packer_start (RELEASE)          -> packer active   hoặc agv queued (ƯỚC LƯỢNG, xem dưới)
  packer_finish (Excel)           -> agv    queued
  agv_load                        -> agv    active
  delivered                       -> xong, không đưa vào state

Phần khó: giờ kết thúc đóng gói chỉ có trong Excel, mà Excel trễ tới 2 giờ và thiếu dòng.
Khi đã RELEASE mà Excel chưa có và AGV chưa load, ta ước lượng:
  kỳ vọng = setup + per_unit × quantity (học bằng trung vị từ các dòng Excel đã nhập)
  - chưa quá kỳ vọng -> packer active, remaining = kỳ vọng − đã trôi qua
  - đã quá kỳ vọng:
      có AGV rảnh  -> vẫn packer active (nếu xong rồi thì AGV rảnh đã nhận), remaining = min
      không AGV rảnh -> agv queued (đóng gói xong, đang chờ AGV)
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from .freshness import feed_updated_at
from .ingest import RawFeeds, hhmm_to_min, min_to_iso

STAGES = ("amr", "packer", "agv")
_STAGE_ORDER = {s: i for i, s in enumerate(STAGES)}


# ---------------------------------------------------------------- ước lượng thời gian
def _median_or_fallback(samples: pd.Series, fallback: float, min_n: int) -> dict:
    samples = samples.dropna()
    samples = samples[samples > 0]
    if len(samples) >= min_n:
        return {"value": float(samples.median()), "source": "data", "n_samples": int(len(samples))}
    return {"value": float(fallback), "source": "fallback", "n_samples": int(len(samples))}


def _fit_pack(journey: pd.DataFrame, fb: dict, min_n: int) -> dict:
    """Học setup + per_unit từ các lượt đã có cả RELEASE lẫn giờ kết thúc Excel.

    Excel ghi "HH:MM" (cắt xuống phút) nên cộng 0,5 phút để thời lượng không bị lệch thấp.
    Cách học bền với outlier (lượt tách pack lâu gấp 2-4 lần): lấy TRUNG VỊ thời lượng cho
    từng quantity, rồi hồi quy tuyến tính các trung vị đó (trọng số = số mẫu).
    Không đủ mẫu, chỉ có một giá trị quantity, hoặc kết quả vô lý (âm) -> dùng fallback.
    """
    done = journey.dropna(subset=["packer_start_min", "packer_finish_min"])
    dur = done["packer_finish_min"] + 0.5 - done["packer_start_min"]
    n = int(len(dur))
    fallback = {"setup": float(fb["pack_setup"]), "per_unit": float(fb["pack_per_unit"]),
                "source": "fallback", "n_samples": n}
    if n < min_n:
        return fallback
    g = pd.DataFrame({"q": done["quantity"].astype(float), "d": dur}).groupby("q")["d"]
    med, cnt = g.median(), g.size()
    if len(med) < 2:
        return fallback
    per_unit, setup = np.polyfit(med.index.to_numpy(), med.to_numpy(), 1, w=np.sqrt(cnt.to_numpy()))
    if per_unit <= 0 or setup < 0:
        return fallback
    return {"setup": float(setup), "per_unit": float(per_unit), "source": "data", "n_samples": n}


def learn_estimates(journey: pd.DataFrame, cfg: dict) -> dict:
    """Ước lượng thời gian từng công đoạn từ dữ liệu ĐÃ BIẾT (journey tại as_of).

    Trả dict: amr_travel, agv_travel (dạng {value, source, n_samples}), pack ({setup, per_unit,
    source, n_samples}) và agv_return (chỉ lấy từ config: feed AGV không có sự kiện "về tới").
    """
    fb, min_n = cfg["fallback_estimates"], int(cfg["estimate_min_samples"])
    return {
        "amr_travel": _median_or_fallback(
            journey["amr_arrive_gate_min"] - journey["amr_dispatch_min"], fb["amr_travel"], min_n),
        "agv_travel": _median_or_fallback(
            journey["delivered_min"] - journey["agv_load_min"], fb["agv_travel"], min_n),
        "pack": _fit_pack(journey, fb, min_n),
        "agv_return": {"value": float(fb["agv_return"]), "source": "config", "n_samples": 0},
    }


# ---------------------------------------------------------------- tài nguyên
def _amr_offline(snap: RawFeeds, as_of_min: float) -> list[str]:
    """Robot có FAULT mà chưa RECOVER tại as_of (sự kiện cuối cùng của robot là FAULT)."""
    fr = snap.amr[snap.amr["event"].isin(["FAULT", "RECOVER"]) & (snap.amr["ts_min"] <= as_of_min)]
    last = fr.sort_values("ts_min", kind="stable").groupby("robot_id")["event"].last()
    return sorted(last[last == "FAULT"].index.tolist())


def _agv_status(snap: RawFeeds, as_of_min: float, agv_return: float, total: int) -> dict:
    """Ước lượng AGV đang chở / đang chạy về / rảnh tại as_of.

    Đang chở: đã load, chưa giao. Đang chạy về: giao trong vòng agv_return phút gần đây và
    chưa load job mới. Rảnh = tổng − hai nhóm trên (không âm).
    """
    agv = snap.agv[snap.agv["load_min"] <= as_of_min]
    carrying = set(agv.loc[agv["deliver_min"].isna(), "agv_id"])
    last = agv.sort_values("load_min", kind="stable").groupby("agv_id").last()
    returning = set(last.index[(last["deliver_min"] > as_of_min - agv_return)
                               & (last["deliver_min"] <= as_of_min)]) - carrying
    return {"carrying": len(carrying), "returning": len(returning),
            "idle": max(total - len(carrying) - len(returning), 0)}


def _planned_breaks(snap: RawFeeds, as_of_min: float) -> int:
    """Số slot đóng gói đang nghỉ THEO KẾ HOẠCH (labor.csv). Chỉ ghi chẩn đoán, không vào state."""
    lab = snap.labor
    if not {"break_start", "break_end"} <= set(lab.columns):
        return 0
    b0 = hhmm_to_min(lab["break_start"], snap.day_start)
    b1 = hhmm_to_min(lab["break_end"], snap.day_start)
    return int(((b0 <= as_of_min) & (as_of_min < b1)).sum())


# ---------------------------------------------------------------- suy stage
def infer_wip(snap: RawFeeds, journey: pd.DataFrame, as_of_min: float, cfg: dict) -> tuple[pd.DataFrame, dict]:
    """Suy stage/trạng thái cho mọi lượt giao chưa xong tại as_of.

    Trả (wip, aux):
      wip: DataFrame[request_id, stage, status, entered_min, remaining_min, estimated, ...]
           entered_min = thời điểm vào stage hiện tại (dùng để xếp thứ tự hàng đợi).
      aux: số liệu phụ cho diagnostics (ước lượng, AGV rảnh, số lượt bị dời...).
    """
    est = learn_estimates(journey, cfg)
    min_rem = float(cfg["min_remaining_min"])
    total = cfg["resources_total"]
    agv_st = _agv_status(snap, as_of_min, est["agv_return"]["value"], int(total["agv"]))

    j = journey[journey["delivered_min"].isna()].copy()
    j["stage"], j["status"] = "amr", "queued"
    j["entered_min"], j["remaining_min"] = j["created_min"], np.nan
    j["estimated"], j["overdue"] = False, False

    def has(c: str) -> pd.Series:
        return j[c].notna()

    # Duyệt từ mốc sớm tới muộn; mốc muộn hơn ghi đè -> kết quả = "mốc cuối cùng đã biết".
    m = has("amr_dispatch_min")
    j.loc[m, ["stage", "status"]] = ["amr", "active"]
    j.loc[m, "entered_min"] = j.loc[m, "amr_dispatch_min"]
    j.loc[m, "remaining_min"] = est["amr_travel"]["value"] - (as_of_min - j.loc[m, "amr_dispatch_min"])

    m = has("amr_arrive_gate_min")
    j.loc[m, ["stage", "status"]] = ["packer", "queued"]
    j.loc[m, "entered_min"] = j.loc[m, "amr_arrive_gate_min"]

    # RELEASE mà chưa có Excel/AGV: phải ước lượng
    m = has("packer_start_min")
    expected = est["pack"]["setup"] + est["pack"]["per_unit"] * j["quantity"].astype(float)
    elapsed = as_of_min - j["packer_start_min"]
    j.loc[m, ["stage", "status"]] = ["packer", "active"]
    j.loc[m, "entered_min"] = j.loc[m, "packer_start_min"]
    j.loc[m, "remaining_min"] = (expected - elapsed)[m]
    j.loc[m, "estimated"] = True
    over = m & (elapsed >= expected)
    j.loc[over, "overdue"] = True
    if agv_st["idle"] == 0:
        # Không AGV rảnh -> lượt quá hạn nhiều khả năng đã đóng gói xong và đang chờ AGV
        j.loc[over, ["stage", "status"]] = ["agv", "queued"]
        j.loc[over, "entered_min"] = (j["packer_start_min"] + expected)[over]

    m = has("packer_finish_min")
    j.loc[m, ["stage", "status"]] = ["agv", "queued"]
    j.loc[m, "entered_min"] = j.loc[m, "packer_finish_min"]
    j.loc[m, ["estimated", "overdue"]] = False

    m = has("agv_load_min")
    j.loc[m, ["stage", "status"]] = ["agv", "active"]
    j.loc[m, "entered_min"] = j.loc[m, "agv_load_min"]
    j.loc[m, "remaining_min"] = est["agv_travel"]["value"] - (as_of_min - j.loc[m, "agv_load_min"])
    j.loc[m, ["estimated", "overdue"]] = False

    # Chặn vượt sức chứa: không thể có nhiều lượt đang đóng gói hơn số vị trí đóng gói.
    # Giữ các lượt bắt đầu MUỘN nhất (nhiều khả năng còn đang làm), dời phần còn lại sang
    # agv queued với giờ xong ước lượng (không muộn hơn as_of).
    pk = j[(j["stage"] == "packer") & (j["status"] == "active")]
    overflow = pk.sort_values(["packer_start_min", "request_id"], ascending=[False, True],
                              kind="stable").index[int(total["packer"]):]
    if len(overflow):
        j.loc[overflow, ["stage", "status"]] = ["agv", "queued"]
        j.loc[overflow, "entered_min"] = np.minimum((j["packer_start_min"] + expected)[overflow], as_of_min)

    active = j["status"] == "active"
    j.loc[active, "remaining_min"] = j.loc[active, "remaining_min"].clip(lower=min_rem)
    j.loc[~active, "remaining_min"] = np.nan

    est_pk = j["estimated"] & j["packer_finish_min"].isna() & j["agv_load_min"].isna()
    aux = {
        "estimates": est,
        "agv": agv_st,
        "n_estimated": int(est_pk.sum()),
        "n_overdue_kept_packer": int((est_pk & j["overdue"] & (j["stage"] == "packer")).sum()),
        "n_overdue_to_agv": int((est_pk & j["overdue"] & (j["stage"] == "agv")).sum()),
        "n_packer_overflow_moved": int(len(overflow)),
    }
    return j.reset_index(drop=True), aux


# ---------------------------------------------------------------- xuất contract
def _request(row, origin_id: str) -> dict:
    """Một dòng journey -> MaterialRequest. WIP luôn có arrival_min = 0 (spec mục 2)."""
    return {
        "request_id": str(row.request_id),
        "arrival_min": 0,
        "origin_id": origin_id,
        "destination_line_id": str(row.line_id),
        "material_id": str(row.material_id),
        "quantity": int(row.quantity),
    }


def _ordered(wip: pd.DataFrame, status: str) -> pd.DataFrame:
    """Lọc theo trạng thái; xếp theo stage, rồi thời điểm vào stage, trùng thì request_id."""
    w = wip[wip["status"] == status].copy()
    w["_s"] = w["stage"].map(_STAGE_ORDER)
    return w.sort_values(["_s", "entered_min", "request_id"], kind="stable")


def build_state(clean_snapshot: RawFeeds, journey: pd.DataFrame, as_of_min: float, cfg: dict) -> dict:
    """CurrentLogisticsState (dict, đúng contract v0.1) tại as_of.

    Nhận: feed sạch đã snapshot, journey cùng as_of, as_of (phút), config Data Layer.
    Trả:  dict {as_of, resources, queued_jobs, active_jobs, feed_updated_at}.
    busy mỗi stage = số active job của stage đó (đúng quy tắc contract).
    """
    wip, _ = infer_wip(clean_snapshot, journey, as_of_min, cfg)
    origin = cfg["origin_id"]
    queued = [{"stage": r.stage, "request": _request(r, origin)}
              for r in _ordered(wip, "queued").itertuples()]
    active = [{"stage": r.stage, "remaining_min": round(float(r.remaining_min), 2),
               "request": _request(r, origin)}
              for r in _ordered(wip, "active").itertuples()]

    total = cfg["resources_total"]
    busy = {s: sum(1 for a in active if a["stage"] == s) for s in STAGES}
    offline = {"amr": len(_amr_offline(clean_snapshot, as_of_min)), "packer": 0, "agv": 0}
    resources = {}
    for s in STAGES:
        t = int(total[s])
        # busy lấy từ active job (bắt buộc); offline bị chặn để busy + offline ≤ total
        resources[s] = {"total": t, "busy": busy[s], "offline": max(min(offline[s], t - busy[s]), 0)}

    return {
        "as_of": min_to_iso(as_of_min, clean_snapshot.day_start),
        "resources": resources,
        "queued_jobs": queued,
        "active_jobs": active,
        "feed_updated_at": feed_updated_at(clean_snapshot, as_of_min),
    }


def build_diagnostics(clean_snapshot: RawFeeds, journey: pd.DataFrame, as_of_min: float, cfg: dict) -> dict:
    """Số liệu chẩn đoán KHÔNG thuộc contract v0.1 (đề xuất cho v0.2).

    - amr_held_at_gate: AMR đã tới cổng nhưng chưa được nhận thùng -> vẫn bận (blocking).
    - amr_returning: AMR đã nhả thùng nhưng chưa về kho.
    Contract v0.1 chỉ đếm AMR "busy" = đang chạy ra cổng, nên hai nhóm này bị ẩn đi.
    """
    wip, aux = infer_wip(clean_snapshot, journey, as_of_min, cfg)
    held = journey[journey["amr_arrive_gate_min"].notna() & journey["packer_start_min"].isna()]
    ret = journey[journey["packer_start_min"].notna() & journey["amr_returned_min"].isna()]
    est = aux["estimates"]
    return {
        "as_of": min_to_iso(as_of_min, clean_snapshot.day_start),
        "note": "Chẩn đoán ngoài contract v0.1 — không dùng làm đầu vào Twin.",
        "amr_held_at_gate": {"count": int(len(held)),
                             "robots": sorted(held["robot_id"].dropna().astype(str).tolist())},
        "amr_returning": {"count": int(len(ret)),
                          "robots": sorted(ret["robot_id"].dropna().astype(str).tolist())},
        "amr_offline_robots": _amr_offline(clean_snapshot, as_of_min),
        "agv_status_est": aux["agv"],
        "packer_on_planned_break": _planned_breaks(clean_snapshot, as_of_min),
        "estimated_jobs": {
            "count": aux["n_estimated"],
            "overdue_kept_packer": aux["n_overdue_kept_packer"],
            "overdue_moved_to_agv": aux["n_overdue_to_agv"],
            "packer_overflow_moved": aux["n_packer_overflow_moved"],
        },
        "estimates": {
            "amr_travel_min": {**est["amr_travel"], "value": round(est["amr_travel"]["value"], 3)},
            "agv_travel_min": {**est["agv_travel"], "value": round(est["agv_travel"]["value"], 3)},
            "pack_setup_min": round(est["pack"]["setup"], 3),
            "pack_per_unit_min": round(est["pack"]["per_unit"], 3),
            "pack_source": est["pack"]["source"],
            "pack_n_samples": est["pack"]["n_samples"],
            "agv_return_min": est["agv_return"],
        },
        "wip_by_stage": {f"{s}_{st}": int(((wip["stage"] == s) & (wip["status"] == st)).sum())
                         for s in STAGES for st in ("queued", "active")},
    }
