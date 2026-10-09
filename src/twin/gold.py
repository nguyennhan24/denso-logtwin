"""Nạp snapshot gold v0.1 và chạy baseline với các đơn đang mở."""
import json
from pathlib import Path

from contracts.validate_contracts import check_bundle
from src.scenario.scenarios import build_baseline

from .entities import (
    ActiveJob, CurrentLogisticsState, MaterialRequest, QueuedJob,
    ResourceStatus, ScenarioConfig,
)
from .simulation import run


def load_gold_state(path: str | Path) -> CurrentLogisticsState:
    """Kiểm contract rồi chuyển state sang dataclass, giữ nguyên thứ tự WIP."""
    with open(path, encoding="utf-8") as stream:
        doc = json.load(stream)
    if doc.get("contract_version") != "0.1":
        raise ValueError("Twin chỉ hỗ trợ gold contract_version = 0.1")
    if not isinstance(doc.get("current_state"), dict):
        raise ValueError("Gold thiếu current_state")
    errors = check_bundle(doc)
    if errors:
        raise ValueError("Gold không hợp lệ: " + "; ".join(errors))

    state = doc["current_state"]
    # Metadata ước lượng/freshness vẫn ở gold; chỉ đưa trường v0.1 vào Twin.
    return CurrentLogisticsState(
        as_of=state["as_of"],
        resources={stage: ResourceStatus(**status)
                   for stage, status in state["resources"].items()},
        queued_jobs=[QueuedJob(stage=job["stage"], request=MaterialRequest(**job["request"]))
                     for job in state["queued_jobs"]],
        active_jobs=[ActiveJob(stage=job["stage"], remaining_min=job["remaining_min"],
                               request=MaterialRequest(**job["request"]))
                     for job in state["active_jobs"]],
        feed_updated_at=state["feed_updated_at"],
    )


def run_gold_baseline(path: str | Path, *, horizon_min: float = 60,
                      sample_every_min: float = 5, seed: int = 42,
                      service_min: dict[str, float] | None = None):
    """Dùng baseline của Decision (capacity = total - offline), chưa thêm demand tương lai."""
    state = load_gold_state(path)
    config = build_baseline(
        {"resources": {stage: {"total": status.total, "busy": status.busy,
                                "offline": status.offline}
                       for stage, status in state.resources.items()}},
        horizon_min=horizon_min, sample_every_min=sample_every_min,
        seed=seed, service_min=service_min,
    )
    return run(ScenarioConfig(**config), [], initial_state=state)
