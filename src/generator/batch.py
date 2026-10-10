"""batch.py — Sinh hàng loạt raw nhiều seed cho cùng một ngày (ground truth cho Backtest).

    python -m src.generator.batch                       # ngày 1..21 x seeds trong config/generator.yaml
    python -m src.generator.batch --seeds 42 7 --days 20 21

Phase 8 (src/validation/phase8.py) đã tự sinh 1 seed cho mỗi ngày (seed + i) vào
data/results/phase8_data/raw/<ngày>/. Batch này KHÔNG làm lại việc đó; nó bổ sung phần còn thiếu:
NHIỀU seed cho CÙNG một ngày, ghi vào data/raw/<ngày>_seed<seed>/ (seed thật), không ghi đè nhau.

Quy tắc seed (để ngày 21 seed 42 giữ nguyên dữ liệu đang dùng ở Phase 4/6):
    ngày anchor (21)  -> seed = s
    ngày d khác       -> seed = s + 1000 * (anchor - d)
s phải trong [0, 1000) và d trong [first_day, anchor] thì các seed thật không bao giờ trùng nhau.
"""
from __future__ import annotations

import argparse
import sys
from datetime import date, timedelta
from pathlib import Path

import yaml

from . import generate_day, raw_dirname
from .world import load_config

SEED_STRIDE = 1000


def seed_for(day: int, base_seed: int, anchor_day: int = 21, first_day: int = 1) -> int:
    """Seed thật của rng cho ngày `day` thuộc chuỗi seed gốc `base_seed`."""
    if not 0 <= base_seed < SEED_STRIDE:
        raise ValueError(f"Seed gốc {base_seed} phải nằm trong [0, {SEED_STRIDE}) để không trùng seed ngày khác")
    if not first_day <= day <= anchor_day:
        raise ValueError(f"Ngày {day} phải nằm trong [{first_day}, {anchor_day}]")
    return base_seed + SEED_STRIDE * (anchor_day - day)


def day_to_date(day: int, base_date: str) -> str:
    """--day N -> ngày lịch, cùng quy ước với Data Layer (N = 1 là base_date)."""
    return (date.fromisoformat(base_date) + timedelta(days=day - 1)).isoformat()


def run_batch(days: list[int], seeds: list[int], *, base_date: str, anchor_day: int = 21,
              first_day: int = 1, out_dir: str | Path = "data/raw", config: dict | None = None,
              skip_existing: bool = False) -> list[dict]:
    """Sinh từng (ngày, seed gốc). Trả manifest: date, day, base_seed, seed, dir, skipped."""
    cfg = config if config is not None else load_config()
    # Kiểm hết trước khi ghi -> sai seed thì không để lại nửa batch
    jobs = [(d, s, seed_for(d, s, anchor_day, first_day)) for s in seeds for d in days]
    manifest = []
    for d, s, real_seed in jobs:
        day_iso = day_to_date(d, base_date)
        target = Path(out_dir) / raw_dirname(day_iso, real_seed)
        skipped = skip_existing and (target / "wms.csv").exists()
        if not skipped:
            generate_day(seed=real_seed, date=day_iso, config=cfg, out_dir=out_dir, seed_in_dirname=True)
        manifest.append({"day": d, "date": day_iso, "base_seed": s, "seed": real_seed,
                         "dir": str(target), "skipped": skipped})
    return manifest


def _parse_days(text: list[str]) -> list[int]:
    """'1-21' hoặc danh sách số."""
    out = []
    for t in text:
        if "-" in t:
            a, b = map(int, t.split("-"))
            out += list(range(a, b + 1))
        else:
            out.append(int(t))
    return out


def main(argv: list[str] | None = None) -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    p = argparse.ArgumentParser(description="Sinh raw nhiều seed cho Backtest")
    p.add_argument("--config", default="config/generator.yaml")
    p.add_argument("--data-layer-config", default="config/data_layer.yaml", help="lấy base_date")
    p.add_argument("--seeds", type=int, nargs="+", help="seed gốc; bỏ trống = batch.seeds trong config")
    p.add_argument("--days", nargs="+", help="vd 1-21 hoặc 20 21; bỏ trống = first_day..anchor_day")
    p.add_argument("--out", default="data/raw")
    p.add_argument("--skip-existing", action="store_true", help="bỏ qua thư mục đã có wms.csv")
    a = p.parse_args(argv)

    cfg = load_config(a.config)
    with open(a.data_layer_config, encoding="utf-8") as f:
        base_date = yaml.safe_load(f)["base_date"]
    b = cfg["batch"]
    days = _parse_days(a.days) if a.days else list(range(b["first_day"], b["anchor_day"] + 1))
    seeds = a.seeds or b["seeds"]
    try:
        manifest = run_batch(days, seeds, base_date=base_date, anchor_day=b["anchor_day"],
                             first_day=b["first_day"], out_dir=a.out, config=cfg,
                             skip_existing=a.skip_existing)
    except ValueError as e:
        sys.exit(f"Lỗi: {e}")
    made = sum(not m["skipped"] for m in manifest)
    print(f"Đã sinh {made}/{len(manifest)} thư mục vào {a.out}/<ngày>_seed<seed>/ "
          f"(seed gốc {seeds}, ngày {days[0]}..{days[-1]})")
    for m in manifest:
        if m["day"] == b["anchor_day"]:
            print(f"  ngày {m['day']} seed gốc {m['base_seed']} -> {m['dir']}")


if __name__ == "__main__":
    main()
