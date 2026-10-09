from decimal import Decimal

STAGES = ("amr", "packer", "agv")

def count_completion(demand, initial_state, events):
    delivered_ids = {
        event["request_id"]
        for event in events
        if event["stage"] == "line"
        and event["event"] == "delivered"
    }

    wip_count = 0
    if initial_state is not None:
        wip_count = (
            len(initial_state.active_jobs)
            + len(initial_state.queued_jobs)
        )

    total_count = len(demand) + wip_count

    return {
        "throughput_count": len(delivered_ids),
        "unfinished_count": total_count - len(delivered_ids),
    }

def sample_times(horizon_min, sample_every_min):
    """Tạo các mốc lấy mẫu, luôn bao gồm phút 0 và horizon."""
    horizon = Decimal(str(horizon_min))
    step = Decimal(str(sample_every_min))

    if (
        not horizon.is_finite()
        or not step.is_finite()
        or horizon <= 0
        or step <= 0
    ):
        raise ValueError(
            "horizon_min và sample_every_min phải hữu hạn và > 0"
        )

    times = []
    time = Decimal("0")

    while time < horizon:
        times.append(float(time))
        time += step

    times.append(float(horizon))

    return times

def build_timeline(config, events, initial_state=None):
    # Sắp xếp theo thời gian, giữ thứ tự các event cùng timestamp.
    ordered_events = sorted(
        events,
        key=lambda event: event["time_min"],
    )

    queued = {stage: set() for stage in STAGES}
    busy = {stage: set() for stage in STAGES}

    arrived_ids = set()
    delivered_ids = set()

    # Nạp các job đã có sẵn tại thời điểm bắt đầu mô phỏng.
    if initial_state is not None:
        for job in initial_state.queued_jobs:
            request_id = job.request.request_id
            queued[job.stage].add(request_id)
            arrived_ids.add(request_id)

        for job in initial_state.active_jobs:
            request_id = job.request.request_id
            busy[job.stage].add(request_id)
            arrived_ids.add(request_id)

    timeline = []
    event_index = 0

    for time in sample_times(
        config.horizon_min,
        config.sample_every_min,
    ):
        # Xử lý toàn bộ sự kiện đến mốc lấy mẫu này.
        while (
            event_index < len(ordered_events)
            and ordered_events[event_index]["time_min"] <= time
        ):
            event = ordered_events[event_index]

            request_id = event["request_id"]
            stage = event["stage"]
            event_name = event["event"]

            if event_name == "arrival":
                arrived_ids.add(request_id)

            elif stage in STAGES:
                if event_name == "queued":
                    queued[stage].add(request_id)

                elif event_name == "start":
                    queued[stage].discard(request_id)
                    busy[stage].add(request_id)

                elif event_name == "finish":
                    busy[stage].discard(request_id)

            elif stage == "line" and event_name == "delivered":
                delivered_ids.add(request_id)

            event_index += 1

        timeline.append({
            "time_min": time,
            "queue_length": {
                stage: len(queued[stage])
                for stage in STAGES
            },
            "busy": {
                stage: len(busy[stage])
                for stage in STAGES
            },
            "delivered_count": len(delivered_ids),
            "unfinished_arrived_count": len(
                arrived_ids - delivered_ids
            ),
        })

    for point in timeline:
        point["queue"] = point["queue_length"]
        point["completed_count"] = point["delivered_count"]

    return timeline

def calculate_wait_times(config, events, initial_state=None):
    queue_entered = {}
    initial_active = set()

    wait_by_request = {}
    wait_by_request_stage = {}
    wait_by_stage = {stage: [] for stage in STAGES}
    delivered_ids = set()

    # WIP đang chờ: bắt đầu quan sát thời gian chờ từ phút 0.
    if initial_state is not None:
        for job in initial_state.queued_jobs:
            key = (job.request.request_id, job.stage)
            queue_entered[key] = 0.0

        for job in initial_state.active_jobs:
            key = (job.request.request_id, job.stage)
            initial_active.add(key)

    ordered_events = sorted(
        events,
        key=lambda event: event["time_min"],
    )

    for event in ordered_events:
        time = event["time_min"]

        if time > config.horizon_min:
            break

        request_id = event["request_id"]
        stage = event["stage"]
        event_name = event["event"]
        key = (request_id, stage)

        if stage in STAGES:
            # Lượt active đã bắt đầu trước snapshot.
            if key in initial_active and time == 0:
                continue

            if event_name == "queued":
                # Giữ thời điểm vào queue đầu tiên nếu log bị lặp.
                queue_entered.setdefault(key, time)

            elif event_name == "start":
                # Không có queued => bắt đầu ngay => wait = 0.
                queued_time = queue_entered.pop(key, time)
                wait = time - queued_time

                wait_by_stage[stage].append(wait)

                wait_by_request[request_id] = (
                    wait_by_request.get(request_id, 0.0) + wait
                )
                stage_waits = wait_by_request_stage.setdefault(
                    request_id, {stage: 0.0 for stage in STAGES}
                )
                stage_waits[stage] += wait

        elif stage == "line" and event_name == "delivered":
            delivered_ids.add(request_id)

    # Tổng chờ qua các stage, trung bình trên request đã giao.
    avg_wait_min = None

    if delivered_ids:
        total_wait = sum(
            wait_by_request.get(request_id, 0.0)
            for request_id in delivered_ids
        )
        avg_wait_min = total_wait / len(delivered_ids)

    avg_wait_by_stage_min = {
        stage: (
            sum(waits) / len(waits)
            if waits else None
        )
        for stage, waits in wait_by_stage.items()
    }

    # Thời gian đã chờ của các lượt vẫn chưa bắt đầu tại horizon.
    open_queue_wait_min = {
        stage: sum(
            config.horizon_min - entered_time
            for (_, queued_stage), entered_time in queue_entered.items()
            if queued_stage == stage
        )
        for stage in STAGES
    }

    return {
        "avg_total_wait_min": avg_wait_min,
        "avg_wait_by_stage_min": avg_wait_by_stage_min,
        "open_queue_wait_min": open_queue_wait_min,

        # Bottleneck: chỉ tính đóng góp chờ của các đơn đã giao.
        # Các stage chưa đi qua kể từ snapshot có đóng góp bằng 0.
        "avg_wait_contribution_by_stage_min": {
            stage: (
                sum(
                    wait_by_request_stage.get(rid, {}).get(stage, 0.0)
                    for rid in delivered_ids
                ) / len(delivered_ids)
                if delivered_ids else None
            )
            for stage in STAGES
        },
    }

def calculate_resource_metrics(config, events, initial_state=None):
    queued = {stage: set() for stage in STAGES}
    busy = {stage: set() for stage in STAGES}

    busy_area = {stage: 0.0 for stage in STAGES}
    queue_area = {stage: 0.0 for stage in STAGES}
    peak_queue = {stage: 0 for stage in STAGES}

    if initial_state is not None:
        for job in initial_state.queued_jobs:
            queued[job.stage].add(job.request.request_id)

        for job in initial_state.active_jobs:
            busy[job.stage].add(job.request.request_id)

    ordered_events = sorted(
        events,
        key=lambda event: event["time_min"],
    )

    previous_time = 0.0
    event_index = 0

    def accumulate_until(time):
        nonlocal previous_time

        duration = time - previous_time

        for stage in STAGES:
            busy_area[stage] += len(busy[stage]) * duration
            queue_area[stage] += len(queued[stage]) * duration

            if duration > 0:
                peak_queue[stage] = max(
                    peak_queue[stage],
                    len(queued[stage]),
                )

        previous_time = time

    while event_index < len(ordered_events):
        time = ordered_events[event_index]["time_min"]

        if time > config.horizon_min:
            break

        if time < 0:
            raise ValueError("time_min không được âm")

        # Trạng thái hiện tại tồn tại từ previous_time đến time.
        accumulate_until(time)

        # Xử lý toàn bộ event cùng timestamp.
        while (
            event_index < len(ordered_events)
            and ordered_events[event_index]["time_min"] == time
        ):
            event = ordered_events[event_index]

            stage = event["stage"]
            request_id = event["request_id"]
            event_name = event["event"]

            if stage in STAGES:
                if event_name == "queued":
                    queued[stage].add(request_id)

                elif event_name == "start":
                    queued[stage].discard(request_id)
                    busy[stage].add(request_id)

                elif event_name == "finish":
                    busy[stage].discard(request_id)

            event_index += 1

        for stage in STAGES:
            peak_queue[stage] = max(
                peak_queue[stage],
                len(queued[stage]),
            )

    # Tính cả khoảng từ event cuối đến horizon.
    accumulate_until(config.horizon_min)

    return {
        "utilization": {
            stage: (
                busy_area[stage]
                / (config.capacity[stage] * config.horizon_min)
            )
            for stage in STAGES
        },
        "avg_queue_length": {
            stage: queue_area[stage] / config.horizon_min
            for stage in STAGES
        },
        "peak_queue": peak_queue,
        "queue_length_end": {
            stage: len(queued[stage])
            for stage in STAGES
        },
        "busy_end": {
            stage: len(busy[stage])
            for stage in STAGES
        },
    }

def calculate_lead_times(demand, initial_state, events):
    """Lead time của đơn đã giao; WIP tính từ snapshot."""
    arrivals = {
        request.request_id: request.arrival_min
        for request in demand
    }

    if initial_state is not None:
        for job in (
            *initial_state.active_jobs,
            *initial_state.queued_jobs,
        ):
            arrivals[job.request.request_id] = 0.0

    delivered = {}
    for event in events:
        if event["stage"] == "line" and event["event"] == "delivered":
            # Một request chỉ được tính hoàn thành một lần.
            delivered.setdefault(
                event["request_id"], event["time_min"]
            )

    return {
        "avg_lead_time_min": (
            sum(
                time - arrivals[rid]
                for rid, time in delivered.items()
            ) / len(delivered)
            if delivered else None
        )
    }

def analyze_events(config, demand, initial_state, events):
    """Gom các KPI và timeline, trả về (metrics, timeline)."""

    # Chỉ tính event trong horizon, kể cả đúng tại horizon.
    horizon_events = [
        event for event in events
        if 0 <= event["time_min"] <= config.horizon_min
    ]

    # 1. Đếm số request hoàn thành và chưa hoàn thành.
    metrics = count_completion(
        demand, initial_state, horizon_events
    )

    # 2. Bổ sung các chỉ số thời gian chờ.
    metrics.update(
        calculate_wait_times(
            config, horizon_events, initial_state
        )
    )
    metrics.update(
        calculate_lead_times(
            demand, initial_state, horizon_events
        )
    )
    
    # 3. Bổ sung utilization và các chỉ số queue.
    metrics.update(
        calculate_resource_metrics(
            config, horizon_events, initial_state
        )
    )

    metrics["bottleneck"] = detect_bottleneck(metrics)

    # 4. Dựng trạng thái tại từng mốc lấy mẫu.
    timeline = build_timeline(
        config, horizon_events, initial_state
    )

    return metrics, timeline

def detect_bottleneck(metrics, thresholds=None):
    """Một logic bottleneck dùng chung cho Twin và Decision."""
    if thresholds is None:
        from pathlib import Path
        import yaml

        path = (
            Path(__file__).resolve().parents[2]
            / "config"
            / "scenarios.yaml"
        )
        with path.open(encoding="utf-8") as stream:
            thresholds = yaml.safe_load(stream)["bottleneck"]

    utils = metrics["utilization"]
    queues = metrics["peak_queue"]

    contributions = metrics.get(
        "avg_wait_contribution_by_stage_min", {}
    )
    waits = {
        stage: contributions.get(stage)
        for stage in STAGES
    }

    stressed = sorted(
        (
            stage for stage in STAGES
            if utils[stage] >= thresholds["utilization_stressed"]
        ),
        key=lambda stage: utils[stage],
        reverse=True,
    )

    primary = stressed[0] if stressed else None
    secondary = None
    parts = []

    if primary is None:
        parts.append(
            "Không có stage vượt ngưỡng utilization bottleneck."
        )
    else:
        parts.append(
            f"{primary.upper()} là bottleneck chính: "
            f"utilization {utils[primary]:.0%}, "
            f"peak queue {queues[primary]}."
        )

        if waits[primary] is not None:
            parts.append(
                "Đóng góp thời gian chờ trung bình: "
                f"{waits[primary]:.2f} phút/đơn đã giao."
            )

        if (
            len(stressed) > 1
            and utils[primary] - utils[stressed[1]]
            <= thresholds["secondary_gap"]
        ):
            secondary = stressed[1]
            parts.append(
                f"{secondary.upper()} là bottleneck phụ."
            )

        for stage in STAGES:
            if (
                stage != primary
                and queues[stage] > queues[primary]
                and queues[stage] >= thresholds["peak_queue_severe"]
            ):
                parts.append(
                    f"Peak queue tại {stage.upper()} "
                    "gợi ý ùn tắc cục bộ."
                )

    return {
        "primary": primary,
        "secondary": secondary,
        "utilizations": utils,
        "peak_queues": queues,
        "wait_per_stage": waits,
        "explanation": " ".join(parts),
    }
