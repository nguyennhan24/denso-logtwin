"""world.py — Khung "nhà máy giả": đọc config, dựng tài nguyên, quy đổi thời gian.

Quy ước thời gian: bên trong Generator dùng PHÚT tính từ đầu ngày (t=0 lúc day_start).
Chỉ khi xuất feed mới đổi sang timestamp thật (ISO 8601 có múi giờ) — giống contract.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import yaml


def load_config(path: str | Path = "config/generator.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class World:
    cfg: dict
    date: str                    # "2026-10-01"
    seed: int
    rng: np.random.Generator     # MỌI số ngẫu nhiên phải đi qua rng này -> tái lập được
    start: datetime              # thời điểm t=0
    horizon_min: float           # độ dài ngày (phút)

    # ---- tài nguyên: danh sách ID từng đơn vị ----
    @property
    def amr_ids(self) -> list[str]:
        return [f"AMR-{i:02d}" for i in range(1, self.cfg["resources"]["amr"] + 1)]

    @property
    def packer_slots(self) -> list[str]:
        # "slot" = vị trí đóng gói tại cổng; người đứng slot thay đổi theo ca
        return [f"PK-{i}" for i in range(1, self.cfg["resources"]["worker"] + 1)]

    @property
    def agv_ids(self) -> list[str]:
        return [f"AGV-{i:02d}" for i in range(1, self.cfg["resources"]["agv"] + 1)]

    def worker_at(self, slot: str, t_min: float) -> str:
        """Ai đang đứng slot này tại thời điểm t (ca 1: W01.., ca 2: W04..)."""
        k = int(slot.split("-")[1])
        n = self.cfg["resources"]["worker"]
        shift_idx = self.shift_index(t_min)
        return f"W{k + shift_idx * n:02d}"

    def shift_index(self, t_min: float) -> int:
        for i, s in enumerate(self.cfg["labor"]["shifts"]):
            if self.clock_to_min(s["start"]) <= t_min < self.clock_to_min(s["end"]):
                return i
        return len(self.cfg["labor"]["shifts"]) - 1

    # ---- thời gian ----
    def clock_to_min(self, hhmm: str) -> float:
        """'14:00' -> số phút kể từ day_start."""
        h, m = map(int, hhmm.split(":"))
        h0, m0 = map(int, self.cfg["day_start"].split(":"))
        return (h * 60 + m) - (h0 * 60 + m0)

    def ts(self, t_min: float) -> datetime:
        return self.start + timedelta(minutes=float(t_min))

    def iso(self, t_min: float) -> str:
        return self.ts(t_min).isoformat(timespec="seconds")

    # ---- ngẫu nhiên ----
    def draw(self, spec: dict) -> float:
        """Lấy 1 thời gian > 0 từ lognormal có trung bình = mean, độ dao động = cv."""
        mean, cv = spec["mean"], spec["cv"]
        sigma = np.sqrt(np.log(1 + cv**2))
        mu = np.log(mean) - sigma**2 / 2
        return float(self.rng.lognormal(mu, sigma))


def build_world(cfg: dict, date: str, seed: int) -> World:
    tz = timezone(timedelta(hours=cfg["tz_offset_hours"]))
    h, m = map(int, cfg["day_start"].split(":"))
    start = datetime.fromisoformat(date).replace(hour=h, minute=m, tzinfo=tz)
    return World(
        cfg=cfg,
        date=date,
        seed=seed,
        rng=np.random.default_rng(seed),
        start=start,
        horizon_min=cfg["day_hours"] * 60,
    )
