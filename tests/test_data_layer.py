"""Test cho Phase 4 — Unified Data Layer.

Truth (_truth/) CHỈ được đọc trong file test này để đo độ chính xác; src/data_layer/ không đọc.
Chạy: pytest -q
"""
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_layer.ingest import FEEDS, load_config, load_day, parse_as_of, snapshot  # noqa: E402
from src.generator import generate_day  # noqa: E402
from src.generator.world import load_config as load_gen_config  # noqa: E402

DATE = "2026-10-01"
DL_CFG = load_config(ROOT / "config" / "data_layer.yaml")
GEN_CFG = load_gen_config(ROOT / "config" / "generator.yaml")
TIME_COLS = {
    "wms": ["created_min", "need_by_min"],
    "amr": ["ts_min"],
    "agv": ["load_min", "deliver_min"],
    "production": ["hour_start_min"],
    "excel": ["start_min", "end_min", "entered_min"],
}


@pytest.fixture(scope="module")
def day_dir(tmp_path_factory):
    """Sinh 1 ngày seed 42 vào thư mục tạm (gồm cả _truth/ để test so sánh)."""
    out = tmp_path_factory.mktemp("raw")
    generate_day(seed=42, date=DATE, config=GEN_CFG, out_dir=out)
    return out / DATE


@pytest.fixture(scope="module")
def raw(day_dir):
    return load_day(day_dir, DL_CFG)


@pytest.fixture(scope="module")
def truth(day_dir):
    return pd.read_csv(day_dir / "_truth" / "requests.csv")


def at(hhmm: str) -> float:
    return parse_as_of(hhmm, DATE, DL_CFG)


# ======================================================== Bước 1: ingest + snapshot
def test_load_day_reads_six_feeds_without_truth(raw, day_dir, monkeypatch):
    for name in FEEDS:
        assert len(getattr(raw, name)) > 0, name
    assert len(raw.master) > 0
    # Không cột nào mang dấu vết truth
    for name, df in raw.feeds().items():
        assert not any("truth" in c.lower() for c in df.columns), name

    # Chặn mọi lần đọc file trong _truth/: load_day vẫn phải chạy được
    real_read_csv = pd.read_csv

    def guarded(path, *a, **k):
        assert "_truth" not in str(path), f"Data Layer đọc truth: {path}"
        return real_read_csv(path, *a, **k)

    monkeypatch.setattr(pd, "read_csv", guarded)
    load_day(day_dir, DL_CFG)


def test_t_min_non_negative_and_ordered(raw):
    for feed, cols in TIME_COLS.items():
        df = getattr(raw, feed)
        for c in cols:
            v = df[c].dropna()
            assert (v >= 0).all(), f"{feed}.{c} âm"
    agv = raw.agv.dropna(subset=["deliver_min"])
    assert (agv["deliver_min"] >= agv["load_min"]).all()
    assert (raw.wms["need_by_min"] >= raw.wms["created_min"]).all()
    assert (raw.excel["end_min"] >= raw.excel["start_min"]).all()
    assert (raw.excel["entered_min"] >= raw.excel["end_min"]).all()


def test_parse_as_of():
    assert at("06:00") == 0.0
    assert at("10:00") == 240.0
    assert parse_as_of("2026-10-01T10:30:00+07:00", DATE, DL_CFG) == 270.0


def test_snapshot_has_nothing_after_as_of(raw):
    t = at("10:00")
    s = snapshot(raw, t)
    assert (s.wms["created_min"] <= t).all()
    assert (s.amr["ts_min"] <= t).all()
    assert (s.agv["load_min"] <= t).all()
    assert (s.agv["deliver_min"].dropna() <= t).all()
    assert s.agv["deliver_epoch"].isna().sum() >= 1          # có job đã load nhưng chưa giao
    assert (s.excel["entered_min"] <= t).all()                # excel cắt theo lúc NHẬP
    assert len(s.excel) < (raw.excel["end_min"] <= t).sum()   # nên ít hơn số dòng đã kết thúc
    # kế hoạch giữ nguyên
    assert len(s.production) == len(raw.production) and len(s.labor) == len(raw.labor)
    # snapshot không sửa dữ liệu gốc
    assert raw.agv["deliver_epoch"].notna().sum() > s.agv["deliver_epoch"].notna().sum()


def test_snapshot_end_of_day_is_everything(raw):
    s = snapshot(raw, 10_000.0)
    for name in FEEDS:
        pd.testing.assert_frame_equal(getattr(s, name), getattr(raw, name))
