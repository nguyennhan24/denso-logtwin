"""Test Demand (Phase 6): plan -> MaterialRequest tương lai.

Raw được sinh lại vào thư mục tạm (ngày 2026-10-21, seed 42) -> không phụ thuộc data/raw trên máy.
CHỈ test (không phải src/demand) được đọc _truth/ để so với Generator.

Chạy: pytest -q tests/test_demand.py
"""
import json
import os
import shutil
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from contracts.validate_contracts import check_bundle  # noqa: E402
from src.data_layer import load_config as dl_config  # noqa: E402
from src.data_layer.pipeline import run as run_data_layer  # noqa: E402
from src.demand.conversion import (  # noqa: E402
    CONTRACT_FIELDS, build_demand, build_demand_table, count_trips, load_bom, to_contract_records, write_demand,
)
from src.demand.planner import PlanError, load_plan, load_plan_spec  # noqa: E402

DAY = "2026-10-21"
PLANS = ROOT / "config" / "plans"
BOM = load_bom(ROOT / "config" / "generator.yaml")


def at(hhmm: str) -> str:
    return f"{DAY}T{hhmm}:00+07:00"


@pytest.fixture(scope="module")
def raw_root(tmp_path_factory):
    from src.generator import generate_day
    from src.generator.world import load_config as gen_config
    out = tmp_path_factory.mktemp("raw")
    cfg = gen_config(ROOT / "config" / "generator.yaml")
    for seed in (42, 7):
        generate_day(seed=seed, date=DAY, config=cfg, out_dir=out, seed_in_dirname=True)
    return out


@pytest.fixture(scope="module")
def raw42(raw_root):
    return raw_root / f"{DAY}_seed42"


def demand(raw_dir, plan="baseline", as_of="14:10", horizon=140, seed=42, mode="expected"):
    p = load_plan(plan, at(as_of), raw_dir=raw_dir, plans_dir=PLANS)
    return build_demand_table(p, BOM, at(as_of), horizon, seed, mode)


def write_plan(tmp_path, text: str) -> Path:
    path = tmp_path / "p.yaml"
    path.write_text(text, encoding="utf-8")
    return path


# ---------- planner ----------
def test_feed_plan_is_canonical_and_tz_aware(raw42):
    p = load_plan("baseline", at("14:10"), raw_dir=raw42, plans_dir=PLANS)
    assert len(p) == 32 and set(p["line_id"]) == {"L1", "L2"}
    assert str(p["hour_start"].dt.tz) == "Asia/Ho_Chi_Minh"
    assert p["hour_start"].min() == pd.Timestamp(at("06:00"))
    raw = pd.read_csv(raw42 / "production.csv")
    assert p["planned_qty"].sum() == raw["planned_qty"].sum()


def test_spike_plan_triples_only_14_16(raw42):
    base = load_plan("baseline", at("14:10"), raw_dir=raw42, plans_dir=PLANS).set_index(["hour_start", "line_id"])
    spike = load_plan("spike_14_16_x3", at("14:10"), raw_dir=raw42, plans_dir=PLANS).set_index(["hour_start", "line_id"])
    ratio = spike["planned_qty"] / base["planned_qty"]
    hours = ratio.index.get_level_values("hour_start").hour
    assert (ratio[(hours >= 14) & (hours < 16)] == 3).all()
    assert (ratio[(hours < 14) | (hours >= 16)] == 1).all()


def test_rows_override_after_adjust(tmp_path, raw42):
    path = write_plan(tmp_path, """
plan_id: t
adjust: [{from: "14:00", to: "16:00", factor: 3}]
rows: [{hour_start: "14:00", line: L1, product: P100, planned_qty: 500},
       {hour_start: "23:00", line: L2, product: P200, planned_qty: 10}]
""")
    p = load_plan(path, at("14:10"), raw_dir=raw42).set_index(["hour_start", "line_id"])["planned_qty"]
    assert p[(pd.Timestamp(at("14:00")), "L1")] == 500             # tuyệt đối, không nhân 3
    assert p[(pd.Timestamp(at("14:00")), "L2")] % 3 == 0             # base x3
    assert p[(pd.Timestamp(at("23:00")), "L2")] == 10                # dòng mới được thêm


def test_base_none_needs_no_raw(tmp_path):
    path = write_plan(tmp_path, """
plan_id: only_rows
base: none
rows: [{hour_start: "15:00", line: L2, product: P200, planned_qty: 80}]
""")
    p = load_plan(path, at("14:10"))
    assert len(p) == 1 and p["planned_qty"].iloc[0] == 80


@pytest.mark.parametrize("body, msg", [
    ("plan_id: x\nadjust: [{from: '16:00', to: '14:00', factor: 3}]", "phải trước"),
    ("plan_id: x\nadjust: [{from: '14:30', to: '16:00', factor: 3}]", "giờ tròn"),
    ("plan_id: x\nadjust: [{from: '14:00', to: '16:00', factor: -1}]", "factor"),
    ("plan_id: x\nadjust: [{from: '14:00', to: '16:00', factor: 2, line: L1}]", "line"),
    ("plan_id: x\nbase: none", "rows"),
    ("plan_id: x\nspike: 3", "spike"),
    ("plan_id: 'bad id'", "plan_id"),
    ("plan_id: x\nrows: [{hour_start: '14:00', line: L1, product: P100, planned_qty: 1},"
     " {hour_start: '14:00', line: L1, product: P100, planned_qty: 2}]", "trùng"),
])
def test_bad_plan_file_has_clear_error(tmp_path, body, msg):
    with pytest.raises(PlanError, match=msg):
        load_plan_spec(write_plan(tmp_path, body))


def test_unknown_plan_lists_available(tmp_path):
    with pytest.raises(PlanError, match="spike_14_16_x3"):
        load_plan_spec("khong_co", PLANS)


def test_bad_production_rows_are_listed(tmp_path, raw42):
    bad = tmp_path / "bad"
    shutil.copytree(raw42, bad, ignore=shutil.ignore_patterns("_truth"))
    prod = pd.read_csv(bad / "production.csv")
    prod.loc[0, "line_code"] = "LINE_Z"
    prod.loc[1, "planned_qty"] = -5
    prod.loc[2, "hour_start"] = "không-phải-giờ"
    prod.to_csv(bad / "production.csv", index=False)
    with pytest.raises(PlanError) as e:
        load_plan("baseline", at("14:10"), raw_dir=bad, plans_dir=PLANS)
    text = str(e.value)
    assert "production.csv:2" in text and "LINE_Z" in text
    assert "production.csv:3" in text and "production.csv:4" in text


def test_non_canonical_line_or_unknown_product_rejected(tmp_path):
    for row, msg in (("line: 'Line A', product: P100", "ID chuẩn"), ("line: L1, product: P999", "BOM")):
        path = write_plan(tmp_path, f"plan_id: x\nbase: none\nrows: [{{hour_start: '15:00', {row}, planned_qty: 9}}]")
        with pytest.raises(PlanError, match=msg):
            build_demand_table(load_plan(path, at("14:10")), BOM, at("14:10"), 140, 42)


def test_plan_not_published_before_first_hour(raw42):
    with pytest.raises(PlanError, match="công bố"):
        load_plan("baseline", at("05:30"), raw_dir=raw42, plans_dir=PLANS)


# ---------- contract ----------
@pytest.mark.parametrize("mode", ["expected", "poisson"])
def test_demand_matches_contract_v01(raw42, mode):
    t = demand(raw42, mode=mode)
    recs = to_contract_records(t)
    assert recs and all(list(r) == CONTRACT_FIELDS for r in recs)
    assert check_bundle({"demand": recs}, horizon_min=140) == []
    assert all(r["request_id"].startswith("DMD-1410-") for r in recs)
    assert all(0 <= r["arrival_min"] < 140 for r in recs)
    assert all(r["origin_id"] == "STORE-1" for r in recs)
    assert t["quantity"].between(2, 6).all()
    assert (t["need_by_at"] - t["created_at"] == pd.Timedelta(minutes=30)).all()
    # arrival_min = phút từ as_of; created_at nằm trong [as_of, as_of + horizon)
    back = pd.Timestamp(at("14:10")) + pd.to_timedelta(t["arrival_min"], unit="min")
    assert ((back - t["created_at"]).abs() < pd.Timedelta(seconds=0.1)).all()


def test_build_demand_returns_twin_dataclass(raw42):
    from src.twin.entities import MaterialRequest
    p = load_plan("baseline", at("14:10"), raw_dir=raw42, plans_dir=PLANS)
    reqs = build_demand(p, BOM, at("14:10"), 140, 42)
    assert reqs and all(isinstance(r, MaterialRequest) for r in reqs)


def test_written_json_has_only_contract_fields(tmp_path, raw42):
    t = demand(raw42)
    pq, js = write_demand(t, tmp_path, "demand_baseline_1410")
    assert json.loads(js.read_text(encoding="utf-8")) == to_contract_records(t)
    back = pd.read_parquet(pq)
    assert list(back.columns[:6]) == CONTRACT_FIELDS and "need_by_at" in back.columns
    assert "priority" not in back.columns


def _state_at(raw_dir, hhmm):
    return run_data_layer(raw_dir, at(hhmm), cfg=dl_config(ROOT / "config" / "data_layer.yaml"),
                          write=False, verbose=False).state_doc


def test_demand_runs_in_twin_with_real_state(raw42):
    """Chỉ gọi Twin (src/twin) và builder baseline (src/scenario), không sửa."""
    from src.scenario.scenarios import build_baseline
    from src.twin.entities import ScenarioConfig
    from src.twin.gold import load_gold_state
    from src.twin.simulation import run
    import tempfile
    with tempfile.TemporaryDirectory() as d:
        sp = Path(d) / "state.json"
        sp.write_text(json.dumps(_state_at(raw42, "14:10")), encoding="utf-8")
        state = load_gold_state(sp)
    p = load_plan("spike_14_16_x3", at("14:10"), raw_dir=raw42, plans_dir=PLANS)
    reqs = build_demand(p, BOM, at("14:10"), 140, 42)
    wip = {j.request.request_id for j in state.queued_jobs + state.active_jobs}
    assert wip and not wip & {r.request_id for r in reqs}
    config = ScenarioConfig(**build_baseline(asdict(state), horizon_min=140, sample_every_min=10, seed=42))
    result = run(config, reqs, initial_state=state)
    assert result.input_count == len(reqs)
    arrived = {e["request_id"] for e in result.events if e["event"] == "arrival"}
    assert {r.request_id for r in reqs} <= arrived


def test_demand_json_through_scenario_runner(tmp_path, raw42):
    """JSON demand -> CLI runner của Phase 7 (check_bundle với state thật + Twin.run). Runner dùng
    default_horizon_min = 60 trong config/scenarios.yaml nên demand ở đây sinh với horizon 60."""
    state_path = tmp_path / "state.json"
    state_path.write_text(json.dumps(_state_at(raw42, "14:10")), encoding="utf-8")
    _, js = write_demand(demand(raw42, horizon=60), tmp_path, "demand")
    out = tmp_path / "result.json"
    # runner.py chưa reconfigure stdout -> print tiếng Việt lỗi cp1258 khi chạy qua pipe trên Windows.
    # Đặt PYTHONIOENCODING ở môi trường (không sửa code của runner); đã ghi vào báo cáo cho owner.
    env = {**os.environ, "PYTHONIOENCODING": "utf-8"}
    proc = subprocess.run(
        [sys.executable, "-m", "src.scenario.runner", "--state", str(state_path), "--demand", str(js),
         "--out", str(out)], cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace", env=env)
    assert proc.returncode == 0, proc.stderr[-2000:]
    res = json.loads(out.read_text(encoding="utf-8"))
    assert res["BASELINE"]["input_count"] == len(json.loads(js.read_text(encoding="utf-8")))


# ---------- tái lập ----------
@pytest.mark.parametrize("mode", ["expected", "poisson"])
def test_same_seed_same_demand(raw42, mode):
    pd.testing.assert_frame_equal(demand(raw42, mode=mode), demand(raw42, mode=mode))


def test_other_seed_changes_demand(raw42):
    # expected: số lượt mỗi giờ tất định, chỉ thời điểm thay đổi theo seed. So trên giờ trọn (14:00-16:00);
    # cửa sổ cắt giữa giờ (14:10) thì số lượt lọt vào cửa sổ có thể khác vì thời điểm khác.
    a, b = demand(raw42, as_of="14:00", horizon=120, seed=42), demand(raw42, as_of="14:00", horizon=120, seed=43)
    assert len(a) == len(b) and not np.allclose(a["arrival_min"], b["arrival_min"])
    assert len(demand(raw42, seed=42)) > 0
    pa, pb = demand(raw42, seed=42, mode="poisson"), demand(raw42, seed=43, mode="poisson")
    assert len(pa) != len(pb) or not np.allclose(pa["arrival_min"], pb["arrival_min"])


def test_demand_does_not_depend_on_as_of_except_window(raw42):
    """Sinh cả ngày rồi mới cắt cửa sổ -> 2 as_of khác nhau thấy cùng các lượt ở phần giao nhau."""
    a = demand(raw42, as_of="14:10", horizon=140)
    b = demand(raw42, as_of="14:40", horizon=60)
    sa = a.loc[(a["created_at"] >= pd.Timestamp(at("14:40"))) & (a["created_at"] < pd.Timestamp(at("15:40"))),
               ["created_at", "destination_line_id", "material_id", "quantity"]].reset_index(drop=True)
    pd.testing.assert_frame_equal(sa, b[["created_at", "destination_line_id", "material_id", "quantity"]])


def test_expected_count_is_carry_rounded_sum_of_lambda(raw42):
    p = load_plan("baseline", at("06:00"), raw_dir=raw42, plans_dir=PLANS)
    cells = count_trips(p, BOM, "expected")
    for _, g in cells.groupby(["line_id", "product", "material_id"]):
        assert g["n_trips"].sum() == int(np.floor(g["lam"].sum() + 0.5))
        assert (g["n_trips"] >= 0).all()


# ---------- khớp Generator (so với _truth, chỉ trong test) ----------
@pytest.mark.parametrize("mode", ["expected", "poisson"])
def test_close_to_generator_truth_day21_seed42(raw42, mode):
    """Truth là 1 lần rút Poisson quanh cùng kỳ vọng: cho phép lệch <= 3 sqrt(kỳ vọng). Số liệu chi tiết: scripts/check_demand.py."""
    full = demand(raw42, as_of="06:00", horizon=16 * 60, mode=mode)
    truth = pd.read_csv(raw42 / "_truth" / "requests.csv")
    assert abs(len(full) - len(truth)) <= 3 * np.sqrt(len(full))
    for line in ("L1", "L2"):
        d = (full["destination_line_id"] == line).sum()
        assert abs(d - (truth["line_id"] == line).sum()) <= 3 * np.sqrt(d)
    assert abs(full["quantity"].mean() - truth["packs"].mean()) < 0.3


# ---------- spike ----------
def test_spike_triples_requests_in_14_16(raw42):
    base = demand(raw42, "baseline", as_of="14:00", horizon=120)
    spike = demand(raw42, "spike_14_16_x3", as_of="14:00", horizon=120)
    assert 2.8 <= len(spike) / len(base) <= 3.2
    for line in ("L1", "L2"):
        r = (spike["destination_line_id"] == line).sum() / (base["destination_line_id"] == line).sum()
        assert 2.7 <= r <= 3.3


def test_spike_leaves_hours_before_14_unchanged(raw42):
    cols = ["created_at", "destination_line_id", "material_id", "quantity"]
    base = demand(raw42, "baseline", as_of="06:00", horizon=480)[cols]
    spike = demand(raw42, "spike_14_16_x3", as_of="06:00", horizon=480)[cols]
    pd.testing.assert_frame_equal(base, spike)


# ---------- không rò rỉ ----------
def test_demand_reads_only_production_and_master(raw42, monkeypatch):
    opened = []
    real = pd.read_csv
    monkeypatch.setattr(pd, "read_csv", lambda p, *a, **k: (opened.append(str(p)), real(p, *a, **k))[1])
    demand(raw42)
    names = {Path(p).name for p in opened}
    assert names == {"production.csv", "master.csv"}
    assert not any("_truth" in p for p in opened)


def _copy_raw(src, dst):
    shutil.copytree(src, dst, ignore=shutil.ignore_patterns("_truth"))
    return dst


def test_cutting_events_after_as_of_and_truth_does_not_change_demand(tmp_path, raw42):
    cut = _copy_raw(raw42, tmp_path / "cut")
    as_of = pd.Timestamp(at("14:10"))
    for name, col, kind in (("wms", "created_at", "iso"), ("amr", "ts", "iso"),
                            ("agv", "load_epoch", "epoch"), ("excel", "entered_at", "iso")):
        df = pd.read_csv(cut / f"{name}.csv")
        t = pd.to_datetime(df[col], unit="s", utc=True) if kind == "epoch" else pd.to_datetime(df[col], utc=True)
        assert (t > as_of).any()                         # thật sự có dòng tương lai để xóa
        df[t <= as_of].to_csv(cut / f"{name}.csv", index=False)
    assert not (cut / "_truth").exists()
    pd.testing.assert_frame_equal(demand(raw42), demand(cut))


def test_replacing_event_feeds_with_other_seed_does_not_change_demand(tmp_path, raw_root, raw42):
    """Demand chỉ phụ thuộc plan + BOM + as_of + seed: thay hết wms/amr/agv/excel bằng dữ liệu seed 7 -> giống hệt."""
    mixed = _copy_raw(raw42, tmp_path / "mixed")
    for name in ("wms", "amr", "agv", "excel", "labor"):
        shutil.copy(raw_root / f"{DAY}_seed7" / f"{name}.csv", mixed / f"{name}.csv")
    assert not (mixed / "wms.csv").read_bytes() == (raw42 / "wms.csv").read_bytes()
    pd.testing.assert_frame_equal(demand(raw42), demand(mixed))
