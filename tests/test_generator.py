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


# ---------- Multi-seed (Phase 6/8): raw có seed trong tên thư mục ----------
from src.data_layer import resolve_raw_dir  # noqa: E402
from src.generator.batch import day_to_date, run_batch, seed_for  # noqa: E402


def test_seeded_dirs_do_not_overwrite(tmp_path):
    for seed in (42, 7):
        generate_day(seed=seed, date="2026-10-21", config=CFG, out_dir=tmp_path, seed_in_dirname=True)
    a, b = tmp_path / "2026-10-21_seed42", tmp_path / "2026-10-21_seed7"
    assert (a / "wms.csv").exists() and (b / "wms.csv").exists()
    assert (a / "wms.csv").read_bytes() != (b / "wms.csv").read_bytes()
    assert (a / "_truth" / "requests.csv").exists()


def test_default_layout_unchanged_for_phase8(tmp_path):
    """phase8.py gọi generate_day(out_dir=...) rồi đọc <out>/<ngày>/ -> mặc định phải giữ layout cũ."""
    generate_day(seed=62, date="2026-10-21", config=CFG, out_dir=tmp_path)
    assert (tmp_path / "2026-10-21" / "wms.csv").exists()


def test_seed_rule():
    assert seed_for(21, 42) == 42                      # ngày 21 seed 42 = dữ liệu demo hiện tại
    assert seed_for(20, 42) == 1042 and seed_for(1, 7) == 20007
    seeds = {seed_for(d, s) for d in range(1, 22) for s in (0, 42, 7, 123, 999)}
    assert len(seeds) == 21 * 5                        # không trùng
    for bad in ((21, 1000), (21, -1), (22, 42), (0, 42)):
        with pytest.raises(ValueError):
            seed_for(*bad)
    assert day_to_date(21, "2026-10-01") == "2026-10-21"


def test_batch_writes_real_seed_dirs_and_skips_existing(tmp_path):
    m = run_batch([20, 21], [42, 7], base_date="2026-10-01", out_dir=tmp_path, config=CFG)
    names = sorted(p.name for p in tmp_path.iterdir())
    assert names == ["2026-10-20_seed1007", "2026-10-20_seed1042", "2026-10-21_seed42", "2026-10-21_seed7"]
    assert not any(x["skipped"] for x in m)
    again = run_batch([21], [42], base_date="2026-10-01", out_dir=tmp_path, config=CFG, skip_existing=True)
    assert again[0]["skipped"]
    with pytest.raises(ValueError):                    # kiểm trước khi ghi
        run_batch([21], [1500], base_date="2026-10-01", out_dir=tmp_path / "x", config=CFG)
    assert not (tmp_path / "x").exists()


def test_batch_day21_seed42_equals_single_generation(tmp_path):
    run_batch([21], [42], base_date="2026-10-01", out_dir=tmp_path, config=CFG)
    one = generate_day(seed=42, date="2026-10-21", config=CFG, out_dir=None)
    on_disk = pd.read_csv(tmp_path / "2026-10-21_seed42" / "wms.csv")
    assert on_disk["pick_no"].tolist() == one["wms"]["pick_no"].tolist()


def test_resolve_raw_dir_prefers_seed_then_legacy(tmp_path):
    (tmp_path / "2026-10-21").mkdir()
    path, legacy = resolve_raw_dir(tmp_path, "2026-10-21", 42)
    assert path == tmp_path / "2026-10-21" and legacy
    (tmp_path / "2026-10-21_seed42").mkdir()
    path, legacy = resolve_raw_dir(tmp_path, "2026-10-21", 42)
    assert path == tmp_path / "2026-10-21_seed42" and not legacy
    path, legacy = resolve_raw_dir(tmp_path, "2026-10-22", 42)   # không có gì -> trả đường dẫn mới
    assert path == tmp_path / "2026-10-22_seed42" and not legacy
