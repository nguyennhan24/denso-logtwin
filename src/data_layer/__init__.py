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
