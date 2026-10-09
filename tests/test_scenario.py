from src.scenario.ranking import rank, detect_bottleneck
from src.scenario.scenarios import build_baseline, build_whatif_scenarios

def test_scenarios_build():
    state = {
        "resources": {
            "amr": {"total": 5, "offline": 1, "busy": 2},
            "packer": {"total": 3, "offline": 0, "busy": 1},
            "agv": {"total": 4, "offline": 0, "busy": 1}
        }
    }
    baseline = build_baseline(state)
    assert baseline["capacity"]["amr"] == 4
    
    whatifs = build_whatif_scenarios(baseline, [{"scenario_id": "+1AMR", "capacity_delta": {"amr": 1}}])
    assert whatifs[0]["capacity"]["amr"] == 5

def test_ranking_logic():
    res = {
        "BASELINE": {
            "metrics": {
                "throughput_count": 10,
                "avg_lead_time_min": 25.0,
                "avg_total_wait_min": 15.0,
                "unfinished_count": 2,
                "utilization": {"amr": 0.85, "packer": 0.5, "agv": 0.4},
                "peak_queue": {"amr": 4, "packer": 1, "agv": 0}
            }
        },
        "+1AMR": {
            "metrics": {
                "throughput_count": 12,
                "avg_lead_time_min": 15.0,
                "avg_total_wait_min": 5.0,
                "unfinished_count": 0,
                "utilization": {"amr": 0.60, "packer": 0.5, "agv": 0.4},
                "peak_queue": {"amr": 1, "packer": 1, "agv": 0}
            }
        }
    }
    ranked = rank(res)
    assert len(ranked) == 2
    assert ranked[0].scenario_id == "+1AMR"
    assert ranked[0].bottleneck.primary is None
    assert ranked[1].bottleneck.primary == "amr"
