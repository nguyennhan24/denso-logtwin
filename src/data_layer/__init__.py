"""Data Layer (Phase 4, Trung) — biến 6 feed thô thành contract sạch.

Luồng: ingest (đọc + snapshot as_of) -> schemas (kiểm tra 3 tầng, quarantine)
-> mapper (journey) -> state_builder (CurrentLogisticsState) -> quality/pipeline.
Không module nào ở đây được đọc data/raw/*/_truth/.
"""
from .ingest import FEEDS, RawFeeds, load_config, load_day, parse_as_of, snapshot

__all__ = ["FEEDS", "RawFeeds", "load_config", "load_day", "parse_as_of", "snapshot"]
