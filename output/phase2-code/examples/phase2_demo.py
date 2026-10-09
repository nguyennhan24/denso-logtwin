"""Chạy fixture Phase 2 và xuất SimulationResult JSON để bàn giao."""
import json
from dataclasses import asdict
from pathlib import Path

from src.twin.entities import MaterialRequest, ScenarioConfig
from src.twin.simulation import run


def main():
    root = Path(__file__).resolve().parents[1]
    fixture = json.loads((root / "contracts_v0.1_example.json").read_text(encoding="utf-8"))
    config = ScenarioConfig(**fixture["scenario_config"])
    demand = [MaterialRequest(**request) for request in fixture["material_requests"]]
    result = run(config, demand)

    for key, expected in fixture["simulation_result"]["metrics"].items():
        assert result.metrics[key] == expected, key

    destination = root / "data" / "results" / "phase2_simulation_result.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(asdict(result), ensure_ascii=False, indent=2, allow_nan=False),
        encoding="utf-8",
    )
    print("PHASE 2 FIXTURE: PASS")
    print("SimulationResult JSON:", destination)


if __name__ == "__main__":
    main()
