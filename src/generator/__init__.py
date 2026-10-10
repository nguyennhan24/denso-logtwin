"""World Generator — Gate Phase 1: generate_day(seed=42) ra đủ 1 ngày dữ liệu.

Dùng:
    from src.generator import generate_day
    out = generate_day(seed=42, date="2026-10-01", out_dir="data/raw")

Hoặc từ terminal:
    python -m src.generator --seed 42 --date 2026-10-01 --days 3

Thư mục raw: CLI ghi vào data/raw/<ngày>_seed<seed>/ (seed thật của rng ngày đó) để đổi seed
không ghi đè. Gọi generate_day() trực tiếp vẫn ghi <ngày>/ như cũ (seed_in_dirname=False)
vì src/validation/phase8.py tự sinh và tự đọc theo layout cũ.
"""
from __future__ import annotations

from datetime import date as _date, timedelta
from pathlib import Path

import pandas as pd

from .demand import make_plan, make_requests
from .feeds import build_feeds
from .flow import run_world
from .world import build_world, load_config

FEED_NAMES = ["wms", "amr", "agv", "production", "labor", "excel"]


def generate_day(
    seed: int = 42,
    date: str = "2026-10-01",
    config_path: str | Path = "config/generator.yaml",
    out_dir: str | Path | None = "data/raw",
    config: dict | None = None,
    seed_in_dirname: bool = False,
) -> dict[str, pd.DataFrame]:
    """Sinh 1 ngày. Cùng (config, date, seed) -> cùng kết quả từng byte.

    seed_in_dirname=True -> ghi vào <out_dir>/<date>_seed<seed>/ (xem raw_dirname).
    """
    cfg = config if config is not None else load_config(config_path)
    w = build_world(cfg, date, seed)

    plan = make_plan(w)
    requests = make_requests(w, plan)
    events = run_world(w, requests)
    feeds = build_feeds(w, plan, requests, events)

    if out_dir is not None:
        day_dir = Path(out_dir) / raw_dirname(date, seed if seed_in_dirname else None)
        for name, df in feeds.items():
            path = day_dir / f"{name}.csv"
            path.parent.mkdir(parents=True, exist_ok=True)
            df.to_csv(path, index=False)
    return feeds


def raw_dirname(date: str, seed: int | None = None) -> str:
    """Tên thư mục raw của 1 ngày: '<date>_seed<seed>', hoặc '<date>' (layout cũ) khi seed=None."""
    return date if seed is None else f"{date}_seed{seed}"


def generate_days(start: str, n_days: int, base_seed: int = 42, **kw) -> None:
    """Nhiều ngày liên tiếp (ngày 1-20 cho calibrate). Seed mỗi ngày khác nhau nhưng cố định."""
    d0 = _date.fromisoformat(start)
    for i in range(n_days):
        generate_day(seed=base_seed + i, date=(d0 + timedelta(days=i)).isoformat(), **kw)
