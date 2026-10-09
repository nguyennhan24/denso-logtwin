"""Phase 8: calibrate trên train, backtest và báo cáo trên test."""

import argparse
from contextlib import redirect_stdout
from datetime import date, datetime, timedelta, timezone
import io
import json
from math import isfinite
from pathlib import Path
from statistics import mean, median

import pandas as pd
import yaml

from src.data_layer.pipeline import run as build_snapshot
from src.generator import generate_day
from .backtest import run_backtest


STAGES = ("amr", "packer", "agv")
MODELS = ("DEFAULT", "CALIBRATED")

DURATIONS = {
    "amr": "amr_travel_min",
    "packer": "pack_min",
    "agv": "agv_travel_min",
}


def read_yaml(path):
    with open(path, encoding="utf-8") as stream:
        return yaml.safe_load(stream)


def write_json(path, document):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            document,
            ensure_ascii=False,
            indent=2,
            allow_nan=False,
        ),
        encoding="utf-8",
    )


def day_start(day, world):
    return datetime.fromisoformat(
        f"{day}T{world['day_start']}"
    ).replace(
        tzinfo=timezone(
            timedelta(hours=world["tz_offset_hours"])
        )
    )


def make_plan(start, train_days, test_days, seed):
    if train_days < 1 or test_days < 5 or seed < 0:
        raise ValueError(
            "Cần >=1 ngày train, >=5 ngày test và seed >=0"
        )

    first = date.fromisoformat(start)

    return [
        {
            "date": (
                first + timedelta(days=i)
            ).isoformat(),
            "seed": seed + i,
            "split": "train" if i < train_days else "test",
        }
        for i in range(train_days + test_days)
    ]


def calibrate(train, raw_root, world):
    """Chỉ lấy thời lượng quan sát được từ journey của ngày train."""
    if not train or any(
        item["split"] != "train" for item in train
    ):
        raise ValueError("Calibrate chỉ được nhận ngày train")

    samples = {stage: [] for stage in STAGES}

    for item in train:
        end = day_start(item["date"], world) + timedelta(
            hours=world["day_hours"]
        )

        result = build_snapshot(
            raw_root / item["date"],
            end,
            write=False,
            verbose=False,
        )

        for stage, column in DURATIONS.items():
            values = result.journey[column].dropna()
            values = values[
                (values > 0)
                & (values < float("inf"))
            ]
            samples[stage].extend(
                float(value) for value in values
            )

    if any(not values for values in samples.values()):
        raise ValueError(
            "Train thiếu mẫu thời lượng của một stage"
        )

    return {
        "method": "median_positive_observed_journey_durations",
        "train_days": train,
        "sample_counts": {
            stage: len(values)
            for stage, values in samples.items()
        },
        "service_min": {
            stage: float(median(values))
            for stage, values in samples.items()
        },
    }


def actual_utilizations(events, capacities, horizon):
    """Busy vật lý / available unit-minutes.

    AMR: busy từ DISPATCH đến RETURNED, gồm blocked.
    AGV: busy từ LOAD đến RETURNED.
    Packer: busy từ START đến FINISH.
    FAULT/BREAK làm giảm thời gian tài nguyên sẵn sàng.
    """
    starts = {
        "amr": "DISPATCH",
        "packer": "START",
        "agv": "LOAD",
    }
    finishes = {
        "amr": "RETURNED",
        "packer": "FINISH",
        "agv": "RETURNED",
    }

    output = {}

    for stage in STAGES:
        busy, offline = set(), set()
        busy_area = offline_area = previous = 0.0

        rows = events[
            events["stage"] == stage
        ].sort_values("t_min", kind="stable")

        for row in rows.itertuples(index=False):
            time = float(row.t_min)

            if not 0 <= time <= horizon or time < previous:
                raise ValueError(
                    "Truth event có thời gian không hợp lệ"
                )

            duration = time - previous
            busy_area += len(busy) * duration
            offline_area += len(offline) * duration
            previous = time
            unit = row.unit

            if row.event == starts[stage]:
                if unit in busy or unit in offline:
                    raise ValueError(
                        f"{stage}/{unit}: start bị trùng"
                    )
                busy.add(unit)

            elif row.event == finishes[stage]:
                if unit not in busy:
                    raise ValueError(
                        f"{stage}/{unit}: finish thiếu start"
                    )
                busy.remove(unit)

            elif row.event in ("FAULT", "BREAK_START"):
                if unit in busy or unit in offline:
                    raise ValueError(
                        f"{stage}/{unit}: offline bị sai"
                    )
                offline.add(unit)

            elif row.event in ("RECOVER", "BREAK_END"):
                if unit not in offline:
                    raise ValueError(
                        f"{stage}/{unit}: recover thiếu offline"
                    )
                offline.remove(unit)

        busy_area += len(busy) * (horizon - previous)
        offline_area += len(offline) * (horizon - previous)

        available = (
            capacities[stage] * horizon - offline_area
        )

        if available <= 0 or busy_area > available + 1e-6:
            raise ValueError(
                f"{stage}: utilization thực tế không hợp lệ"
            )

        output[stage] = busy_area / available

    return output


def primary_bottleneck(utils, threshold):
    stage = max(
        STAGES,
        key=lambda name: utils[name],
    )
    return stage if utils[stage] >= threshold else None


def summarize(windows):
    """MAE/MAPE theo cửa sổ, kèm số mẫu hợp lệ."""
    collected = {}

    for window in windows:
        for name, value in window["errors"].items():
            entries = (
                {name: value}
                if "absolute_error" in value
                else {
                    f"{name}.{stage}": error
                    for stage, error in value.items()
                }
            )

            for key, error in entries.items():
                collected.setdefault(key, []).append(error)

    output = {}

    for name, entries in collected.items():
        absolute = [
            error["absolute_error"]
            for error in entries
            if error["absolute_error"] is not None
        ]
        relative = [
            error["relative_error_pct"]
            for error in entries
            if error["relative_error_pct"] is not None
        ]

        output[name] = {
            "mae": mean(absolute) if absolute else None,
            "mape_pct": mean(relative) if relative else None,
            "valid_count": len(absolute),
            "percentage_count": len(relative),
            "total_count": len(entries),
        }

    return output


def evaluate_day(
    item,
    raw_root,
    state_root,
    world,
    settings,
    service,
):
    day = item["date"]
    horizon = float(settings["default_horizon_min"])
    length = float(world["day_hours"] * 60)

    if not isfinite(horizon) or horizon <= 0:
        raise ValueError("Horizon phải hữu hạn và dương")

    count = length / horizon

    if not count.is_integer():
        raise ValueError(
            "Horizon phải chia hết độ dài ngày"
        )

    capacities = {
        "amr": world["resources"]["amr"],
        "packer": world["resources"]["worker"],
        "agv": world["resources"]["agv"],
    }

    events = pd.read_csv(
        raw_root / day / "_truth" / "events.csv"
    )
    actual_util = actual_utilizations(
        events,
        capacities,
        length,
    )

    threshold = settings["bottleneck"]["utilization_stressed"]
    actual_primary = primary_bottleneck(
        actual_util,
        threshold,
    )

    models = {
        name: {
            "windows": [],
            "busy_area": dict.fromkeys(STAGES, 0.0),
            "available_area": dict.fromkeys(STAGES, 0.0),
        }
        for name in MODELS
    }

    for index in range(int(count)):
        at = day_start(day, world) + timedelta(
            minutes=index * horizon
        )

        snapshot = build_snapshot(
            raw_root / day,
            at,
            write=False,
            verbose=False,
        )

        state_path = state_root / day / f"{index:02d}.json"
        write_json(state_path, snapshot.state_doc)

        for name, override in (
            ("DEFAULT", None),
            ("CALIBRATED", service),
        ):
            # Twin đang print từng event; gom log khi chạy batch.
            with redirect_stdout(io.StringIO()):
                run = run_backtest(
                    state_path=state_path,
                    raw_dir=raw_root / day,
                    seed=item["seed"],
                    service_min=override,
                )

            run["date"] = day
            run["data_seed"] = item["seed"]

            model = models[name]
            model["windows"].append(run)

            for stage in STAGES:
                available = (
                    run["config"]["capacity"][stage] * horizon
                )
                model["available_area"][stage] += available
                model["busy_area"][stage] += (
                    run["predicted_metrics"]["utilization"][stage]
                    * available
                )

    output = {}

    for name, model in models.items():
        predicted_util = {
            stage: (
                model["busy_area"][stage]
                / model["available_area"][stage]
            )
            for stage in STAGES
        }
        predicted_primary = primary_bottleneck(
            predicted_util,
            threshold,
        )

        output[name] = {
            "day": {
                "date": day,
                "seed": item["seed"],
                "actual_utilization": actual_util,
                "predicted_utilization": predicted_util,
                "actual_primary": actual_primary,
                "predicted_primary": predicted_primary,
                "match": predicted_primary == actual_primary,
            },
            "windows": model["windows"],
        }

    return output


def run_phase8(
    root="data/results/phase8_data",
    out="data/results/phase8_validation.json",
    start="2026-10-01",
    train_days=20,
    test_days=5,
    seed=42,
):
    root = Path(root)

    world = read_yaml("config/generator.yaml")
    settings = read_yaml("config/scenarios.yaml")
    data_config = read_yaml("config/data_layer.yaml")

    plan = make_plan(start, train_days, test_days, seed)
    train = [
        item for item in plan
        if item["split"] == "train"
    ]
    test = [
        item for item in plan
        if item["split"] == "test"
    ]

    raw_root = root / "raw"

    for index, item in enumerate(plan, start=1):
        print(
            f"DATA {index}/{len(plan)}: {item}",
            flush=True,
        )
        generate_day(
            date=item["date"],
            seed=item["seed"],
            config=world,
            out_dir=raw_root,
        )

    manifest = {
        "days": plan,
        "generator_config": world,
        "scenario_config": settings,
        "data_layer_config": data_config,
    }
    write_json(root / "manifest.json", manifest)

    print("CALIBRATE: train only", flush=True)
    calibration = calibrate(train, raw_root, world)
    write_json(root / "calibration.json", calibration)

    models = {
        name: {"days": [], "windows": []}
        for name in MODELS
    }

    for index, item in enumerate(test, start=1):
        print(
            f"TEST {index}/{len(test)}: {item['date']}",
            flush=True,
        )

        evaluated = evaluate_day(
            item,
            raw_root,
            root / "states",
            world,
            settings,
            calibration["service_min"],
        )

        for name, result in evaluated.items():
            models[name]["days"].append(result["day"])
            models[name]["windows"].extend(
                result["windows"]
            )

    for name, model in models.items():
        model["summary"] = summarize(model["windows"])

        matches = sum(
            day["match"] for day in model["days"]
        )
        rate = matches / len(model["days"]) * 100.0

        model["bottleneck_agreement_pct"] = rate
        model["gate"] = {
            "minimum_test_days": 5,
            "minimum_agreement_pct": 80.0,
            "passed": (
                len(model["days"]) >= 5
                and rate >= 80.0
            ),
        }

        throughput = model["summary"]["throughput_count"]["mae"]
        wait = model["summary"]["avg_total_wait_min"]["mae"]

        model["narrative"] = (
            f"{name}: {len(test)} ngày test, "
            f"{len(model['windows'])} cửa sổ. "
            f"Throughput MAE={throughput}; "
            f"wait MAE={wait} phút. "
            f"Bottleneck khớp {matches}/{len(test)} ngày "
            f"({rate:.1f}%). "
            f"Mục tiêu agreement: "
            f"{'ĐẠT' if model['gate']['passed'] else 'CHƯA ĐẠT'}."
        )

    baseline = models["DEFAULT"]["summary"]
    calibrated = models["CALIBRATED"]["summary"]

    comparison = [
        {
            "kpi": key,
            "default_mae": baseline[key]["mae"],
            "calibrated_mae": value["mae"],
            "mae_delta": (
                value["mae"] - baseline[key]["mae"]
                if (
                    value["mae"] is not None
                    and baseline[key]["mae"] is not None
                )
                else None
            ),
        }
        for key, value in calibrated.items()
    ]

    actual_labels = sorted({
        str(day["actual_primary"])
        for day in models["CALIBRATED"]["days"]
    })

    report = {
        "report_version": "0.1",
        "evaluation_mode": "known_actual_demand_replay",
        "dataset": manifest,
        "calibration": calibration,
        "models": models,
        "comparison": comparison,
        "limitations": [
            "Demand là request thực tế: chưa đánh giá forecast demand.",
            "Dữ liệu Generator: chưa kiểm chứng tại nhà máy thật.",
            "Twin v0.1 chưa mô hình đầy đủ blocking, chuyến về và sự cố.",
            "Bottleneck actual dùng busy/available vật lý cả ngày; "
            "busy AMR/AGV gồm chuyến về, AMR gồm cả blocked.",
            "MAPE bỏ actual=0 hoặc KPI=None; xem số mẫu hợp lệ.",
            f"Bottleneck actual trong bộ test này: {actual_labels}; "
            "agreement không chứng minh độ chính xác "
            "ở các bottleneck khác.",
        ],
    }

    write_json(out, report)

    print(
        f"Service calibrated: {calibration['service_min']}",
        flush=True,
    )
    for model in models.values():
        print(model["narrative"], flush=True)

    print(f"Report: {out}", flush=True)
    return report


def main():
    parser = argparse.ArgumentParser(
        description="Pipeline Phase 8"
    )
    parser.add_argument(
        "--root",
        default="data/results/phase8_data",
    )
    parser.add_argument(
        "--out",
        default="data/results/phase8_validation.json",
    )
    parser.add_argument("--start", default="2026-10-01")
    parser.add_argument("--train-days", type=int, default=20)
    parser.add_argument("--test-days", type=int, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    run_phase8(
        args.root,
        args.out,
        args.start,
        args.train_days,
        args.test_days,
        args.seed,
    )


if __name__ == "__main__":
    main()