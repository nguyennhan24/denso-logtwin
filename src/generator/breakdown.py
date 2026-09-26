"""breakdown.py — Các "sự cố" làm thế giới giả khó và giống thật hơn Twin.

- AMR hỏng: một robot bị rút khỏi đội trong thời gian sửa.
- Công nhân nghỉ giải lao: một slot đóng gói tạm không có người.
- Outlier: thỉnh thoảng một lượt tách pack lâu gấp 2-4 lần.

Kỹ thuật: tài nguyên là simpy.FilterStore chứa ID từng đơn vị.
"Chiếm" một đơn vị = get() nó ra; trả lại = put() vào. Sự cố cũng chỉ là
một tiến trình chiếm đơn vị đó -> hệ thống tự thiếu năng lực, không cần code đặc biệt.
"""
from __future__ import annotations

import simpy

from .world import World


def amr_faults(env: simpy.Environment, w: World, amr_pool: simpy.FilterStore, log) -> None:
    """Lên lịch các lần AMR hỏng trong ngày (số lần ~ Poisson)."""
    b = w.cfg["breakdown"]
    n = w.rng.poisson(b["amr_fault_per_hour"] * w.horizon_min / 60)
    times = sorted(w.rng.uniform(0, w.horizon_min, size=n))
    repairs = [w.draw(b["amr_repair"]) for _ in times]   # rút số NGAY để thứ tự rng cố định
    for t, rep in zip(times, repairs):
        env.process(_one_fault(env, amr_pool, float(t), rep, log))


def _one_fault(env, amr_pool, t, repair, log):
    yield env.timeout(t)
    # TODO(Trung, nâng cấp): hiện robot chỉ hỏng khi đang rảnh; thực tế có thể hỏng giữa task.
    amr = yield amr_pool.get()
    log(env.now, "amr", "FAULT", amr)
    yield env.timeout(repair)
    log(env.now, "amr", "RECOVER", amr)
    yield amr_pool.put(amr)


def worker_breaks(env: simpy.Environment, w: World, packer_pool: simpy.FilterStore, log) -> None:
    """Mỗi ca, mỗi slot nghỉ 1 lần, lệch giờ nhau để không nghỉ cùng lúc."""
    lab = w.cfg["labor"]
    offsets = lab["break_offsets_min"]
    for shift in lab["shifts"]:
        s0 = w.clock_to_min(shift["start"])
        for k, slot in enumerate(w.packer_slots):
            t = s0 + offsets[k % len(offsets)]
            if t < w.horizon_min:
                env.process(_one_break(env, w, packer_pool, slot, t, lab["break_min"], log))


def _one_break(env, w, packer_pool, slot, t, dur, log):
    yield env.timeout(t)
    # Chờ đúng slot này làm xong lượt hiện tại rồi mới nghỉ (FilterStore lọc theo ID)
    yield packer_pool.get(lambda x: x == slot)
    who = w.worker_at(slot, env.now)
    log(env.now, "packer", "BREAK_START", slot, worker=who)
    yield env.timeout(dur)
    log(env.now, "packer", "BREAK_END", slot, worker=who)
    yield packer_pool.put(slot)


def pack_time(w: World, packs: int) -> tuple[float, bool]:
    """Thời gian tách pack = setup + từng pack; có xác suất thành outlier."""
    s = w.cfg["service"]
    b = w.cfg["breakdown"]
    t = w.draw(s["pack_setup"]) + sum(w.draw(s["pack_per_pack"]) for _ in range(packs))
    is_outlier = bool(w.rng.random() < b["pack_outlier_prob"])
    if is_outlier:
        f = b["pack_outlier_factor"]
        t *= float(w.rng.uniform(f["min"], f["max"]))
    return t, is_outlier
