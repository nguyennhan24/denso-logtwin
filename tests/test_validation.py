from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from src.validation import phase8
from src.validation.backtest import build_actual, TIME_COLUMNS
from src.validation.metrics import calculate_error


def test_train_test_dates_and_seeds_are_disjoint():
    plan = phase8.make_plan("2026-10-01", 20, 5, 42)
    train = [
        item for item in plan
        if item["split"] == "train"
    ]
    test = [
        item for item in plan
        if item["split"] == "test"
    ]

    assert len(train) == 20 and len(test) == 5
    assert not (
        {x["date"] for x in train}
        & {x["date"] for x in test}
    )
    assert not (
        {x["seed"] for x in train}
        & {x["seed"] for x in test}
    )
    assert len({x["seed"] for x in test}) == 5


def test_rejects_too_few_test_days():
    with pytest.raises(ValueError):
        phase8.make_plan("2026-10-01", 20, 4, 42)


def test_calibration_rejects_test_data():
    with pytest.raises(ValueError):
        phase8.calibrate(
            [{"split": "test"}],
            None,
            None,
        )


def test_calibration_uses_only_train_journey(monkeypatch):
    calls = []
    journey = pd.DataFrame({
        "amr_travel_min": [2, 4, 0],
        "pack_min": [4, 8, 0],
        "agv_travel_min": [3, 5, 0],
    })

    def snapshot(path, at, write, verbose):
        assert write is False
        calls.append(path.name)
        return SimpleNamespace(
            journey=journey.copy()
        )

    monkeypatch.setattr(
        phase8,
        "build_snapshot",
        snapshot,
    )

    train = phase8.make_plan(
        "2026-10-01", 2, 5, 42
    )[:2]
    world = {
        "day_start": "06:00",
        "day_hours": 16,
        "tz_offset_hours": 7,
    }

    result = phase8.calibrate(
        train,
        Path("raw"),
        world,
    )

    assert calls == ["2026-10-01", "2026-10-02"]
    assert result["service_min"] == {
        "amr": 3,
        "packer": 6,
        "agv": 4,
    }
    assert result["sample_counts"] == dict.fromkeys(
        phase8.STAGES,
        4,
    )


def test_actual_busy_includes_blocking_return_and_offline():
    events = pd.DataFrame([
        (0, "amr", "DISPATCH", "A"),
        (1, "amr", "ARRIVE_GATE", "A"),
        (2, "amr", "RELEASE", "A"),
        (4, "amr", "RETURNED", "A"),
        (5, "amr", "FAULT", "A"),
        (7, "amr", "RECOVER", "A"),
        (0, "packer", "START", "P"),
        (2, "packer", "FINISH", "P"),
        (3, "packer", "BREAK_START", "P"),
        (4, "packer", "BREAK_END", "P"),
        (0, "agv", "LOAD", "G"),
        (3, "agv", "RETURNED", "G"),
    ], columns=["t_min", "stage", "event", "unit"])

    actual = phase8.actual_utilizations(
        events,
        dict.fromkeys(phase8.STAGES, 1),
        10,
    )

    assert actual["amr"] == pytest.approx(4 / 8)
    assert actual["packer"] == pytest.approx(2 / 9)
    assert actual["agv"] == pytest.approx(3 / 10)


def test_summary_preserves_missing_and_zero_denominators():
    windows = [
        {
            "errors": {
                "throughput_count": calculate_error(a, p)
            }
        }
        for a, p in [(0, 5), (10, 12), (None, 1)]
    ]

    value = phase8.summarize(windows)["throughput_count"]

    assert value["mae"] == 3.5
    assert value["mape_pct"] == 20
    assert value["valid_count"] == 2
    assert value["percentage_count"] == 1
    assert value["total_count"] == 3


def test_actual_wip_uses_wait_and_lead_after_snapshot():
    truth = pd.DataFrame(
        [
            [0, 2, 4, 5, 7, 8, 10],
            [6, 6, 8, 8, 10, 10, 12],
        ],
        columns=TIME_COLUMNS,
        dtype=float,
    )

    metrics, timeline = build_actual(
        truth,
        3,
        8,
        2,
    )

    assert metrics["throughput_count"] == 1
    assert metrics["unfinished_count"] == 1
    assert metrics["avg_total_wait_min"] == 2
    assert metrics["avg_lead_time_min"] == 7
    assert metrics["avg_queue_length"]["packer"] == pytest.approx(1 / 8)
    assert timeline[-1]["completed_count"] == 1