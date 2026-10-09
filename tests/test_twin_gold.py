"""Kiểm tra đường nối Data Layer -> gold -> Twin của Sync 2."""
import json
from dataclasses import asdict
from pathlib import Path

import pytest

from src.twin.gold import load_gold_state, run_gold_baseline

ROOT = Path(__file__).resolve().parents[1]


def test_real_gold_runs_in_twin(tmp_path):
    from src.data_layer.pipeline import run

    snapshot = run(ROOT / "tests/fixtures/raw_small", "2026-10-21T14:10:00+07:00",
                   write=False, verbose=False)
    path = tmp_path / "state.json"
    path.write_text(json.dumps(snapshot.state_doc), encoding="utf-8")
    state = load_gold_state(path)
    original = snapshot.state_doc["current_state"]
    assert [j.request.request_id for j in state.queued_jobs] == [
        j["request"]["request_id"] for j in original["queued_jobs"]
    ]
    before = asdict(state)
    result = run_gold_baseline(path)
    assert asdict(load_gold_state(path)) == before
    assert result.initial_state_used is True
    assert result.input_count == 0
    assert result.metrics["throughput_count"] + result.metrics["unfinished_count"] == (
        len(state.queued_jobs) + len(state.active_jobs)
    )
    assert result.timeline[-1]["time_min"] == 60
    for row in result.timeline:
        for stage, resource in state.resources.items():
            assert row["busy"][stage] <= resource.total - resource.offline
    assert asdict(result) == asdict(run_gold_baseline(path))


def test_gold_keeps_queue_order_and_excludes_offline(tmp_path):
    # Một AMR offline: hai job cùng lúc phải đi lần lượt trên AMR còn lại.
    doc = json.loads((ROOT / "contracts/mock_v0_1.json").read_text(encoding="utf-8"))
    state = doc["current_state"]
    state["active_jobs"] = []
    state["resources"] = {stage: {"total": 2 if stage == "amr" else 1,
                                   "busy": 0, "offline": 1 if stage == "amr" else 0}
                          for stage in ("amr", "packer", "agv")}
    request = state["queued_jobs"][0]["request"]
    state["queued_jobs"] = [
        {"stage": "amr", "request": {**request, "request_id": rid}}
        for rid in ("Z-FIRST", "A-SECOND")
    ]
    state["resource_counts"] = {"amr": {"idle": 1}}  # Metadata không phải trường dataclass.
    state["queued_jobs"][0]["created_offset_min"] = -10
    path = tmp_path / "state.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    result = run_gold_baseline(path, service_min={"amr": 4, "packer": 5.5, "agv": 3})
    starts = [(e["request_id"], e["time_min"]) for e in result.events
              if e["stage"] == "amr" and e["event"] == "start"]
    assert starts == [("Z-FIRST", 0), ("A-SECOND", 4)]


@pytest.mark.parametrize("fault", ["version", "missing", "duplicate", "busy"])
def test_invalid_gold_is_rejected(tmp_path, fault):
    doc = json.loads((ROOT / "contracts/mock_v0_1.json").read_text(encoding="utf-8"))
    if fault == "version":
        doc["contract_version"] = "0.2"
    elif fault == "missing":
        del doc["current_state"]
    elif fault == "duplicate":
        doc["current_state"]["queued_jobs"].append(doc["current_state"]["queued_jobs"][0])
    else:
        doc["current_state"]["resources"]["amr"]["busy"] = 0
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(doc), encoding="utf-8")
    with pytest.raises(ValueError):
        run_gold_baseline(path)
