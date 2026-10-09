import pytest

from src.twin.entities import (
    ActiveJob,
    CurrentLogisticsState,
    MaterialRequest,
    QueuedJob,
    ResourceStatus,
    ScenarioConfig,
)
from src.twin.simulation import run


def request(rid, arrival=0):
    return MaterialRequest(
        request_id=rid,
        arrival_min=arrival,
        origin_id="WH-01",
        destination_line_id="LINE-01",
        material_id="MAT-01",
        quantity=1,
    )


def config():
    return ScenarioConfig(
        scenario_id="BASELINE",
        horizon_min=20,
        sample_every_min=5,
        random_seed=42,
        capacity={"amr": 1, "packer": 1, "agv": 1},
        service_min={"amr": 5, "packer": 4, "agv": 3},
    )


def test_contract_kpis():
    result = run(
        config(),
        [request("REQ-001"), request("REQ-002", 2)],
    )
    metrics = result.metrics

    assert metrics["throughput_count"] == 2
    assert metrics["avg_total_wait_min"] == pytest.approx(1.5)
    assert metrics["avg_lead_time_min"] == pytest.approx(13.5)
    assert metrics["peak_queue"] == {
        "amr": 1, "packer": 0, "agv": 0
    }
    assert result.timeline[-1]["completed_count"] == 2


def test_no_delivery_has_null_time_kpis():
    result = run(config(), [])

    assert result.metrics["avg_total_wait_min"] is None
    assert result.metrics["avg_lead_time_min"] is None


def test_wip_times_start_at_snapshot():
    state = CurrentLogisticsState(
        as_of="2026-10-21T14:10:00+07:00",
        resources={
            "amr": ResourceStatus(1, 0, 0),
            "packer": ResourceStatus(1, 0, 0),
            "agv": ResourceStatus(1, 1, 0),
        },
        active_jobs=[
            ActiveJob("agv", 2, request("ACTIVE")),
        ],
        queued_jobs=[
            QueuedJob("agv", request("QUEUED")),
        ],
        feed_updated_at={},
    )

    result = run(config(), [], initial_state=state)

    assert result.metrics["avg_lead_time_min"] == pytest.approx(3.5)
    assert result.metrics["avg_total_wait_min"] == pytest.approx(1)
    assert (
        result.metrics["avg_wait_contribution_by_stage_min"]["agv"]
        == pytest.approx(1)
    )