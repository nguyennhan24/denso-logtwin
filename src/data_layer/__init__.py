"""Data Layer (Phase 4, Trung): 6 feed bẩn -> journey, order_status, CurrentLogisticsState.

7 bước: ingest -> validate -> mapper -> state_builder -> freshness -> báo cáo chất lượng -> contract_check.
Chạy:  python -m src.data_layer --day 21 --seed 42 --as-of 14:10
"""
from __future__ import annotations

from pathlib import Path

import yaml

FEEDS = ["wms", "amr", "agv", "production", "labor", "excel"]
STAGES = ["amr", "packer", "agv"]


def load_config(path: str | Path = "config/data_layer.yaml") -> dict:
    with open(path, encoding="utf-8") as f:
        return yaml.safe_load(f)


def resolve_raw_dir(raw_root: str | Path, day: str, seed: int) -> tuple[Path, bool]:
    """Thư mục raw của (ngày, seed). Trả (đường dẫn, legacy).

    Ưu tiên data/raw/<ngày>_seed<seed>/ (Generator CLI hiện ghi kiểu này). Nếu không có mà còn
    thư mục cũ data/raw/<ngày>/ thì dùng nó với legacy=True: không biết seed thật nên caller phải cảnh báo.
    """
    seeded = Path(raw_root) / f"{day}_seed{seed}"
    legacy = Path(raw_root) / day
    if not seeded.exists() and legacy.exists():
        return legacy, True
    return seeded, False
