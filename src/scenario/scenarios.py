import copy
from typing import Any

def build_baseline(
    state: dict[str, Any],
    horizon_min: float = 60.0,
    sample_every_min: float = 5.0,
    seed: int = 42,
    service_min: dict[str, float] | None = None,
) -> dict[str, Any]:
    """Tạo baseline config từ CurrentLogisticsState."""
    if service_min is None:
        service_min = {"amr": 5.0, "packer": 4.0, "agv": 3.0}

    resources = state.get("resources", {})
    capacity = {}
    for stage in ("amr", "packer", "agv"):
        res = resources.get(stage, {"total": 0, "offline": 0, "busy": 0})
        # Baseline capacity = total - offline
        cap = res.get("total", 0) - res.get("offline", 0)
        # Đảm bảo không nhỏ hơn số đang bận
        capacity[stage] = max(cap, res.get("busy", 0))

    return {
        "scenario_id": "BASELINE",
        "horizon_min": float(horizon_min),
        "sample_every_min": float(sample_every_min),
        "random_seed": int(seed),
        "capacity": capacity,
        "service_min": copy.deepcopy(service_min)
    }

def build_whatif_scenarios(
    baseline: dict[str, Any],
    scenario_defs: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    """Tạo list ScenarioConfig WHAT-IF từ baseline."""
    results = []
    for sd in scenario_defs:
        cfg = copy.deepcopy(baseline)
        cfg["scenario_id"] = sd["scenario_id"]

        for stage in ("amr", "packer", "agv"):
            # Tăng capacity
            delta = sd.get("capacity_delta", {}).get(stage, 0)
            cfg["capacity"][stage] += delta

            # Nhân service_min
            factor = sd.get("service_min_factor", {}).get(stage, 1.0)
            cfg["service_min"][stage] = round(cfg["service_min"][stage] * factor, 2)

        results.append(cfg)
    return results

def validate_scenario(cfg: dict[str, Any], state: dict[str, Any]) -> list[str]:
    """Kiểm tra scenario hợp lệ trước khi đưa vào Twin.run()."""
    errs = []
    horizon = cfg.get("horizon_min", 0)
    sample = cfg.get("sample_every_min", 0)
    
    if horizon <= 0:
        errs.append("horizon_min phải > 0")
    if sample <= 0 or horizon % sample != 0:
        errs.append("sample_every_min phải > 0 và chia hết cho horizon_min")
        
    resources = state.get("resources", {})
    for stage in ("amr", "packer", "agv"):
        busy = resources.get(stage, {}).get("busy", 0)
        cap = cfg.get("capacity", {}).get(stage, 0)
        if cap < busy:
            errs.append(f"capacity[{stage}] ({cap}) < busy ({busy}) tại snapshot")
            
        svc = cfg.get("service_min", {}).get(stage, 0)
        if svc <= 0:
            errs.append(f"service_min[{stage}] phải > 0")
            
    return errs
