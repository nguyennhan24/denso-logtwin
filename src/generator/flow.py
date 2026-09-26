"""flow.py — "Nhà máy thật" chạy trong máy: luồng Kho -> AMR -> cổng/Packer -> AGV -> Line.

Khác Twin v0.1 ở những điểm CỐ Ý (để Backtest có ý nghĩa):
  1. AMR bị GIỮ ở cổng tới khi có người nhận thùng (blocking) — đúng quan sát genba.
  2. Thời gian tách pack phụ thuộc số pack + có outlier.
  3. Có AMR hỏng, công nhân nghỉ, thời gian ngẫu nhiên, AMR/AGV phải chạy về.
Twin chỉ cần dự đoán được XU HƯỚNG của thế giới này, không cần giống 100%.
"""
from __future__ import annotations

import pandas as pd
import simpy

from .breakdown import amr_faults, pack_time, worker_breaks
from .world import World


def run_world(w: World, requests: pd.DataFrame) -> pd.DataFrame:
    """Chạy mô phỏng một ngày, trả về event log 'sự thật' (ground truth)."""
    env = simpy.Environment()
    pools = {
        "amr": simpy.FilterStore(env, capacity=len(w.amr_ids)),
        "packer": simpy.FilterStore(env, capacity=len(w.packer_slots)),
        "agv": simpy.FilterStore(env, capacity=len(w.agv_ids)),
    }
    pools["amr"].items = list(w.amr_ids)
    pools["packer"].items = list(w.packer_slots)
    pools["agv"].items = list(w.agv_ids)

    events: list[dict] = []

    def log(t, stage, event, unit=None, pick_no=None, **extra):
        events.append(
            {"t_min": float(t), "stage": stage, "event": event,
             "unit": unit, "pick_no": pick_no, **extra}
        )

    amr_faults(env, w, pools["amr"], log)
    worker_breaks(env, w, pools["packer"], log)
    for r in requests.itertuples(index=False):
        env.process(_request_flow(env, w, r, pools, log))

    env.run(until=w.horizon_min)
    return pd.DataFrame(events)


def _return_trip(env, pool, unit, duration, log, stage):
    yield env.timeout(duration)
    log(env.now, stage, "RETURNED", unit)
    yield pool.put(unit)


def _request_flow(env, w: World, r, pools, log):
    s = w.cfg["service"]
    yield env.timeout(r.arrival_min)
    log(env.now, "wms", "CREATED", None, r.pick_no)

    # --- AMR: chờ robot rảnh, chạy ra cổng ---
    amr = yield pools["amr"].get()
    log(env.now, "amr", "DISPATCH", amr, r.pick_no)
    yield env.timeout(w.draw(s["amr_travel_out"]))
    log(env.now, "amr", "ARRIVE_GATE", amr, r.pick_no)

    # --- Packer: AMR đứng CHỜ ở cổng tới khi có slot rảnh (blocking) ---
    slot = yield pools["packer"].get()
    worker = w.worker_at(slot, env.now)
    log(env.now, "packer", "START", slot, r.pick_no, worker=worker)
    log(env.now, "amr", "RELEASE", amr, r.pick_no)            # thùng đã được lấy khỏi AMR
    env.process(_return_trip(env, pools["amr"], amr, w.draw(s["amr_return"]), log, "amr"))

    t_pack, outlier = pack_time(w, r.packs)
    yield env.timeout(t_pack)
    log(env.now, "packer", "FINISH", slot, r.pick_no, worker=worker, outlier=outlier)
    yield pools["packer"].put(slot)

    # --- AGV: hộp mới chờ AGV rồi chở tới line ---
    agv = yield pools["agv"].get()
    log(env.now, "agv", "LOAD", agv, r.pick_no)
    yield env.timeout(w.draw(s["agv_travel"]))
    log(env.now, "line", "DELIVERED", agv, r.pick_no)
    env.process(_return_trip(env, pools["agv"], agv, w.draw(s["agv_return"]), log, "agv"))
