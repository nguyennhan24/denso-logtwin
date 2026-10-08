import simpy
from resources import make_resources
from process import material_flow
from entities import (
    ActiveJob,
    CurrentLogisticsState,
    MaterialRequest,
    ResourceStatus,
    ScenarioConfig,
    QueuedJob,
    SimulationResult,
)
from metrics import analyze_events
from copy import deepcopy

STAGES = ("amr", "packer", "agv")
from metrics import count_completion

def validate_inputs(config, demand, initial_state):
    # Kiểm tra những điều kiện liên quan đến cả một lần mô phỏng.
    if set(config.capacity) != set(STAGES):
        raise ValueError("Capacity must be amr, packer, agv")

    if set(config.service_min) != set(STAGES):
        raise ValueError("service_min must be amr, packer, agv")

    seen_ids = set()

    for request in demand:
        if not 0 <= request.arrival_min < config.horizon_min:
            raise ValueError(
                f"{request.request_id}: arrival_min must be in "
                f"[0, {config.horizon_min})"
            )

        if request.request_id in seen_ids:
            raise ValueError(f"Trùng request_id: {request.request_id}")
        seen_ids.add(request.request_id)

    if initial_state is None:
        return

    if set(initial_state.resources) != set(STAGES):
        raise ValueError("Resources must be amr, packer, agv")

    for stage in STAGES:
        active_count = sum(
            job.stage == stage for job in initial_state.active_jobs
        )
        reported_busy = initial_state.resources[stage].busy

        if active_count != reported_busy:
            raise ValueError(
                f"{stage}: busy={reported_busy} nhưng có "
                f"{active_count} active jobs"
            )

        if config.capacity[stage] < active_count:
            raise ValueError(
                f"{stage}: capacity={config.capacity[stage]} "
                f"nhỏ hơn số active={active_count}"
            )

    for job in initial_state.active_jobs:
        if job.stage not in STAGES or job.remaining_min <= 0:
            raise ValueError("ActiveJob có stage hoặc remaining_min không hợp lệ")

    for job in initial_state.queued_jobs:
        if job.stage not in STAGES:
            raise ValueError("QueuedJob có stage không hợp lệ")

    for job in (*initial_state.active_jobs, *initial_state.queued_jobs):
        request_id = job.request.request_id
        if request_id in seen_ids:
            raise ValueError(f"Trùng request_id: {request_id}")
        seen_ids.add(request_id)

def receive_request(env, request, pools, config, events):
    yield env.timeout(request.arrival_min)

    events.append({
        "time_min": env.now,
        "request_id": request.request_id,
        "stage": "amr",
        "event": "arrival",
    })

    yield env.process(
        material_flow(env, request, pools, config, events)
    )

def simulate_event(config, demand, initial_state=None):
    validate_inputs(config, demand, initial_state)

    env = simpy.Environment()
    pools = make_resources(env, config)
    events = []

    snapshot_queue_keys = set()

    if initial_state is not None:
        for job in initial_state.active_jobs:
            env.process(
                material_flow(
                    env, job.request, pools, config, events,
                    start_stage=job.stage,
                    remaining_min=job.remaining_min,
                )
            )   

        for job in initial_state.queued_jobs:
            key = (job.request.request_id, job.stage)
            snapshot_queue_keys.add(key)

            # Job đã ở trong queue tại phút 0.
            events.append({
                "time_min": 0,
                "request_id": job.request.request_id,
                "stage": job.stage,
                "event": "queued",
            })

            env.process(
                material_flow(
                    env, job.request, pools, config, events,
                    start_stage=job.stage,
                )
            )

    for request in sorted(
        demand,
        key=lambda item: (item.arrival_min, item.request_id),
    ):
        env.process(
            receive_request(env, request, pools, config, events)
        )

    # 3. Chạy mô phỏng
    while env.peek() <= config.horizon_min:
        env.step()

    result = []
    seen_snapshot_queue = set()
    for event in sorted(events, key=lambda item: item["time_min"]):
        if event["event"] == "queued" and event["time_min"] == 0:
            key = (event["request_id"], event["stage"])

            if key in snapshot_queue_keys:
                if key in seen_snapshot_queue:
                    continue
                seen_snapshot_queue.add(key)

        result.append(event)

    return result
def run(config, demand, initial_state=None):
    """Chạy mô phỏng và trả kết quả theo contract Phase 2."""

    # Tạo bản sao để các lần chạy không dùng chung dữ liệu mutable.
    config, demand, initial_state = deepcopy(
        (config, list(demand), initial_state)
    )

    # Chạy mô phỏng để thu event log.
    events = simulate_event(
        config,
        demand,
        initial_state=initial_state,
    )

    # Từ event log, tính KPI và dựng timeline.
    metrics, timeline = analyze_events(
        config,
        demand,
        initial_state,
        events,
    )

    # Đóng gói đầu ra theo contract.
    return SimulationResult(
        scenario_id=config.scenario_id,
        horizon_min=config.horizon_min,
        initial_state_used=initial_state is not None,
        input_count=len(demand),
        metrics=metrics,
        timeline=timeline,
        events=events,
    )

if __name__ == "__main__":
    def make_request(request_id, arrival_min=0):
        return MaterialRequest(
            request_id=request_id,
            arrival_min=arrival_min,
            origin_id="STORE-1",
            destination_line_id="L1",
            material_id="M01",
            quantity=1,
        )

    def event_times(events, request_id, stage, event_name):
        return [
            event["time_min"]
            for event in events
            if event["request_id"] == request_id
            and event["stage"] == stage
            and event["event"] == event_name
        ]

    as_of = "2026-10-01T10:00:00+07:00"
    feed_updated_at = {
        name: as_of
        for name in ("wms", "amr", "agv", "production", "labor", "excel")
    }

    # CA 1: Không có initial_state
    config_1 = ScenarioConfig(
        scenario_id="NO-WIP",
        horizon_min=20,
        sample_every_min=5,
        random_seed=42,
        capacity={"amr": 5, "packer": 3, "agv": 4},
        service_min={"amr": 4.0, "packer": 5.5, "agv": 3.0},
    )

    events_1 = simulate_event(
        config_1,
        [make_request("NEW-1", arrival_min=1.5)],
    )

    assert event_times(events_1, "NEW-1", "amr", "arrival") == [1.5]
    assert event_times(events_1, "NEW-1", "line", "delivered") == [14.0]
    print("CA 1: PASS - demand không có WIP")

    # CA 2: Một active AGV, một queued AGV
    config_2 = ScenarioConfig(
        scenario_id="AGV-WIP",
        horizon_min=20,
        sample_every_min=5,
        random_seed=42,
        capacity={"amr": 5, "packer": 3, "agv": 1},
        service_min={"amr": 4.0, "packer": 5.5, "agv": 3.0},
    )

    state_2 = CurrentLogisticsState(
        as_of=as_of,
        resources={
            "amr": ResourceStatus(total=5, busy=0, offline=0),
            "packer": ResourceStatus(total=3, busy=0, offline=0),
            "agv": ResourceStatus(total=1, busy=1, offline=0),
        },
        active_jobs=[
            ActiveJob(
                stage="agv",
                remaining_min=2.0,
                request=make_request("ACTIVE-AGV"),
            )
        ],
        queued_jobs=[
            QueuedJob(
                stage="agv",
                request=make_request("QUEUED-AGV"),
            )
        ],
        feed_updated_at=feed_updated_at,
    )

    events_2 = simulate_event(config_2, [], initial_state=state_2)

    assert event_times(
        events_2, "ACTIVE-AGV", "line", "delivered"
    ) == [2.0]
    assert event_times(
        events_2, "QUEUED-AGV", "agv", "queued"
    ) == [0]
    assert event_times(
        events_2, "QUEUED-AGV", "agv", "start"
    ) == [2.0]
    assert event_times(
        events_2, "QUEUED-AGV", "line", "delivered"
    ) == [5.0]
    print("CA 2: PASS - active trước queued, queued chỉ ghi một lần")

    # CA 3: WIP FIFO trước demand mới ở phút 0
    config_3 = ScenarioConfig(
        scenario_id="FIFO-CHECK",
        horizon_min=20,
        sample_every_min=5,
        random_seed=42,
        capacity={"amr": 1, "packer": 1, "agv": 1},
        service_min={"amr": 5.0, "packer": 4.0, "agv": 3.0},
    )

    state_3 = CurrentLogisticsState(
        as_of=as_of,
        resources={
            "amr": ResourceStatus(total=1, busy=1, offline=0),
            "packer": ResourceStatus(total=1, busy=0, offline=0),
            "agv": ResourceStatus(total=1, busy=0, offline=0),
        },
        active_jobs=[
            ActiveJob(
                stage="amr",
                remaining_min=3.0,
                request=make_request("REQ-OLD-003"),
            )
        ],
        queued_jobs=[
            QueuedJob(
                stage="amr",
                request=make_request("REQ-OLD-002"),
            ),
            QueuedJob(
                stage="amr",
                request=make_request("REQ-OLD-001"),
            ),
        ],
        feed_updated_at=feed_updated_at,
    )

    events_3 = simulate_event(
        config_3,
        [make_request("REQ-001", arrival_min=0)],
        initial_state=state_3,
    )

    amr_starts = [
        (event["request_id"], event["time_min"])
        for event in events_3
        if event["stage"] == "amr"
        and event["event"] == "start"
        and event["request_id"] in {
            "REQ-OLD-002", "REQ-OLD-001", "REQ-001"
        }
    ]

    assert amr_starts == [
        ("REQ-OLD-002", 3.0),
        ("REQ-OLD-001", 8.0),
        ("REQ-001", 13.0),
    ], amr_starts

    print("CA 3: PASS - WIP FIFO trước demand phút 0")
    print("TẤT CẢ CA KIỂM TRA ĐỀU PASS")
    
    assert count_completion(
        [make_request("NEW-1", arrival_min=1.5)],
        None,
        events_1,
    ) == {
        "throughput_count": 1,
        "unfinished_count": 0,
    }

    assert count_completion(
        [],
        state_2,
        events_2,
    ) == {
        "throughput_count": 2,
        "unfinished_count": 0,
    }

    assert count_completion(
        [make_request("REQ-001", arrival_min=0)],
        state_3,
        events_3,
    ) == {
        "throughput_count": 3,
        "unfinished_count": 1,
    }

    print("COUNT COMPLETION: PASS")

    demand_3 = [
        make_request("REQ-001", arrival_min=0)
    ]

    result = run(
        config_3,
        demand_3,
        initial_state=state_3,
    )

    assert isinstance(result, SimulationResult)
    assert result.initial_state_used is True
    assert result.input_count == 1

    assert result.metrics["throughput_count"] == 3
    assert result.metrics["unfinished_count"] == 1

    assert result.timeline[0]["time_min"] == 0
    assert result.timeline[-1]["time_min"] == config_3.horizon_min

    assert result.timeline[-1]["delivered_count"] == 3
    assert result.timeline[-1]["unfinished_arrived_count"] == 1

    print("\nMETRICS:")
    for name, value in result.metrics.items():
        print(name, ":", value)

    print("\nTIMELINE:")
    for snapshot in result.timeline:
        print(snapshot)

    print("\nSIMULATION RESULT: PASS")