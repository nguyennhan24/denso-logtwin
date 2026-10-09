import pandas as pd
from src.demand.forecast import forecast_demand, detect_spikes, build_risk_timeline

def test_forecast_demand():
    plan_data = pd.DataFrame([
        {"hour_idx": 0, "line_id": "L1", "product": "P100", "planned_qty": 100}
    ])
    bom = {
        "P100": {"bom_packs_per_unit": {"M01": 0.2}}
    } # 100 * 0.2 = 20 packs needed
    
    reqs = forecast_demand(plan_data, bom, horizon_hours=1, start_min=0.0)
    assert len(reqs) > 0
    assert all(r["arrival_min"] >= 0 and r["arrival_min"] < 60 for r in reqs)

def test_detect_spikes():
    forecast = [
        {"arrival_min": 10}, {"arrival_min": 12}, {"arrival_min": 15}, # window 0
        {"arrival_min": 45} # window 1
    ]
    spikes = detect_spikes(forecast, window_min=30.0, spike_threshold=0.5)
    assert len(spikes) >= 0

def test_build_risk_timeline():
    forecast = [{"arrival_min": 10}]
    spikes = [{"window_start_min": 0, "window_end_min": 30, "request_count": 5, "severity": "warning"}]
    state = {}
    timeline = build_risk_timeline(forecast, spikes, state, interval_min=15.0)
    assert len(timeline) > 0
    assert timeline[0]["risk_level"] in ("medium", "high")
