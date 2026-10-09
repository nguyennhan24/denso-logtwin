"""Phase 8: backtest baseline bằng demand thực tế và truth của Generator."""

import argparse
from dataclasses import asdict
import json
from math import isfinite
from pathlib import Path

import pandas as pd
import yaml

from src.data_layer.mapper import normalize_request_id
from src.scenario.scenarios import build_baseline
from src.twin.entities import MaterialRequest, ScenarioConfig
from src.twin.gold import load_gold_state
from src.twin.metrics import sample_times
from src.twin.simulation import run as run_twin

from .metrics import compare_metrics


# Mỗi stage: thời điểm vào queue và thời điểm bắt đầu xử lý.
STAGE_TIMES = {
    "amr": ("arrival_min", "amr_DISPATCH"),
    "packer": ("amr_ARRIVE_GATE", "packer_START"),
    "agv": ("packer_FINISH", "agv_LOAD"),
}

TIME_COLUMNS = (
    "arrival_min",
    "amr_DISPATCH",
    "amr_ARRIVE_GATE",
    "packer_START",
    "packer_FINISH",
    "agv_LOAD",
    "line_DELIVERED",
)


def load_truth(path):
    """Đọc truth, kiểm ID và thứ tự các mốc thời gian."""
    truth = pd.read_csv(path)

    required = {
        "pick_no",
        "origin_id",
        "line_id",
        "material_id",
        "packs",
        *TIME_COLUMNS,
    }

    missing = required - set(truth.columns)
    if missing:
        raise ValueError(f"Truth thiếu cột: {sorted(missing)}")

    truth["pick_no"] = truth["pick_no"].map(normalize_request_id)

    if truth["pick_no"].isna().any() or truth["pick_no"].duplicated().any():
        raise ValueError("Truth có request_id không hợp lệ hoặc bị trùng")

    for column in (*TIME_COLUMNS, "packs"):
        truth[column] = pd.to_numeric(truth[column], errors="raise")

    for row in truth.itertuples(index=False):
        if not isfinite(row.arrival_min) or row.arrival_min < 0:
            raise ValueError(f"{row.pick_no}: arrival_min không hợp lệ")

        if (
            not isfinite(row.packs)
            or row.packs <= 0
            or row.packs != int(row.packs)
        ):
            raise ValueError(f"{row.pick_no}: packs phải là số nguyên dương")

        previous = row.arrival_min
        gap = False

        for column in TIME_COLUMNS[1:]:
            value = getattr(row, column)

            # Request chưa đi đến stage này thì mốc được để trống.
            if pd.isna(value):
                gap = True
                continue

            if gap or not isfinite(value) or value < previous:
                raise ValueError(
                    f"{row.pick_no}: thứ tự mốc {column} bị sai"
                )

            previous = value

    return truth


def _queue_summary(rows, queued_column, start_column, cut, end):
    """Tính queue trung bình theo thời gian và peak queue."""
    changes = {}
    area = 0.0

    for row in rows.itertuples(index=False):
        queued_at = getattr(row, queued_column)
        started_at = getattr(row, start_column)

        if pd.isna(queued_at):
            continue

        if pd.isna(started_at):
            started_at = float("inf")

        # Chỉ quan sát phần queue kể từ snapshot.
        entered = max(queued_at, cut)

        if entered > end or started_at <= entered:
            continue

        area += min(started_at, end) - entered
        changes[entered] = changes.get(entered, 0) + 1

        if started_at <= end:
            changes[started_at] = changes.get(started_at, 0) - 1

    # Gộp thay đổi cùng timestamp để tránh peak giả.
    current = peak = 0

    for time in sorted(changes):
        current += changes[time]
        peak = max(peak, current)

    return area / (end - cut), peak


def build_actual(truth, cut, horizon_min, sample_every_min):
    """Dựng KPI thực tế; WIP chỉ tính wait/lead kể từ snapshot."""
    end = cut + horizon_min

    # Giữ WIP thật và request mới trong cửa sổ.
    # Loại request đã giao trước hoặc đúng snapshot.
    rows = truth[
        (truth["arrival_min"] < end)
        & (
            truth["line_DELIVERED"].isna()
            | (truth["line_DELIVERED"] > cut)
        )
    ].copy()

    done = rows[
        rows["line_DELIVERED"].notna()
        & (rows["line_DELIVERED"] <= end)
    ]

    waits = []
    leads = []

    for row in done.itertuples(index=False):
        waits.append(sum(
            max(
                0.0,
                getattr(row, start_column)
                - max(getattr(row, queued_column), cut),
            )
            for queued_column, start_column in STAGE_TIMES.values()
        ))

        leads.append(
            row.line_DELIVERED - max(row.arrival_min, cut)
        )

    metrics = {
        "throughput_count": len(done),
        "unfinished_count": len(rows) - len(done),
        "avg_total_wait_min": (
            sum(waits) / len(waits) if waits else None
        ),
        "avg_lead_time_min": (
            sum(leads) / len(leads) if leads else None
        ),
        "avg_queue_length": {},
        "peak_queue": {},
    }

    for stage, (queued_column, start_column) in STAGE_TIMES.items():
        average, peak = _queue_summary(
            rows,
            queued_column,
            start_column,
            cut,
            end,
        )

        metrics["avg_queue_length"][stage] = average
        metrics["peak_queue"][stage] = peak

    timeline = []

    for time in sample_times(horizon_min, sample_every_min):
        at = cut + time

        timeline.append({
            "time_min": time,
            "completed_count": int(
                (rows["line_DELIVERED"] <= at).sum()
            ),
            "queue": {
                stage: int((
                    (rows[queued_column] <= at)
                    & (
                        rows[start_column].isna()
                        | (rows[start_column] > at)
                    )
                ).sum())
                for stage, (queued_column, start_column)
                in STAGE_TIMES.items()
            },
        })

    return metrics, timeline


def run_backtest(
    state_path="data/gold/latest/state.json",
    raw_dir=None,
    config_path="config/scenarios.yaml",
    world_config_path="config/generator.yaml",
    seed=42,
    service_min=None,
):
    """Chạy baseline từ gold, đánh giá bằng truth cùng ngày."""
    state = load_gold_state(state_path)
    as_of = pd.Timestamp(state.as_of)
    raw_dir = Path(raw_dir or f"data/raw/{as_of.date()}")

    with open(config_path, encoding="utf-8") as stream:
        settings = yaml.safe_load(stream)

    with open(world_config_path, encoding="utf-8") as stream:
        world = yaml.safe_load(stream)

    if (
        as_of.tz is None
        or as_of.utcoffset().total_seconds() / 3600
        != world["tz_offset_hours"]
    ):
        raise ValueError("Timezone snapshot không khớp config Generator")

    # Truth dùng phút từ đầu ngày; Twin dùng phút từ snapshot.
    hour, minute = map(int, world["day_start"].split(":"))
    day_start = as_of.normalize() + pd.Timedelta(
        hours=hour,
        minutes=minute,
    )
    cut = (as_of - day_start).total_seconds() / 60.0

    config = ScenarioConfig(**build_baseline(
        asdict(state),
        horizon_min=settings["default_horizon_min"],
        sample_every_min=settings["default_sample_every_min"],
        seed=seed,
        service_min=(
            service_min
            if service_min is not None
            else settings["default_service_min"]
        ),
    ))

    end = cut + config.horizon_min

    if cut < 0 or end > world["day_hours"] * 60:
        raise ValueError("Cửa sổ backtest vượt ngoài ngày của Generator")

    # Kiểm raw và snapshot thuộc cùng ngày.
    created = pd.to_datetime(
        pd.read_csv(raw_dir / "wms.csv")["created_at"],
        utc=True,
        errors="raise",
    ).dt.tz_convert(as_of.tz)

    if created.isna().any() or not created.dt.date.eq(as_of.date()).all():
        raise ValueError("Raw và snapshot không thuộc cùng ngày")

    truth = load_truth(raw_dir / "_truth" / "requests.csv")
    indexed = truth.set_index("pick_no")

    jobs = [*state.queued_jobs, *state.active_jobs]
    observed_ids = {job.request.request_id for job in jobs}

    # Kiểm ID và nội dung WIP giữa snapshot và truth.
    for job in jobs:
        request = job.request

        if request.request_id not in indexed.index:
            raise ValueError(
                f"{request.request_id}: snapshot không có trong truth"
            )

        row = indexed.loc[request.request_id]

        expected = (
            row.origin_id,
            row.line_id,
            row.material_id,
            int(row.packs),
        )
        actual = (
            request.origin_id,
            request.destination_line_id,
            request.material_id,
            request.quantity,
        )

        if actual != expected:
            raise ValueError(
                f"{request.request_id}: snapshot và truth không khớp"
            )

    # Replay demand thực tế để đánh giá Twin.
    # Không đưa lại request đã có trong WIP vào demand.
    future = truth[
        (truth["arrival_min"] >= cut)
        & (truth["arrival_min"] < end)
        & ~truth["pick_no"].isin(observed_ids)
    ]

    demand = [
        MaterialRequest(
            request_id=row.pick_no,
            arrival_min=float(row.arrival_min - cut),
            origin_id=row.origin_id,
            destination_line_id=row.line_id,
            material_id=row.material_id,
            quantity=int(row.packs),
        )
        for row in future.itertuples(index=False)
    ]

    predicted = run_twin(
        config,
        demand,
        initial_state=state,
    )

    actual_metrics, actual_timeline = build_actual(
        truth,
        cut,
        config.horizon_min,
        config.sample_every_min,
    )

    true_open = set(truth.loc[
        (truth["arrival_min"] <= cut)
        & (
            truth["line_DELIVERED"].isna()
            | (truth["line_DELIVERED"] > cut)
        ),
        "pick_no",
    ])

    return {
        "evaluation_mode": "known_actual_demand_replay",
        "as_of": state.as_of,
        "window_end": (
            as_of + pd.Timedelta(minutes=config.horizon_min)
        ).isoformat(),
        "raw_dir": str(raw_dir),
        "config": asdict(config),
        "demand_count": len(demand),
        "snapshot_check": {
            "observed_wip_count": len(observed_ids),
            "true_wip_count": len(true_open),
            "missing_from_snapshot": sorted(
                true_open - observed_ids
            ),
            "snapshot_only_ids": sorted(
                observed_ids - true_open
            ),
        },
        "actual_metrics": actual_metrics,
        "predicted_metrics": predicted.metrics,
        "errors": compare_metrics(
            actual_metrics,
            predicted.metrics,
        ),
        "actual_timeline": actual_timeline,
        "predicted_timeline": predicted.timeline,
    }


def main():
    parser = argparse.ArgumentParser(
        description="Backtest baseline với truth"
    )
    parser.add_argument(
        "--state",
        default="data/gold/latest/state.json",
    )
    parser.add_argument("--raw")
    parser.add_argument(
        "--config",
        default="config/scenarios.yaml",
    )
    parser.add_argument(
        "--world-config",
        default="config/generator.yaml",
    )
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument(
        "--out",
        default="data/results/phase8_backtest.json",
    )
    args = parser.parse_args()

    try:
        result = run_backtest(
            args.state,
            args.raw,
            args.config,
            args.world_config,
            args.seed,
        )

        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                result,
                ensure_ascii=False,
                indent=2,
                allow_nan=False,
            ),
            encoding="utf-8",
        )

    except (
        OSError,
        ValueError,
        KeyError,
        TypeError,
        yaml.YAMLError,
    ) as error:
        parser.exit(1, f"Backtest không chạy được: {error}\n")

    print(f"Window: {result['as_of']} -> {result['window_end']}")
    print(f"Snapshot: {result['snapshot_check']}")
    print(f"Demand mới: {result['demand_count']}")

    for name in (
        "throughput_count",
        "avg_total_wait_min",
        "avg_lead_time_min",
    ):
        print(f"{name}: {result['errors'][name]}")

    print(f"Results: {output}")


if __name__ == "__main__":
    main()