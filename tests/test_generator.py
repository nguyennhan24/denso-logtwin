"""Test cho Phase 0 (mock đúng contract) và Phase 1 (Generator đạt gate).

Chạy: pytest -q
"""
import copy
import json
import sys
from pathlib import Path

import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.validate_contracts import check_bundle  # noqa: E402
from src.generator import FEED_NAMES, generate_day  # noqa: E402
from src.generator.world import load_config  # noqa: E402

CFG = load_config(ROOT / "config" / "generator.yaml")


@pytest.fixture(scope="module")
def day42():
    return generate_day(seed=42, config=CFG, out_dir=None)


# ---------- Phase 0 ----------
def test_mock_matches_contract():
    doc = json.loads((ROOT / "contracts" / "mock_v0_1.json").read_text(encoding="utf-8"))
    assert check_bundle(doc, horizon_min=60) == []


def test_validator_catches_busy_mismatch():
    doc = json.loads((ROOT / "contracts" / "mock_v0_1.json").read_text(encoding="utf-8"))
    bad = copy.deepcopy(doc)
    bad["current_state"]["resources"]["packer"]["busy"] = 2
    assert any("busy" in e for e in check_bundle(bad))


# ---------- Phase 1: gate ----------
def test_six_feeds_non_empty(day42):
    for name in FEED_NAMES:
        assert name in day42 and len(day42[name]) > 0, name


def test_same_seed_same_output(day42):
    again = generate_day(seed=42, config=CFG, out_dir=None)
    for name in FEED_NAMES + ["_truth/requests"]:
        pd.testing.assert_frame_equal(day42[name], again[name])


def test_different_seed_differs(day42):
    other = generate_day(seed=7, config=CFG, out_dir=None)
    assert not day42["wms"].equals(other["wms"])


def test_stage_order_is_physical(day42):
    t = day42["_truth/requests"].dropna(subset=["line_DELIVERED"])
    assert (t["amr_DISPATCH"] >= t["arrival_min"]).all()
    assert (t["amr_ARRIVE_GATE"] >= t["amr_DISPATCH"]).all()
    assert (t["packer_START"] >= t["amr_ARRIVE_GATE"]).all()
    assert (t["packer_FINISH"] >= t["packer_START"]).all()
    assert (t["agv_LOAD"] >= t["packer_FINISH"]).all()
    assert (t["line_DELIVERED"] >= t["agv_LOAD"]).all()


def test_ids_are_intentionally_different(day42):
    """Feed không dùng chung ID chuẩn -> Data Layer có việc thật để làm."""
    assert not day42["wms"]["line_code"].isin(["L1", "L2"]).any()
    assert day42["agv"]["label"].str.startswith("PK").all()
    assert day42["excel"]["pick"].dtype.kind == "i"


def test_excel_is_lagged_and_incomplete(day42):
    ex = day42["excel"]
    assert len(ex) < len(day42["_truth/requests"])        # thiếu dòng
    assert ex["entered_at"].nunique() <= 9                 # chỉ nhập theo chu kỳ 2h
