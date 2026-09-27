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


# ======================================================== Bước 2: kiểm tra 3 tầng
from src.data_layer.schemas import validate_feeds  # noqa: E402


def test_clean_seed_has_empty_quarantine(raw):
    clean, q = validate_feeds(raw)
    assert q.empty, q.to_string()
    for name in FEEDS:
        assert len(getattr(clean, name)) == len(getattr(raw, name))


def test_dirty_rows_are_quarantined_with_reason(raw):
    wms, amr, agv = raw.wms.copy(), raw.amr.copy(), raw.agv.copy()
    wms.loc[5, "line_code"] = "LINE_Z"
    i_ret = amr.index[amr["event"] == "RETURNED"][3]
    amr.loc[i_ret, "event"] = "FLY"
    agv.loc[7, "label"] = "PK999999"
    dirty = raw.with_feeds(wms=wms, amr=amr, agv=agv)

    clean, q = validate_feeds(dirty)
    got = {(r.feed, r.row_index): r.reason for r in q.itertuples()}
    assert set(got) == {("wms", 5), ("amr", i_ret), ("agv", 7)}, q.to_string()
    assert "line_code" in got[("wms", 5)]
    assert "event" in got[("amr", i_ret)]
    assert "label" in got[("agv", 7)] and "tầng 3" in got[("agv", 7)]
    # các dòng khác vẫn giữ
    assert len(clean.wms) == len(wms) - 1
    assert len(clean.amr) == len(amr) - 1
    assert len(clean.agv) == len(agv) - 1
    assert len(clean.excel) == len(raw.excel)


def test_tier1_catches_unreadable_values(raw):
    wms, excel = raw.wms.copy(), raw.excel.copy()
    wms.loc[2, "qty_packs"] = pd.NA            # ô "abc" sau khi ép kiểu thành NA
    excel.loc[1, "start_min"] = float("nan")   # "25:99" không đọc được
    _, q = validate_feeds(raw.with_feeds(wms=wms, excel=excel))
    reasons = dict(zip(zip(q["feed"], q["row_index"]), q["reason"]))
    assert "qty_packs" in reasons[("wms", 2)]
    assert "start" in reasons[("excel", 1)]


def test_orphan_amr_task_is_quarantined(raw):
    amr = raw.amr.copy()
    i = amr.index[amr["event"] == "ARRIVE_GATE"][0]
    amr.loc[i, "task_id"] = "T-9999"
    _, q = validate_feeds(raw.with_feeds(amr=amr))
    assert list(q["row_index"]) == [i] and "DISPATCH" in q["reason"].iloc[0]


# ======================================================== Bước 3: ghép ID -> journey
from src.data_layer.mapper import JOURNEY_COLS, build_journey, match_stats  # noqa: E402

END = 10_000.0     # "cuối ngày": sau mọi sự kiện
SEC = 1 / 60       # 1 giây tính bằng phút


@pytest.fixture(scope="module")
def clean_full(raw):
    return validate_feeds(raw)[0]


@pytest.fixture(scope="module")
def journey_end(clean_full):
    return build_journey(clean_full, END)


def test_journey_shape(journey_end, raw):
    assert list(journey_end.columns) == JOURNEY_COLS
    assert journey_end["request_id"].is_unique
    assert len(journey_end) == len(raw.wms)
    assert journey_end["line_id"].isin(["L1", "L2"]).all()
    assert journey_end["material_id"].str.match(r"^M0\d$").all()


def test_journey_matches_truth(journey_end, truth):
    m = journey_end.merge(truth, left_on="request_id", right_on="pick_no", how="inner",
                          suffixes=("", "_truth"))
    assert len(m) == len(truth)
    pairs = {
        "created_min": "arrival_min",
        "amr_dispatch_min": "amr_DISPATCH",
        "amr_arrive_gate_min": "amr_ARRIVE_GATE",
        "packer_start_min": "amr_RELEASE",
        "agv_load_min": "agv_LOAD",
        "delivered_min": "line_DELIVERED",
    }
    for ours, theirs in pairs.items():
        both = m[[ours, theirs]]
        assert (both[ours].isna() == both[theirs].isna()).all(), ours
        d = (both[theirs] - both[ours]).dropna()
        assert ((d >= -1e-9) & (d < SEC)).all(), f"{ours}: lệch tối đa {d.abs().max()}"
    assert (m["task_id"] == m["amr_task_id"]).all()
    assert (m["material_id"] == m["material_id_truth"]).all()
    assert (m["line_id"] == m["line_id_truth"]).all() and (m["quantity"] == m["packs"]).all()

    # packer_finish từ Excel: khớp truth ở mức phút, và KHÔNG tự điền dòng Excel bị thiếu
    ex = m.dropna(subset=["packer_finish_min"])
    d = ex["packer_FINISH"] - ex["packer_finish_min"]
    assert ((d >= -1e-6) & (d < 1)).all()
    missing = m[m["packer_finish_min"].isna() & m["agv_load_min"].notna()]
    assert len(missing) > 0      # Excel thiếu ~5% dòng -> có lượt đã lên AGV mà không có finish


def test_match_rates(clean_full, journey_end):
    st = match_stats(clean_full, journey_end)
    print("\nmatch_stats seed 42:", {k: {kk: v[kk] for kk in ("total", "matched", "rate")} for k, v in st.items()})
    assert st["amr"]["rate"] >= 0.99
    assert st["agv"]["rate"] >= 0.99
    assert st["excel"]["rate"] == 1.0


def test_journey_as_of_has_no_future(clean_full):
    t = at("10:00")
    j = build_journey(clean_full, t)
    for c in [c for c in JOURNEY_COLS if c.endswith("_min") and c != "need_by_min"]:
        assert (j[c].dropna() <= t).all(), c
    assert j["packer_finish_min"].notna().sum() > 0


def test_journey_at_day_start_is_empty(clean_full):
    j = build_journey(clean_full, 0.0)
    assert list(j.columns) == JOURNEY_COLS and len(j) == 0


# ======================================================== Bước 4: freshness + state
import json  # noqa: E402
from datetime import datetime  # noqa: E402

import numpy as np  # noqa: E402

from contracts.validate_contracts import FEEDS as CONTRACT_FEEDS, check_state  # noqa: E402
from src.data_layer.freshness import feed_updated_at, freshness_report  # noqa: E402
from src.data_layer.state_builder import build_diagnostics, build_state, infer_wip  # noqa: E402

CHECK_TIMES = ["08:00", "10:00", "14:00", "18:00"]


def state_at(raw_feeds, t):
    """Chuỗi chuẩn: snapshot -> validate -> journey -> state (+ wip để so truth)."""
    clean, _ = validate_feeds(snapshot(raw_feeds, t))
    j = build_journey(clean, t)
    return clean, j, build_state(clean, j, t, DL_CFG)


def truth_stage_sets(truth, t):
    """Stage đúng của từng lượt giao tại t theo truth. Feed ghi giờ cắt xuống giây -> làm tương tự."""
    def known(c):
        return np.floor(truth[c] * 60 + 1e-6) / 60 <= t

    masks = {
        ("amr", "queued"): known("arrival_min") & ~known("amr_DISPATCH"),
        ("amr", "active"): known("amr_DISPATCH") & ~known("amr_ARRIVE_GATE"),
        ("packer", "queued"): known("amr_ARRIVE_GATE") & ~known("packer_START"),
        ("packer", "active"): known("packer_START") & ~known("packer_FINISH"),
        ("agv", "queued"): known("packer_FINISH") & ~known("agv_LOAD"),
        ("agv", "active"): known("agv_LOAD") & ~known("line_DELIVERED"),
    }
    return {k: set(truth.loc[m, "pick_no"]) for k, m in masks.items()}


def state_sets(state):
    out = {(s, st): set() for s in ("amr", "packer", "agv") for st in ("queued", "active")}
    for st, key in (("queued", "queued_jobs"), ("active", "active_jobs")):
        for job in state[key]:
            out[(job["stage"], st)].add(job["request"]["request_id"])
    return out


def test_feed_updated_at(raw):
    t = at("10:00")
    fu = feed_updated_at(raw, t)             # đưa cả ngày vào: hàm vẫn phải tự cắt theo as_of
    assert set(fu) == set(CONTRACT_FEEDS)
    as_of = datetime.fromisoformat("2026-10-01T10:00:00+07:00")
    for k, v in fu.items():
        assert v.endswith("+07:00") and datetime.fromisoformat(v) <= as_of, k
    assert fu["excel"] == "2026-10-01T10:00:00+07:00"      # đợt nhập Excel lúc 10:00
    assert fu["production"] == fu["labor"] == "2026-10-01T06:00:00+07:00"
    rep = freshness_report(raw, t)
    assert rep["excel"]["source_type"] == "manual" and rep["amr"]["source_type"] == "stream"
    assert all(r["lag_min"] >= 0 for r in rep.values())
    assert rep["production"]["lag_min"] == 240.0


@pytest.mark.parametrize("hhmm", CHECK_TIMES)
def test_state_passes_contract(raw, hhmm):
    _, _, st = state_at(raw, at(hhmm))
    assert check_state(st) == [], check_state(st)
    json.dumps(st)                                          # xuất JSON được
    assert st["as_of"] == f"2026-10-01T{hhmm}:00+07:00"


@pytest.mark.parametrize("hhmm", CHECK_TIMES)
def test_state_amr_matches_truth_exactly(raw, truth, hhmm):
    t = at(hhmm)
    ours, real = state_sets(state_at(raw, t)[2]), truth_stage_sets(truth, t)
    assert ours[("amr", "queued")] == real[("amr", "queued")]
    assert ours[("amr", "active")] == real[("amr", "active")]
    assert ours[("packer", "queued")] == real[("packer", "queued")]   # RELEASE có trong stream -> chính xác
    assert ours[("agv", "active")] == real[("agv", "active")]         # load/deliver có trong stream


def test_state_packer_agv_error_table(raw, truth):
    """Bảng sai số packer/agv (phần phải ước lượng vì Excel trễ). In ra để theo dõi.

    Ngưỡng: lệch số lượng ≤ 1 ở mỗi ô tại 4 mốc. Quét 10 seed × mỗi 5 phút cho thấy
    tỷ lệ khớp chính xác tập packer active / agv queued = 98,5% (xem docs/phase4_design.md mục 5).
    """
    print("\nas_of  | packer_active ours/truth | agv_queued ours/truth | lệch tập")
    for hhmm in CHECK_TIMES:
        t = at(hhmm)
        ours, real = state_sets(state_at(raw, t)[2]), truth_stage_sets(truth, t)
        diff = sum(len(ours[k] ^ real[k]) for k in [("packer", "active"), ("agv", "queued")])
        print(f"{hhmm}  | {len(ours[('packer', 'active')])}/{len(real[('packer', 'active')])}"
              f"                      | {len(ours[('agv', 'queued')])}/{len(real[('agv', 'queued')])}"
              f"                   | {diff}")
        for k in [("packer", "active"), ("agv", "queued")]:
            assert abs(len(ours[k]) - len(real[k])) <= 1, (hhmm, k)


def test_state_queue_order_and_wip_rules(raw):
    clean, j, st = state_at(raw, at("10:00"))
    wip, _ = infer_wip(clean, j, at("10:00"), DL_CFG)
    entered = dict(zip(wip["request_id"], wip["entered_min"]))
    for stage in ("amr", "packer", "agv"):
        ids = [q["request"]["request_id"] for q in st["queued_jobs"] if q["stage"] == stage]
        keys = [(entered[i], i) for i in ids]
        assert keys == sorted(keys), stage
    for job in st["queued_jobs"] + st["active_jobs"]:
        assert job["request"]["arrival_min"] == 0
        assert job["request"]["origin_id"] == "STORE-1"
    assert all(a["remaining_min"] >= DL_CFG["min_remaining_min"] for a in st["active_jobs"])


def _truncate_csvs(src, dst, t):
    """Tự cắt CSV: xóa mọi dòng sau as_of, xóa deliver_epoch sau as_of. KHÔNG copy _truth/."""
    full = load_day(src, DL_CFG)
    dst.mkdir(parents=True)
    keep = {
        "wms": full.wms["created_min"] <= t,
        "amr": full.amr["ts_min"] <= t,
        "agv": full.agv["load_min"] <= t,
        "excel": full.excel["entered_min"] <= t,
    }
    for name in FEEDS + ("master",):
        df = pd.read_csv(src / f"{name}.csv", dtype=str, keep_default_na=False)
        if name in keep:
            df = df[keep[name].to_numpy()].copy()
        if name == "agv":
            late = (full.agv.loc[keep["agv"], "deliver_min"] > t).to_numpy()
            df.loc[late, "deliver_epoch"] = ""
        df.to_csv(dst / f"{name}.csv", index=False)


@pytest.mark.parametrize("hhmm", ["07:30", "10:00", "14:10", "18:00"])
def test_no_look_ahead(raw, day_dir, tmp_path, hhmm):
    """State từ feed đầy đủ == state từ feed đã bị xóa mọi dòng sau as_of."""
    t = at(hhmm)
    cut_dir = tmp_path / DATE
    _truncate_csvs(day_dir, cut_dir, t)
    cut = load_day(cut_dir, DL_CFG)
    c_full, j_full, st_full = state_at(raw, t)
    c_cut, j_cut, st_cut = state_at(cut, t)
    assert st_full == st_cut
    assert build_diagnostics(c_full, j_full, t, DL_CFG) == build_diagnostics(c_cut, j_cut, t, DL_CFG)
    pd.testing.assert_frame_equal(j_full, j_cut)


def test_diagnostics(raw):
    t = at("10:00")
    clean, j, st = state_at(raw, t)
    d = build_diagnostics(clean, j, t, DL_CFG)
    n_packer_q = sum(1 for q in st["queued_jobs"] if q["stage"] == "packer")
    assert d["amr_held_at_gate"]["count"] == n_packer_q      # thùng chờ ở cổng = AMR bị giữ
    assert d["amr_returning"]["count"] >= 0
    assert d["estimates"]["pack_source"] == "data"
    assert d["estimated_jobs"]["count"] >= 0
    json.dumps(d)


# ======================================================== Bước 5: quality + pipeline
from contracts.validate_contracts import check_bundle  # noqa: E402
from src.data_layer.pipeline import ContractError, run  # noqa: E402
from src.data_layer.quality import dq_report  # noqa: E402


def test_dq_report(raw):
    t = at("10:00")
    snap = snapshot(raw, t)
    wms = snap.wms.copy()
    wms.loc[wms.index[0], "line_code"] = "LINE_Z"
    snap = snap.with_feeds(wms=wms)
    clean, q = validate_feeds(snap)
    j = build_journey(clean, t)
    rep = dq_report(snap, clean, q, j, t, DL_CFG)
    assert rep["rows"]["wms"]["quarantined"] == 1
    keys = {m["key"] for m in rep["metrics"]}
    assert {"rows_in", "match_amr", "match_agv", "match_excel", "excel_coverage",
            "estimated_jobs", "lag_excel"} <= keys
    assert all(m["label"] for m in rep["metrics"])
    assert rep["match"]["amr"]["rate"] < 1.0          # task của phiếu WMS bị cách ly không ghép được
    assert 0.9 <= rep["excel_coverage"]["coverage"] <= 1.0
    assert rep["quarantine_reasons"][0]["count"] == 1
    json.dumps(rep, ensure_ascii=False)


def _run_pipeline(day_dir, out):
    return run(DATE, ["08:00", "10:00", "14:30"], raw_dir=day_dir.parent, out_dir=out,
               config_path=ROOT / "config" / "data_layer.yaml")


def test_pipeline_end_to_end(day_dir, tmp_path):
    s = _run_pipeline(day_dir, tmp_path / "u1")
    gold = tmp_path / "u1" / DATE / "gold"
    silver = tmp_path / "u1" / DATE / "silver"
    for tag in ("0800", "1000", "1430"):
        st = json.loads((gold / f"state_{tag}.json").read_text(encoding="utf-8"))
        assert check_bundle({"current_state": st, "demand": []}) == []
        assert (gold / f"dq_{tag}.json").exists() and (gold / f"diag_{tag}.json").exists()
    j = pd.read_parquet(gold / "journey.parquet")
    assert list(j.columns) == JOURNEY_COLS and (j["created_min"] <= at("14:30")).all()
    for f in FEEDS + ("master", "quarantine"):
        assert (silver / f"{f}.parquet").exists(), f
    assert [r["as_of"][11:16] for r in s["as_of"]] == ["08:00", "10:00", "14:30"]

    # Chạy 2 lần -> kết quả giống hệt từng byte
    _run_pipeline(day_dir, tmp_path / "u2")
    for p in sorted((tmp_path / "u1" / DATE).rglob("*.*")):
        q = tmp_path / "u2" / DATE / p.relative_to(tmp_path / "u1" / DATE)
        assert p.read_bytes() == q.read_bytes(), p.name


def test_pipeline_stops_on_contract_error(day_dir, tmp_path, monkeypatch):
    import src.data_layer.pipeline as pl

    def broken(*a, **k):
        st = build_state(*a, **k)
        st["resources"]["amr"]["busy"] += 1      # cố ý làm sai quy tắc busy = số active job
        return st

    monkeypatch.setattr(pl, "build_state", broken)
    with pytest.raises(ContractError, match="busy"):
        _run_pipeline(day_dir, tmp_path / "bad")
    assert not (tmp_path / "bad").exists()     # không ghi gì khi state sai


# ======================================================== Script tự kiểm tra trong docs/examples
import subprocess  # noqa: E402


@pytest.mark.parametrize("args", [
    ["trace_request.py", "--request", "PICK-00050", "--as-of", "08:15"],
    ["compare_truth.py", "--as-of", "10:00"],
])
def test_docs_examples_run(day_dir, args):
    """Các script hướng dẫn trong docs/data_layer.md phải chạy được (tài liệu không bị cũ)."""
    cmd = [sys.executable, str(ROOT / "docs" / "examples" / args[0]), *args[1:],
           "--raw-dir", str(day_dir.parent), "--date", DATE]
    out = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert out.returncode == 0, out.stderr
    if args[0] == "compare_truth.py":
        assert "Khớp chính xác 6/6" in out.stdout


def test_docs_check_state_file(day_dir, tmp_path):
    run(DATE, ["10:00"], raw_dir=day_dir.parent, out_dir=tmp_path, config_path=ROOT / "config" / "data_layer.yaml")
    f = tmp_path / DATE / "gold" / "state_1000.json"
    out = subprocess.run([sys.executable, str(ROOT / "docs" / "examples" / "check_state_file.py"), str(f)],
                         capture_output=True, text=True, encoding="utf-8", cwd=ROOT)
    assert out.returncode == 0 and "HỢP LỆ" in out.stdout, out.stdout + out.stderr
