"""Phase 7: chạy baseline và các scenario bằng Twin."""

import argparse
from dataclasses import asdict
import json
from pathlib import Path

import yaml

from contracts.validate_contracts import check_bundle
from src.twin.entities import (
    CurrentLogisticsState,
    MaterialRequest,
    ScenarioConfig,
)
from src.twin.gold import load_gold_state
from src.twin.simulation import run as run_twin

from .scenarios import build_baseline, build_whatif_scenarios


def run_scenarios(
    configs: list[ScenarioConfig],
    demand: list[MaterialRequest],
    initial_state: CurrentLogisticsState | None = None,
) -> dict[str, dict]:
    """Chạy các scenario trên cùng đầu vào, trả kết quả theo scenario_id."""
    configs = list(configs)
    demand = list(demand)

    if not configs:
        raise ValueError("Danh sách scenario không được rỗng")

    scenario_ids = [config.scenario_id for config in configs]

    if any(
        not isinstance(scenario_id, str) or not scenario_id.strip()
        for scenario_id in scenario_ids
    ):
        raise ValueError("scenario_id phải là chuỗi không rỗng")

    if len(set(scenario_ids)) != len(scenario_ids):
        raise ValueError("scenario_id bị trùng")

    if "BASELINE" not in scenario_ids:
        raise ValueError("Cần có scenario BASELINE để so sánh")

    # Cùng cửa sổ dự báo và seed để so sánh công bằng.
    reference = configs[0]

    for config in configs:
        if config.horizon_min != reference.horizon_min:
            raise ValueError("Các scenario phải có cùng horizon_min")

        if config.random_seed != reference.random_seed:
            raise ValueError("Các scenario phải có cùng random_seed")

    results = {}

    for config in configs:
        try:
            # Twin.run đã deepcopy đầu vào cho mỗi lần chạy.
            result = run_twin(
                config,
                demand,
                initial_state=initial_state,
            )
        except ValueError as error:
            raise ValueError(
                f"Scenario {config.scenario_id}: {error}"
            ) from error

        # Chuyển dataclass thành dict và giữ nguyên KPI None.
        results[config.scenario_id] = asdict(result)

    return results


def main():
    parser = argparse.ArgumentParser(
        description="Chạy baseline và WHAT-IF từ snapshot gold"
    )
    parser.add_argument(
        "--state",
        default="data/gold/latest/state.json",
    )
    parser.add_argument(
        "--config",
        default="config/scenarios.yaml",
    )
    parser.add_argument(
        "--demand",
        help="File JSON chứa list MaterialRequest; bỏ trống để chỉ chạy WIP",
    )
    parser.add_argument(
        "--out",
        default="data/results/phase7_scenarios.json",
    )
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()

    try:
        state = load_gold_state(args.state)
        state_dict = asdict(state)

        with open(args.config, encoding="utf-8") as stream:
            settings = yaml.safe_load(stream)

        if not isinstance(settings, dict):
            raise ValueError("Config scenario phải là một YAML mapping")

        baseline = build_baseline(
            state_dict,
            horizon_min=settings["default_horizon_min"],
            sample_every_min=settings["default_sample_every_min"],
            seed=args.seed,
            service_min=settings["default_service_min"],
        )

        whatif_configs = build_whatif_scenarios(
            baseline,
            settings["scenarios"],
        )
        configs = [
            ScenarioConfig(**item)
            for item in [baseline, *whatif_configs]
        ]

        raw_demand = []

        if args.demand:
            with open(args.demand, encoding="utf-8") as stream:
                raw_demand = json.load(stream)

        if not isinstance(raw_demand, list) or not all(
            isinstance(item, dict) for item in raw_demand
        ):
            raise ValueError(
                "Demand phải là một JSON list các request object"
            )

        # Kiểm tra arrival nằm trong horizon và ID không trùng với WIP.
        errors = check_bundle(
            {
                "current_state": state_dict,
                "demand": raw_demand,
            },
            horizon_min=baseline["horizon_min"],
        )

        if errors:
            raise ValueError("; ".join(errors))

        demand = [
            MaterialRequest(**item)
            for item in raw_demand
        ]

        results = run_scenarios(
            configs,
            demand,
            initial_state=state,
        )

        output = Path(args.out)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                results,
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
        parser.exit(1, f"Không chạy được scenario: {error}\n")

    print(f"Snapshot: {state.as_of} | demand mới: {len(demand)}")

    for scenario_id, result in results.items():
        metrics = result["metrics"]
        print(
            f"{scenario_id}: "
            f"throughput={metrics['throughput_count']} "
            f"| unfinished={metrics['unfinished_count']} "
            f"| wait={metrics['avg_total_wait_min']} "
            f"| lead={metrics['avg_lead_time_min']}"
        )

    print(f"Results: {output}")


if __name__ == "__main__":
    main()