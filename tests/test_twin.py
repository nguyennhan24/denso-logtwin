"""Kiểm tra hành vi và contract của core Twin Phase 2."""
import copy
import json
import math
import sys
from dataclasses import asdict
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.twin.entities import (
    ActiveJob, CurrentLogisticsState, MaterialRequest,
    QueuedJob, ResourceStatus, ScenarioConfig,
)
from src.twin.simulation import run


def make_request(request_id, arrival_min=0):
    return MaterialRequest(request_id, arrival_min, "STORE-1", "L1", "M01", 1)


def make_config(horizon_min=20, sample_every_min=5, capacity=None, service_min=None):
    return ScenarioConfig(
        "BASELINE", horizon_min, sample_every_min, 42,
        capacity or {"amr": 1, "packer": 1, "agv": 1},
        service_min or {"amr": 5.0, "packer": 4.0, "agv": 3.0},
    )


def make_state(active_jobs=(), queued_jobs=(), total=None):
    as_of = "2026-10-01T10:00:00+07:00"
    total = total or {"amr": 1, "packer": 1, "agv": 1}
    return CurrentLogisticsState(
        as_of=as_of,
        resources={stage: ResourceStatus(
            total[stage], sum(job.stage == stage for job in active_jobs), 0,
        ) for stage in total},
        queued_jobs=list(queued_jobs), active_jobs=list(active_jobs),
        feed_updated_at={name: as_of for name in
            ("wms", "amr", "agv", "production", "labor", "excel")},
    )


def starts(result, stage):
    return [(event["request_id"], event["time_min"])
        for event in result.events
        if event["stage"] == stage and event["event"] == "start"]


def test_fixture_matches_contract():
    doc = json.loads((ROOT / "contracts_v0.1_example.json").read_text(encoding="utf-8"))
    result = run(ScenarioConfig(**doc["scenario_config"]),
        [MaterialRequest(**request) for request in doc["material_requests"]])
    for key, expected in doc["simulation_result"]["metrics"].items():
        assert result.metrics[key] == expected
    assert len(result.timeline) == len(doc["simulation_result"]["timeline"])
    for got, expected in zip(result.timeline, doc["simulation_result"]["timeline"]):
        assert all(got[key] == value for key, value in expected.items())
    assert result.events == doc["simulation_result"]["events"]


def test_new_simultaneous_arrivals_sorted_by_id():
    result = run(make_config(), [make_request("Z"), make_request("A")])
    assert starts(result, "amr") == [("A", 0), ("Z", 5)]


@pytest.mark.parametrize("stage", ["packer", "agv"])
def test_simultaneous_downstream_arrivals_sorted_by_id(stage):
    # Z được đăng ký trước nhưng A và Z cùng tới stage đích một phút.
    if stage == "packer":
        capacity = {"amr": 2, "packer": 1, "agv": 2}
        service_min = {"amr": 5, "packer": 1, "agv": 1}
        active_jobs = [ActiveJob("amr", 5, make_request("Z"))]
        expected = [("A", 5), ("Z", 6)]
    else:
        capacity = {"amr": 1, "packer": 2, "agv": 1}
        service_min = {"amr": 1, "packer": 3, "agv": 2}
        active_jobs = [ActiveJob("packer", 4, make_request("Z"))]
        expected = [("A", 4), ("Z", 6)]
    result = run(make_config(capacity=capacity, service_min=service_min),
        [make_request("A")], make_state(active_jobs, total=capacity))
    assert starts(result, stage) == expected


def test_wip_fifo_before_new_demand():
    state = make_state(
        [ActiveJob("amr", 3, make_request("OLD-003"))],
        [QueuedJob("amr", make_request("OLD-002")),
         QueuedJob("amr", make_request("OLD-001"))],
    )
    result = run(make_config(), [make_request("NEW")], state)
    assert starts(result, "amr") == [
        ("OLD-003", 0), ("OLD-002", 3), ("OLD-001", 8), ("NEW", 13)]
    assert result.metrics["throughput_count"] == 3
    assert result.metrics["unfinished_count"] == 1
    for request_id in ("OLD-002", "OLD-001"):
        assert sum(event["request_id"] == request_id
            and event["stage"] == "amr" and event["event"] == "queued"
            for event in result.events) == 1


def test_increased_capacity_still_prioritizes_wip():
    state = make_state(
        [ActiveJob("amr", 3, make_request("ACTIVE"))],
        [QueuedJob("amr", make_request("Z")), QueuedJob("amr", make_request("A"))],
    )
    result = run(make_config(capacity={"amr": 2, "packer": 1, "agv": 1}),
        [make_request("B")], state)
    assert starts(result, "amr") == [("ACTIVE", 0), ("Z", 0), ("A", 3), ("B", 5)]


def test_interleaved_wip_preserves_each_stage_order():
    capacity = {"amr": 2, "packer": 2, "agv": 2}
    state = make_state(queued_jobs=[
        QueuedJob("packer", make_request("Z-P")),
        QueuedJob("amr", make_request("Z-A")),
        QueuedJob("packer", make_request("A-P")),
        QueuedJob("amr", make_request("A-A")),
    ], total=capacity)
    result = run(make_config(capacity=capacity), [], state)
    assert starts(result, "amr")[:2] == [("Z-A", 0), ("A-A", 0)]
    assert starts(result, "packer")[:2] == [("Z-P", 0), ("A-P", 0)]


def test_existing_queue_precedes_new_arrival_at_release():
    result = run(make_config(), [
        make_request("FIRST"), make_request("Z", 1), make_request("A", 5),
    ])
    assert starts(result, "amr") == [("FIRST", 0), ("Z", 5), ("A", 10)]


def test_existing_downstream_queue_precedes_new_arrival():
    capacity = {"amr": 1, "packer": 1, "agv": 1}
    state = make_state(
        [ActiveJob("agv", 5, make_request("ACTIVE"))],
        [QueuedJob("agv", make_request("Z"))],
    )
    result = run(make_config(capacity=capacity,
        service_min={"amr": 2, "packer": 3, "agv": 2}), [make_request("A")], state)
    assert starts(result, "agv") == [("ACTIVE", 0), ("Z", 5), ("A", 7)]


@pytest.mark.parametrize("stage,delivered_min", [("amr", 9), ("packer", 5), ("agv", 2)])
def test_active_job_uses_remaining_time(stage, delivered_min):
    result = run(make_config(), [],
        make_state([ActiveJob(stage, 2, make_request("WIP"))]))
    assert result.metrics["throughput_count"] == 1
    assert result.metrics["avg_lead_time_min"] == delivered_min
    assert result.metrics["avg_total_wait_min"] == 0
    assert [event["time_min"] for event in result.events
        if event["event"] == "delivered"] == [delivered_min]


def test_immediate_service_has_no_queued_event():
    result = run(make_config(), [make_request("A")])
    assert all(event["event"] != "queued" for event in result.events)


def test_empty_result_has_null_averages():
    result = run(make_config(), [])
    assert result.metrics["throughput_count"] == result.metrics["unfinished_count"] == 0
    assert result.metrics["avg_total_wait_min"] is None
    assert result.metrics["avg_lead_time_min"] is None
    assert all(value == 0 for value in result.metrics["utilization"].values())


def test_delivered_exactly_at_horizon_is_counted():
    result = run(make_config(horizon_min=12, sample_every_min=3), [make_request("A")])
    assert result.metrics["throughput_count"] == 1
    assert result.timeline[-1]["completed_count"] == 1
    assert result.events[-1]["time_min"] == 12


def test_unfinished_job_contributes_busy_until_horizon():
    result = run(make_config(horizon_min=10), [make_request("A")])
    assert result.metrics["throughput_count"] == 0
    assert result.metrics["unfinished_count"] == 1
    assert result.metrics["avg_lead_time_min"] is None
    assert math.isclose(result.metrics["utilization"]["agv"], 0.1)


def test_open_queue_wait_at_horizon():
    result = run(make_config(horizon_min=5,
        service_min={"amr": 10, "packer": 1, "agv": 1}),
        [make_request("A"), make_request("B")])
    assert result.metrics["open_queue_wait_min"]["amr"] == 5
    assert result.metrics["queue_length_end"]["amr"] == 1
    assert result.metrics["utilization"]["amr"] == 1


def test_peak_between_samples_and_sample_invariance():
    demand = [make_request("A"), make_request("B", 2)]
    result = run(make_config(), demand)
    other = run(make_config(sample_every_min=2), demand)
    assert result.metrics == other.metrics
    assert result.metrics["peak_queue"]["amr"] == 1
    assert all(point["queue"]["amr"] == 0 for point in result.timeline)


def test_repeat_scenario_runs_do_not_mutate_input():
    config, demand, state = make_config(), [make_request("A"), make_request("B", 2)], make_state()
    before = copy.deepcopy((config, demand, state))
    baseline = run(config, demand, state)
    changed = copy.deepcopy(config)
    changed.capacity["amr"] = 2
    run(changed, demand, state)
    assert run(config, demand, state) == baseline
    assert (config, demand, state) == before


def test_json_roundtrip():
    result = run(make_config(), [])
    payload = json.loads(json.dumps(asdict(result), allow_nan=False))
    assert payload["metrics"]["avg_total_wait_min"] is None
    assert payload["metrics"]["avg_lead_time_min"] is None
    assert payload["timeline"][-1]["completed_count"] == 0


@pytest.mark.parametrize("arrival_min,quantity", [(float("nan"), 1), (float("inf"), 1), (-1, 1), (0, True), (0, 1.5), (0, 0)])
def test_invalid_request_rejected(arrival_min, quantity):
    with pytest.raises(ValueError):
        MaterialRequest("A", arrival_min, "STORE-1", "L1", "M01", quantity)


@pytest.mark.parametrize("changes", [
    {"horizon_min": float("inf")}, {"horizon_min": float("nan")},
    {"horizon_min": 12}, {"capacity": {"amr": 1.5, "packer": 1, "agv": 1}},
    {"capacity": {"amr": True, "packer": 1, "agv": 1}},
    {"service_min": {"amr": float("inf"), "packer": 1, "agv": 1}},
])
def test_invalid_config_rejected(changes):
    with pytest.raises(ValueError):
        make_config(**changes)


def test_mutated_config_is_validated_again():
    config = make_config()
    config.horizon_min = float("inf")
    with pytest.raises(ValueError):
        run(config, [])


def test_duplicate_ids_rejected():
    with pytest.raises(ValueError):
        run(make_config(), [make_request("A"), make_request("A")])
    with pytest.raises(ValueError):
        run(make_config(), [make_request("A")],
            make_state(queued_jobs=[QueuedJob("amr", make_request("A"))]))


def test_capacity_below_busy_rejected():
    state = make_state([
        ActiveJob("amr", 2, make_request("A")),
        ActiveJob("amr", 2, make_request("B")),
    ], total={"amr": 2, "packer": 1, "agv": 1})
    with pytest.raises(ValueError):
        run(make_config(), [], state)


@pytest.mark.parametrize("invalid", ["wip_arrival", "duplicate_wip", "missing_feed", "no_timezone", "future_feed", "busy_mismatch"])
def test_invalid_state_rejected(invalid):
    state = make_state()
    values = {name: getattr(state, name) for name in (
        "as_of", "resources", "queued_jobs", "active_jobs", "feed_updated_at")}
    if invalid == "wip_arrival":
        values["queued_jobs"] = [QueuedJob("amr", make_request("A", 1))]
    elif invalid == "duplicate_wip":
        values["queued_jobs"] = [QueuedJob("amr", make_request("A")), QueuedJob("agv", make_request("A"))]
    elif invalid == "missing_feed":
        values["feed_updated_at"] = {}
    elif invalid == "no_timezone":
        values["as_of"] = "2026-10-01T10:00:00"
    elif invalid == "future_feed":
        values["feed_updated_at"] = dict(state.feed_updated_at, wms="2026-10-01T11:00:00+07:00")
    else:
        values["resources"] = dict(state.resources, amr=ResourceStatus(1, 1, 0))
    with pytest.raises(ValueError):
        CurrentLogisticsState(**values)


@pytest.mark.parametrize("slots", [1, 2, 4])
def test_conservation_and_capacity_under_load(slots):
    config = make_config(horizon_min=60,
        capacity={"amr": slots, "packer": slots, "agv": slots})
    demand = [make_request(f"REQ-{i:03}", i // 3) for i in range(45)]
    result = run(config, demand)
    assert result.metrics["throughput_count"] + result.metrics["unfinished_count"] == len(demand)
    assert result.metrics["unfinished_count"] == (
        sum(result.metrics["queue_length_end"].values()) + sum(result.metrics["busy_end"].values()))
    assert result.timeline[-1]["completed_count"] == result.metrics["throughput_count"]
    assert all(0 <= value <= 1 for value in result.metrics["utilization"].values())
    busy = {stage: set() for stage in config.capacity}
    for event in result.events:
        if event["stage"] not in busy:
            continue
        users = busy[event["stage"]]
        if event["event"] == "start":
            users.add(event["request_id"])
        elif event["event"] == "finish":
            users.remove(event["request_id"])
        assert len(users) <= slots
