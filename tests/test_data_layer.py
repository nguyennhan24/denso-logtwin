"""Test Data Layer (Phase 4).

Phần lớn test chạy trên tests/fixtures/raw_small (CSV viết tay, có lỗi cố ý) để không phụ thuộc Generator.
Vài test cuối chạy trên dữ liệu Generator thật (ngày 2026-10-21, seed 42) và đối chiếu _truth/ — CHỈ test được đọc _truth.

Chạy: pytest -q tests/test_data_layer.py
"""
import copy
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.data_layer import load_config  # noqa: E402
from src.data_layer.contract_check import ContractError, write_gold  # noqa: E402
from src.data_layer.ingest import EVENT_TIME_COLUMNS, ingest  # noqa: E402
from src.data_layer.mapper import normalize_request_id, normalize_task_id  # noqa: E402
from src.data_layer.pipeline import run  # noqa: E402
from src.data_layer.validate import validate_all  # noqa: E402

FIX = ROOT / "tests" / "fixtures" / "raw_small"
CFG = load_config(ROOT / "config" / "data_layer.yaml")
AS_OF = "2026-10-21T14:10:00+07:00"


def ts(hhmm: str) -> pd.Timestamp:
    return pd.Timestamp(f"2026-10-21T{hhmm}:00+07:00")


def reasons_of(q: pd.DataFrame, row_ref: str) -> set:
    return set(q.loc[q["row_ref"] == row_ref, "reason"])


@pytest.fixture(scope="module")
def ing():
    return ingest(FIX, AS_OF, CFG)


@pytest.fixture(scope="module")
def val(ing):
    return validate_all(ing.feeds, CFG)


# ---------- Bước 1: ingest ----------
def test_ingest_never_reads_truth(monkeypatch):
    """Theo dõi mọi lần pandas mở file: không lần nào được chạm vào _truth/."""
    opened = []
    real_read_csv = pd.read_csv

    def spy(path, *a, **kw):
        opened.append(str(path))
        return real_read_csv(path, *a, **kw)

    monkeypatch.setattr(pd, "read_csv", spy)
    ingest(FIX, AS_OF, CFG)
    assert opened, "ingest phải đọc file qua pd.read_csv"
    assert not any("_truth" in p for p in opened)


def test_ingest_drops_future_rows(ing):
    """Mọi cột SỰ KIỆN THỰC TẾ của mọi feed phải <= as_of hoặc rỗng."""
    as_of = pd.Timestamp(AS_OF)
    for name, cols in EVENT_TIME_COLUMNS.items():
        for col in cols:
            assert (ing.feeds[name][col].dropna() <= as_of).all(), f"{name}.{col}"
    assert "PICK-00006" not in set(ing.feeds["wms"]["pick_no"])          # tạo lúc 14:20
    assert ing.report["amr"]["future_rows_cut"] == 2


@pytest.mark.parametrize("hhmm, deliver_known", [("14:07", None), ("14:10", False), ("14:12", True)])
def test_agv_row_between_load_and_deliver(hhmm, deliver_known):
    """J-0002: LOAD 14:08, giao 14:12. Trước LOAD: chưa có dòng. Giữa: giữ dòng, giờ giao = NaT. Sau: đủ."""
    agv = ingest(FIX, f"2026-10-21T{hhmm}:00+07:00", CFG).feeds["agv"].set_index("job_id")
    if deliver_known is None:
        assert "J-0002" not in agv.index
        return
    assert agv.loc["J-0002", "load_epoch"] == ts("14:08")
    if deliver_known:
        assert agv.loc["J-0002", "deliver_epoch"] == ts("14:12")
    else:
        assert pd.isna(agv.loc["J-0002", "deliver_epoch"])


def test_plan_columns_are_kept_even_after_as_of(ing):
    """Cột kế hoạch / hạn được công bố trước -> không bị cắt dù nằm sau as_of."""
    as_of = pd.Timestamp(AS_OF)
    assert (ing.feeds["wms"]["need_by"] > as_of).any()                    # hạn giao
    assert (ing.feeds["production"]["hour_start"] > as_of).any()          # kế hoạch giờ 15:00
    assert (ing.feeds["labor"]["break_start"] > as_of).any()              # lịch nghỉ ca 2
    assert ing.report["production"]["future_rows_cut"] == 0
    assert ing.report["labor"]["future_rows_cut"] == 0


def test_ingest_parses_all_time_formats_to_local_tz(ing):
    assert str(ing.feeds["agv"]["load_epoch"].dt.tz) == "Asia/Ho_Chi_Minh"      # epoch giây
    assert ing.feeds["excel"]["start"].iloc[0] == ts("13:55")                      # "HH:MM"
    assert ing.feeds["labor"]["shift_start"].min() == ts("06:00")
    assert ing.feeds["agv"]["load_epoch"].min() == ts("12:00")


def test_ingest_drops_exact_duplicates_but_records_them(ing):
    dup = ing.quarantine
    assert set(dup["row_ref"]) == {"wms.csv:4", "excel.csv:3"}
    assert set(dup["reason"]) == {"duplicate_row"}
    assert (ing.feeds["wms"]["pick_no"] == "PICK-00001").sum() == 1


# ---------- Bước 2: validate ----------
def test_validate_flags_bad_rows(val):
    _, q = val
    assert reasons_of(q, "wms.csv:7") == {"out_of_range"}           # qty_packs = 0
    assert reasons_of(q, "wms.csv:9") == {"missing_timestamp"}      # created_at trống
    assert reasons_of(q, "amr.csv:12") == {"invalid_event"}         # event JUMP
    assert reasons_of(q, "amr.csv:18") == {"missing_timestamp"}     # ts trống
    assert reasons_of(q, "agv.csv:2") == {"out_of_range"}           # chuyến AGV 110 phút
    assert reasons_of(q, "production.csv:4") == {"out_of_range"}    # planned_qty = -5
    assert reasons_of(q, "production.csv:6") == {"not_a_number"}    # planned_qty = abc
    assert reasons_of(q, "excel.csv:5") == {"out_of_range"}         # end < start
    assert reasons_of(q, "excel.csv:6") == {"missing_timestamp"}    # end trống


def test_validate_does_not_flag_misspelled_ids(val):
    """ID lệch dạng (pick-3, PICK-4, t4, PK-2) là việc của mapper, validate không được gắn cờ."""
    clean, q = val
    for ref in ["amr.csv:11", "amr.csv:14", "amr.csv:16", "agv.csv:5"]:
        assert reasons_of(q, ref) == set(), ref
    assert "pick-3" in set(clean["amr"]["wms_ref"])
    # task_id không tồn tại cũng chưa bị gắn cờ ở bước này (chưa nối khóa)
    assert reasons_of(q, "amr.csv:17") == set()


def test_validate_keeps_clean_rows_typed(val):
    clean, _ = val
    assert clean["wms"]["qty_packs"].dtype == "int64"
    assert len(clean["wms"]) == 6          # 10 dòng - 1 tương lai - 1 trùng - 2 lỗi


# ---------- Bước 3: mapper ----------
@pytest.fixture(scope="module")
def res():
    return run(FIX, AS_OF, cfg=CFG, write=False, verbose=False)


def test_normalize_ids_handles_every_variant():
    for v in ["PICK-00012", "PK12", 12, "12", "pick-12", "PK-12", " pk 012 ", "PICK-12", "12.0"]:
        assert normalize_request_id(v) == "PICK-00012", v
    for v in [None, float("nan"), "", "ABC", "PK", "0", "T-0012"]:
        assert normalize_request_id(v) is None, v
    for v in ["T-0007", "t7", "T0007", "t-7"]:
        assert normalize_task_id(v) == "T-0007", v


def test_misspelled_ids_are_linked_after_mapper(res):
    j = res.journey.set_index("request_id")
    assert pd.notna(j.loc["PICK-00003", "amr_dispatch_at"])      # wms_ref "pick-3"
    assert pd.notna(j.loc["PICK-00003", "amr_arrive_gate_at"])   # task_id "t4"
    assert j.loc["PICK-00004", "amr_id"] == "AMR-02"             # wms_ref "PICK-4"
    assert j.loc["PICK-00002", "agv_id"] == "AGV-02"             # label "PK-2"
    assert j.loc["PICK-00001", "worker_id"] == "W01"             # excel pick 1


def test_unlinkable_rows_go_to_quarantine_with_reason(res):
    q = res.quarantine
    assert reasons_of(q, "amr.csv:17") == {"task_not_in_wms"}     # T-0099 chưa từng DISPATCH
    assert reasons_of(q, "agv.csv:4") == {"label_not_in_wms"}     # PK77
    assert reasons_of(q, "excel.csv:4") == {"pick_not_in_wms"}    # pick 88


def test_out_of_range_kept_in_report_totals(res):
    """Dòng ngoài khoảng bị cách ly nhưng vẫn được đếm trong báo cáo, không biến mất."""
    q = res.quality
    assert q["feed_rows"]["wms"]["raw_rows"] == 10
    assert q["feed_rows"]["wms"]["quarantined_rows"] == 3          # trùng + qty 0 + thiếu giờ
    assert q["quarantine_by_reason"]["out_of_range"] == 4
    assert q["quarantine_total"] == len(res.quarantine) == 14
    assert q["link_rate"]["overall"]["rows"] == 19


# ---------- Bước 4: state ----------
def check_resource_sum(state: dict):
    det = state["resource_detail"]["amr"]
    total = state["resources"]["amr"]["total"]
    assert len(det["busy"]) + len(det["idle"]) + len(det["blocked"]) + len(det["offline"]) == total
    assert len(det["offline"]) == state["resources"]["amr"]["offline"]
    # Mỗi loại tài nguyên: busy + blocked (nếu có) + offline + idle = total,
    # idle = total - busy - blocked - offline phải bằng số ID trong resource_detail.<loại>.idle (nếu có danh sách)
    for kind, c in state["resource_counts"].items():
        assert c["total"] == state["resources"][kind]["total"], kind
        assert c["offline"] == state["resources"][kind]["offline"], kind
        idle = c["total"] - c["busy"] - c.get("blocked", 0) - c["offline"]
        assert idle >= 0 and c["idle"] == idle, kind
        ids = state["resource_detail"][kind].get("idle")
        if ids is not None:
            assert len(ids) == idle, kind
    assert state["resource_counts"]["amr"]["blocked"] == len(det["blocked"])
    assert [j["amr_id"] for j in det["blocked_jobs"]] == det["blocked"]


def check_active_job_flags(state: dict):
    """overdue <=> est_remaining_min = 0; completion_unconfirmed <=> overdue và mốc kết thúc khâu là nguồn manual."""
    manual_stage = {"amr": False, "packer": True, "agv": False}     # packer kết thúc theo Excel nhập tay
    for j in state["active_jobs"]:
        assert j["overdue"] == (j["est_remaining_min"] == 0), j["request"]["request_id"]
        if j["overdue"]:
            assert j["est_remaining_min"] == 0
        assert j["completion_unconfirmed"] == (j["overdue"] and manual_stage[j["stage"]])
        assert j["remaining_min"] > 0                               # contract v0.1 vẫn giữ nguyên


def test_amr_busy_idle_blocked_offline_sum_to_total(res):
    s = res.state_doc["current_state"]
    check_resource_sum(s)
    det = s["resource_detail"]["amr"]
    assert det == {
        "busy": ["AMR-02"],                 # DISPATCH 14:07, đang chở
        "blocked": ["AMR-01", "AMR-04"],    # ARRIVE_GATE, đứng chờ ở cổng
        "idle": ["AMR-03", "AMR-06"],       # RETURNED / chưa có sự kiện hợp lệ
        "offline": ["AMR-05"],              # FAULT 14:05
        # Thùng mà xe blocked đang giữ = request của sự kiện ARRIVE_GATE gần nhất
        "blocked_jobs": [{"amr_id": "AMR-01", "request_id": "PICK-00009"},
                         {"amr_id": "AMR-04", "request_id": "PICK-00003"}],
    }
    assert s["resources"]["amr"] == {"total": 6, "busy": 1, "offline": 1}   # busy contract = job đang ở khâu AMR
    assert "blocked" not in s["resources"]["amr"]                            # Twin dựng ResourceStatus(**...)
    assert s["resource_counts"] == {
        "amr": {"total": 6, "busy": 1, "blocked": 2, "idle": 2, "offline": 1},
        "packer": {"total": 3, "busy": 0, "idle": 2, "offline": 1},
        "agv": {"total": 4, "busy": 1, "idle": 3, "offline": 0},
    }


def test_blocked_jobs_are_the_boxes_waiting_at_gate(res):
    """Xe blocked giữ đúng thùng đang chờ packer (packer_queued) trong order_status."""
    os_ = res.order_status
    gate = os_[os_["current_stage"] == "packer_queued"]
    jobs = res.state_doc["current_state"]["resource_detail"]["amr"]["blocked_jobs"]
    assert {(j["amr_id"], j["request_id"]) for j in jobs} == set(zip(gate["amr_id"], gate["request_id"]))


def test_arrival_min_stays_zero_created_offset_differs(res):
    """arrival_min theo contract = 0 cho mọi WIP (spec mục 2). Thời điểm tạo thật nằm ở created_offset_min:
    phút so với as_of, đơn tạo trước as_of thì âm; tạo khác lúc -> giá trị khác nhau."""
    s = res.state_doc["current_state"]
    as_of = pd.Timestamp(AS_OF)
    created = res.order_status.set_index("request_id")["created_at"]
    jobs = s["queued_jobs"] + s["active_jobs"]
    assert jobs and all(j["request"]["arrival_min"] == 0 for j in jobs)
    offset = {j["request"]["request_id"]: j["created_offset_min"] for j in jobs}
    for rid, off in offset.items():
        assert off == round((created[rid] - as_of).total_seconds() / 60, 2)
        assert off <= 0
    assert any(off < 0 for off in offset.values())
    for a in offset:
        for b in offset:
            if created[a] != created[b]:
                assert offset[a] != offset[b], (a, b)


def test_workers_available_excludes_break(res):
    p = res.state_doc["current_state"]["resource_detail"]["packer"]
    assert p["workers_on_shift"] == ["W04", "W05", "W06"]
    assert p["workers_available"] == ["W04", "W06"]                 # W05 nghỉ 14:05-14:20
    assert res.state_doc["current_state"]["resources"]["packer"] == {"total": 3, "busy": 0, "offline": 1}


def test_open_requests_point_in_time(res):
    os_ = res.order_status
    assert (os_["created_at"] <= pd.Timestamp(AS_OF)).all()
    assert set(os_["request_id"]) == {"PICK-00002", "PICK-00003", "PICK-00004", "PICK-00005", "PICK-00009"}
    # PICK-00002 giao lúc 14:12 (sau as_of) -> vẫn đang mở, đang trên AGV
    assert os_.set_index("request_id").loc["PICK-00002", "current_stage"] == "agv_active"


def test_queue_order_is_fifo(res):
    q = res.state_doc["current_state"]["queued_jobs"]
    assert [(j["stage"], j["request"]["request_id"]) for j in q] == [
        ("amr", "PICK-00005"), ("packer", "PICK-00009"), ("packer", "PICK-00003")]


def check_estimates_flagged(r):
    os_ = r.order_status
    est = os_[os_["est_remaining_min"].notna() | os_["stage_is_estimated"]]
    assert est["is_estimated"].all()
    assert (est["estimate_method"].str.len() > 0).all()
    for job in r.state_doc["current_state"]["active_jobs"]:
        assert job["is_estimated"] is True and job["estimate_method"]


def test_estimated_fields_are_flagged(res):
    check_estimates_flagged(res)
    assert res.quality["estimated"]["active_jobs_estimated"] == 2


def test_overdue_when_estimate_already_passed():
    r = run(FIX, "2026-10-21T14:25:00+07:00", cfg=CFG, write=False, verbose=False)
    row = r.order_status.set_index("request_id").loc["PICK-00004"]   # DISPATCH 14:07, chưa tới cổng
    assert row["overdue"] and row["est_remaining_min"] == 0
    job = next(j for j in r.state_doc["current_state"]["active_jobs"] if j["request"]["request_id"] == "PICK-00004")
    assert job["overdue"] is True and job["remaining_min"] == CFG["estimate"]["overdue_remaining_min"]
    # Định nghĩa: quá thời gian ước lượng -> est_remaining_min = 0 và overdue = true
    assert job["est_remaining_min"] == 0
    # Khâu AMR kết thúc bằng ARRIVE_GATE (stream) -> không phải "có thể đã xong mà chưa ghi"
    assert job["completion_unconfirmed"] is False
    check_active_job_flags(r.state_doc["current_state"])


# ---------- Bước 5: freshness ----------
def test_order_stale_after_threshold(res):
    os_ = res.order_status.set_index("request_id")
    assert os_.loc["PICK-00009", "stale"]                 # sự kiện cuối 13:05 -> 65 phút
    assert os_.loc["PICK-00009", "age_min"] == 65
    assert not os_.loc["PICK-00005", "stale"]


def test_source_stale_after_threshold():
    r = run(FIX, "2026-10-21T14:25:00+07:00", cfg=CFG, write=False, verbose=False)
    f = r.state_doc["current_state"]["source_freshness"]
    assert f["amr"]["last_update"] == "2026-10-21T14:07:30+07:00"
    assert f["amr"]["stale"] and f["amr"]["age_min"] == 17.5      # stream, ngưỡng 5 phút
    assert not f["excel"]["stale"]                                  # manual, ngưỡng 150 phút
    assert "amr" in r.quality["stale_sources"]


def test_feed_updated_at_never_after_as_of(res):
    fu = res.state_doc["current_state"]["feed_updated_at"]
    assert set(fu) == {"wms", "amr", "agv", "production", "labor", "excel"}
    assert all(pd.Timestamp(v) <= pd.Timestamp(AS_OF) for v in fu.values())


# ---------- Bước 7: contract_check ----------
def test_good_output_writes_gold(tmp_path):
    cfg = {**CFG, "silver_root": str(tmp_path / "silver"), "gold_root": str(tmp_path / "gold")}
    r = run(FIX, AS_OF, cfg=cfg, out_name="fx", verbose=False)
    files = sorted(p.name for p in (tmp_path / "gold" / "fx").iterdir())
    assert files == ["data_quality_1410.json", "journey.parquet", "order_status_1410.parquet",
                     "quarantine.csv", "state_1410.json"]
    assert (tmp_path / "silver" / "fx" / "quarantine.csv").exists()
    assert r.gold_dir is not None
    # Bản sao lần chạy gần nhất, tên file cố định cho Dashboard
    latest = tmp_path / "gold" / "latest"
    assert sorted(p.name for p in latest.iterdir()) == ["data_quality.json", "order_status.parquet", "state.json"]
    assert (latest / "state.json").read_text(encoding="utf-8") == \
        (tmp_path / "gold" / "fx" / "state_1410.json").read_text(encoding="utf-8")
    # Chạy mốc khác -> latest bị ghi đè bằng mốc mới
    run(FIX, "2026-10-21T14:25:00+07:00", cfg=cfg, out_name="fx", verbose=False)
    assert '"as_of": "2026-10-21T14:25:00+07:00"' in (latest / "state.json").read_text(encoding="utf-8")


@pytest.mark.parametrize("break_it", [
    lambda s: s["resources"]["amr"].update(busy=5),                          # busy != số active job
    lambda s: s["active_jobs"][0].update(remaining_min=0),                   # remaining_min phải > 0
    lambda s: s["queued_jobs"][0]["request"].update(quantity=0),             # quantity > 0
    lambda s: s["queued_jobs"][0]["request"].update(extra_field=1),          # Twin không nhận khóa lạ
    lambda s: s["feed_updated_at"].pop("excel"),                             # thiếu 1 nguồn
    lambda s: s["feed_updated_at"].update(wms="2026-10-21T15:00:00+07:00"),  # vượt as_of
    lambda s: s["active_jobs"][0].update(estimate_method=""),                # ước lượng không có cách tính
    lambda s: s["resource_detail"]["amr"]["idle"].append("AMR-99"),          # tổng xe không khớp total
    lambda s: s["resources"]["amr"].update(blocked=["AMR-01"]),              # khóa lạ trong resources
    lambda s: s["resources"]["amr"].update(blocked=2),                       # khóa lạ trong resources
    lambda s: s["queued_jobs"][0]["request"].update(arrival_min=-5),         # contract: arrival_min >= 0
    lambda s: s["active_jobs"][0].update(overdue=True),                      # overdue mà est_remaining_min > 0
    lambda s: s["active_jobs"][0].update(completion_unconfirmed=True),       # chưa overdue
    lambda s: s["queued_jobs"][0].update(created_offset_min=3),              # đơn mở không thể tạo sau as_of
    lambda s: s["resource_counts"]["amr"].update(idle=3),                    # tổng không khớp total
    lambda s: s["resource_detail"]["amr"]["blocked_jobs"].pop(),             # thiếu xe blocked
])
def test_bad_contract_raises_and_writes_nothing(res, tmp_path, break_it):
    doc = copy.deepcopy(res.state_doc)
    break_it(doc["current_state"])
    gold = tmp_path / "gold"
    with pytest.raises(ContractError):
        write_gold(gold, "1410", doc, res.order_status, res.journey, res.quarantine, res.quality)
    assert not gold.exists()


def test_state_loads_in_twin_dataclasses(res):
    """State gold phải nạp được bằng dataclass của Twin (src/twin/entities.py) — chỉ đọc, không sửa."""
    from src.twin.entities import ActiveJob, CurrentLogisticsState, MaterialRequest, QueuedJob, ResourceStatus
    s = res.state_doc["current_state"]
    CurrentLogisticsState(
        as_of=s["as_of"],
        resources={k: ResourceStatus(**v) for k, v in s["resources"].items()},
        queued_jobs=[QueuedJob(stage=j["stage"], request=MaterialRequest(**j["request"])) for j in s["queued_jobs"]],
        active_jobs=[ActiveJob(stage=j["stage"], remaining_min=j["remaining_min"],
                               request=MaterialRequest(**j["request"])) for j in s["active_jobs"]],
        feed_updated_at=s["feed_updated_at"],
    )


# ---------- Dữ liệu Generator thật: ngày 2026-10-21, seed 42 ----------
REAL_DATE = "2026-10-21"


@pytest.fixture(scope="module")
def real_dir(tmp_path_factory):
    """Sinh lại raw vào thư mục tạm -> test không phụ thuộc data/raw đang có gì trên máy."""
    from src.generator import generate_day
    from src.generator.world import load_config as gen_config
    out = tmp_path_factory.mktemp("raw")
    generate_day(seed=42, date=REAL_DATE, config=gen_config(ROOT / "config" / "generator.yaml"), out_dir=out)
    return out / REAL_DATE


def real_ts(hhmm: str) -> str:
    return f"{REAL_DATE}T{hhmm}:00+07:00"


def run_real(real_dir, as_of):
    return run(real_dir, as_of, cfg=CFG, write=False, verbose=False)


def test_real_link_rate_at_least_95(real_dir):
    assert run_real(real_dir, real_ts("14:10")).link_stats["overall"]["rate"] >= 0.95
    assert run_real(real_dir, real_ts("23:59")).link_stats["overall"]["rate"] >= 0.95


@pytest.mark.parametrize("hhmm", ["06:30", "10:05", "14:10", "17:55", "21:59"])
def test_real_state_invariants(real_dir, hhmm):
    r = run_real(real_dir, real_ts(hhmm))          # run() đã kiểm contract, sai là raise
    s = r.state_doc["current_state"]
    check_resource_sum(s)
    check_active_job_flags(s)
    check_estimates_flagged(r)
    assert (r.order_status["created_at"] <= pd.Timestamp(real_ts(hhmm))).all()
    # Trạng thái xe phải khớp đơn: AMR đang chở -> busy, AMR có thùng chờ ở cổng -> blocked
    os_ = r.order_status
    det = s["resource_detail"]["amr"]
    assert set(os_.loc[os_["current_stage"] == "amr_active", "amr_id"]) <= set(det["busy"])
    assert set(os_.loc[os_["current_stage"] == "packer_queued", "amr_id"]) == set(det["blocked"])
    agv_holding = os_.loc[os_["current_stage"] == "agv_active", "agv_id"]
    assert set(agv_holding) <= set(s["resource_detail"]["agv"]["busy"])
    gate = os_[os_["current_stage"] == "packer_queued"]
    assert {(j["amr_id"], j["request_id"]) for j in det["blocked_jobs"]} == set(zip(gate["amr_id"], gate["request_id"]))


def test_real_1410_overdue_packer_is_unconfirmed(real_dir):
    """14:10 ngày 21 seed 42: PICK-00210 đóng gói quá hạn ước lượng. Mốc 'đóng gói xong' chỉ có trong Excel
    (manual, trễ tới 2 giờ) -> est_remaining_min = 0, completion_unconfirmed = true."""
    s = run_real(real_dir, real_ts("14:10")).state_doc["current_state"]
    job = next(j for j in s["active_jobs"] if j["request"]["request_id"] == "PICK-00210")
    assert job["stage"] == "packer" and job["overdue"] is True
    assert job["est_remaining_min"] == 0 and job["completion_unconfirmed"] is True
    check_active_job_flags(s)


def _truth(real_dir):
    """CHỈ test được đọc _truth. Đổi phút-từ-06:00 sang giờ thật."""
    t = pd.read_csv(real_dir / "_truth" / "requests.csv").set_index("pick_no")
    start = pd.Timestamp(f"{REAL_DATE}T06:00:00+07:00")
    for c in ["wms_CREATED", "amr_DISPATCH", "amr_ARRIVE_GATE", "packer_START", "packer_FINISH",
              "agv_LOAD", "line_DELIVERED"]:
        t[c] = start + pd.to_timedelta(t[c], unit="min")
    return t


def test_real_timestamps_match_truth(real_dir):
    """Các đơn nối được phải có mốc khớp truth. Feed cắt giây (ISO/epoch) hoặc cắt phút (Excel HH:MM)."""
    j = run_real(real_dir, real_ts("23:59")).journey.set_index("request_id")
    t = _truth(real_dir).reindex(j.index)
    pairs = [("created_at", "wms_CREATED", 1), ("amr_dispatch_at", "amr_DISPATCH", 1),
             ("amr_arrive_gate_at", "amr_ARRIVE_GATE", 1), ("packer_start_at", "packer_START", 1),
             ("packer_end_at", "packer_FINISH", 60), ("agv_load_at", "agv_LOAD", 1),
             ("delivered_at", "line_DELIVERED", 1)]
    for ours, truth, tol_s in pairs:
        both = j[ours].notna() & t[truth].notna()
        assert both.sum() > 0, ours
        err = (t.loc[both, truth] - j.loc[both, ours]).dt.total_seconds()
        assert err.between(0, tol_s).all(), f"{ours}: lệch tối đa {err.abs().max()} giây"
        # Mốc nào truth có thì journey cũng phải có (trừ Excel cố ý thiếu ~5% dòng)
        if ours != "packer_end_at":
            assert j[ours].notna().sum() == t[truth].notna().sum(), ours
    has_task = j["amr_task_id"].notna()
    assert (j.loc[has_task, "amr_task_id"] == t.loc[has_task, "amr_task_id"]).all()


@pytest.mark.parametrize("hhmm", ["08:40", "10:05", "14:10", "19:05"])
def test_real_stage_matches_truth(real_dir, hhmm):
    """Khâu hiện tại tại as_of khớp truth. Chỉ cho phép một kiểu lệch: thùng đóng xong đang chờ AGV
    mà Excel chưa nhập -> Data Layer vẫn thấy 'packer_active'."""
    as_of = pd.Timestamp(real_ts(hhmm))
    r = run_real(real_dir, as_of)
    t = _truth(real_dir)
    t = t[t["wms_CREATED"] <= as_of]
    truth_stage = pd.Series(np.select(
        [t["line_DELIVERED"] <= as_of, t["agv_LOAD"] <= as_of, t["packer_FINISH"] <= as_of,
         t["packer_START"] <= as_of, t["amr_ARRIVE_GATE"] <= as_of, t["amr_DISPATCH"] <= as_of],
        ["delivered", "agv_active", "agv_queued", "packer_active", "packer_queued", "amr_active"],
        default="amr_queued"), index=t.index)
    ours = r.journey.set_index("request_id")["current_stage"]
    ours.update(r.order_status.set_index("request_id")["current_stage"])    # gồm cả khâu suy ra
    ours = ours.reindex(truth_stage.index)
    diff = truth_stage != ours
    allowed = (truth_stage == "agv_queued") & (ours == "packer_active")
    assert (allowed | ~diff).all(), pd.DataFrame({"truth": truth_stage[diff], "ours": ours[diff]})


def _faults(real_dir):
    amr = pd.read_csv(real_dir / "amr.csv")
    f = amr[amr["event"] == "FAULT"].reset_index(drop=True)
    rec = amr[amr["event"] == "RECOVER"].reset_index(drop=True)
    return [(a, pd.Timestamp(s), pd.Timestamp(e)) for a, s, e in zip(f["robot_id"], f["ts"], rec["ts"])]


def test_real_amr_fault_shows_offline(real_dir):
    """Với mọi lần hỏng có trong dữ liệu: giữa lúc hỏng thì offline, sau RECOVER thì không."""
    faults = _faults(real_dir)
    assert faults, "ngày 21 seed 42 phải có ít nhất 1 lần AMR hỏng"
    for robot, start, end in faults:
        during = run_real(real_dir, start + (end - start) / 2).state_doc["current_state"]
        after = run_real(real_dir, end + pd.Timedelta(minutes=1)).state_doc["current_state"]
        assert robot in during["resource_detail"]["amr"]["offline"]
        assert robot not in after["resource_detail"]["amr"]["offline"]


def test_real_amr03_breakdown_1405_1440(real_dir):
    """Kịch bản trong đề: AMR-03 hỏng 14:05-14:40. Generator hiện KHÔNG có kịch bản cố định này
    (hỏng ngẫu nhiên; ngày 21 seed 42 AMR-03 hỏng 06:47-06:57) -> bỏ qua nếu dữ liệu không có."""
    t1410, t1445 = pd.Timestamp(real_ts("14:10")), pd.Timestamp(real_ts("14:45"))
    hit = [f for f in _faults(real_dir) if f[0] == "AMR-03" and f[1] <= t1410 < f[2] <= t1445]
    if not hit:
        pytest.skip("Generator không sinh AMR-03 hỏng 14:05-14:40 ngày 21 seed 42")
    s1410 = run_real(real_dir, t1410).state_doc["current_state"]
    s1445 = run_real(real_dir, t1445).state_doc["current_state"]
    assert "AMR-03" in s1410["resource_detail"]["amr"]["offline"]
    assert "AMR-03" not in s1445["resource_detail"]["amr"]["offline"]


# ---------- Future invariance: dữ liệu sau as_of không được ảnh hưởng tới state ----------
# Phân loại feed ĐỘC LẬP với ingest.py (không import EVENT_TIME_COLUMNS) để test không tự xác nhận chính nó.
#   "event": feed sự kiện. `row_time` quyết định dòng đã tồn tại chưa (> as_of -> xóa dòng);
#            `event_cols` là các cột thời điểm sự kiện, dòng còn giữ mà ô > as_of -> để rỗng.
#   "plan":  kế hoạch công bố đầu ngày -> giữ nguyên toàn bộ.
FUTURE_FEEDS = {
    "wms": {"kind": "event", "row_time": "created_at", "event_cols": ["created_at"]},   # need_by = hạn, giữ
    "amr": {"kind": "event", "row_time": "ts", "event_cols": ["ts"]},
    "agv": {"kind": "event", "row_time": "load_epoch", "event_cols": ["load_epoch", "deliver_epoch"]},
    "excel": {"kind": "event", "row_time": "entered_at", "event_cols": ["start", "end", "entered_at"]},
    "production": {"kind": "plan"},
    "labor": {"kind": "plan"},
}
FUTURE_FORMATS = {"created_at": "iso", "ts": "iso", "entered_at": "iso", "load_epoch": "epoch",
                  "deliver_epoch": "epoch", "start": "hhmm", "end": "hhmm"}


def _raw_time(text: str, fmt: str, day: str):
    """Đọc 1 ô giờ thô. Ô rỗng/hỏng -> None (không biết là tương lai -> giữ nguyên ô)."""
    s = (text or "").strip()
    if not s:
        return None
    try:
        if fmt == "epoch":
            return pd.Timestamp(int(float(s)), unit="s", tz="UTC").tz_convert("Asia/Ho_Chi_Minh")
        if fmt == "hhmm":
            return pd.Timestamp(f"{day}T{s}").tz_localize("Asia/Ho_Chi_Minh")
        t = pd.Timestamp(s)
        return t.tz_localize("Asia/Ho_Chi_Minh") if t.tzinfo is None else t
    except (ValueError, TypeError, OverflowError):
        return None


def _truncate_raw(src: Path, dst: Path, as_of: pd.Timestamp) -> None:
    """Bản sao raw "như thể đang ở as_of": xóa dòng sự kiện tương lai, làm rỗng ô sự kiện tương lai."""
    import shutil
    dst.mkdir(parents=True)
    shutil.copy(src / "master.csv", dst / "master.csv")
    day = as_of.strftime("%Y-%m-%d")
    for name, spec in FUTURE_FEEDS.items():
        df = pd.read_csv(src / f"{name}.csv", dtype=str, keep_default_na=False)
        if spec["kind"] == "event":
            t = df[spec["row_time"]].map(lambda x: _raw_time(x, FUTURE_FORMATS[spec["row_time"]], day))
            df = df[[v is None or v <= as_of for v in t]].copy()
            for col in spec["event_cols"]:
                fut = df[col].map(lambda x: (v := _raw_time(x, FUTURE_FORMATS[col], day)) is not None and v > as_of)
                df.loc[fut, col] = ""
        df.to_csv(dst / f"{name}.csv", index=False)


def _json_diff(a, b, path="$") -> list[str]:
    """Liệt kê đường dẫn các trường khác nhau giữa 2 cây JSON."""
    if isinstance(a, dict) and isinstance(b, dict):
        out = []
        for k in sorted(set(a) | set(b), key=str):
            if k not in a or k not in b:
                out.append(f"{path}.{k}: {'thiếu bên gốc' if k not in a else 'thiếu bên bản cắt'}")
            else:
                out += _json_diff(a[k], b[k], f"{path}.{k}")
        return out
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return [f"{path}: độ dài {len(a)} != {len(b)}"]
        return [d for i, (x, y) in enumerate(zip(a, b)) for d in _json_diff(x, y, f"{path}[{i}]")]
    return [] if a == b else [f"{path}: {a!r} != {b!r}"]


@pytest.mark.parametrize("hhmm", ["09:00", "14:10", "16:30"])
def test_real_future_invariance(real_dir, tmp_path, hhmm):
    """State tại as_of trên raw gốc == state trên raw đã xóa sạch mọi thứ xảy ra sau as_of."""
    import json
    as_of = pd.Timestamp(real_ts(hhmm))
    cut_dir = tmp_path / REAL_DATE
    _truncate_raw(real_dir, cut_dir, as_of)

    full = json.loads(json.dumps(run_real(real_dir, as_of).state_doc, default=str))
    cut = json.loads(json.dumps(run_real(cut_dir, as_of).state_doc, default=str))
    diffs = _json_diff(full, cut)
    assert not diffs, f"{len(diffs)} trường khác nhau tại {hhmm}:\n" + "\n".join(diffs[:50])
