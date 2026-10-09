import math
from typing import Any
import pandas as pd

def forecast_demand(
    plan: pd.DataFrame,
    bom: dict[str, dict[str, Any]],
    horizon_hours: int = 4,
    packs_per_trip: dict[str, int] | None = None,
    start_min: float = 0.0,
) -> list[dict[str, Any]]:
    """Dự báo demand từ production plan × BOM."""
    if packs_per_trip is None:
        packs_per_trip = {"min": 2, "max": 6}
    
    mean_packs = (packs_per_trip["min"] + packs_per_trip["max"]) / 2
    end_min = start_min + horizon_hours * 60.0
    
    requests = []
    req_id_counter = 1
    
    for r in plan.itertuples():
        hour_idx = r.hour_idx
        # Check if this hour is within our horizon
        hour_start = hour_idx * 60.0
        hour_end = (hour_idx + 1) * 60.0
        
        if hour_end <= start_min or hour_start >= end_min:
            continue
            
        product_bom = bom.get(r.product, {}).get("bom_packs_per_unit", {})
        for material_id, packs_per_unit in product_bom.items():
            # Total packs needed in this hour for this material
            total_packs_needed = r.planned_qty * packs_per_unit
            
            # Number of trips needed
            num_trips = int(round(total_packs_needed / mean_packs))
            
            # Spread trips evenly across the hour
            for i in range(num_trips):
                # Calculate deterministic arrival time within the hour
                offset = (i + 0.5) * (60.0 / num_trips) if num_trips > 0 else 0
                arrival_time = hour_start + offset
                
                if start_min <= arrival_time < end_min:
                    requests.append({
                        "request_id": f"FCAST-{req_id_counter:05d}",
                        "arrival_min": round(arrival_time, 2),
                        "origin_id": "STORE-1",  # Fixed per config
                        "destination_line_id": r.line_id,
                        "material_id": material_id,
                        "quantity": int(math.ceil(mean_packs))
                    })
                    req_id_counter += 1
                    
    # Sort by arrival_min
    requests.sort(key=lambda x: x["arrival_min"])
    return requests

def detect_spikes(
    forecast: list[dict[str, Any]],
    window_min: float = 30.0,
    spike_threshold: float = 2.0,
) -> list[dict[str, Any]]:
    """Phát hiện khoảng thời gian có demand cao bất thường."""
    if not forecast:
        return []
        
    max_time = max(r["arrival_min"] for r in forecast)
    num_windows = int(math.ceil(max_time / window_min))
    
    counts = [0] * num_windows
    for r in forecast:
        idx = int(r["arrival_min"] / window_min)
        if idx < num_windows:
            counts[idx] += 1
            
    if not counts:
        return []
        
    mean_count = sum(counts) / len(counts)
    variance = sum((c - mean_count) ** 2 for c in counts) / len(counts)
    std_dev = math.sqrt(variance)
    
    spikes = []
    for i, count in enumerate(counts):
        if std_dev > 0 and count > mean_count + spike_threshold * std_dev:
            severity = "critical" if count > mean_count + 3 * std_dev else "warning"
            spikes.append({
                "window_start_min": i * window_min,
                "window_end_min": (i + 1) * window_min,
                "request_count": count,
                "avg_count": round(mean_count, 2),
                "severity": severity
            })
            
    return spikes

def build_risk_timeline(
    forecast: list[dict[str, Any]],
    spikes: list[dict[str, Any]],
    state: dict[str, Any],
    interval_min: float = 15.0,
) -> list[dict[str, Any]]:
    """Xây timeline rủi ro cho tab NEXT."""
    timeline = []
    if not forecast:
        return timeline
        
    max_time = max(r["arrival_min"] for r in forecast)
    num_intervals = int(math.ceil(max_time / interval_min))
    
    for i in range(num_intervals):
        start = i * interval_min
        end = start + interval_min
        
        reqs_in_interval = sum(1 for r in forecast if start <= r["arrival_min"] < end)
        
        risk_level = "low"
        note = "Bình thường"
        
        # Check if overlaps with any spike
        for spike in spikes:
            # Overlap check
            if max(start, spike["window_start_min"]) < min(end, spike["window_end_min"]):
                risk_level = "high" if spike["severity"] == "critical" else "medium"
                note = f"Demand spike dự kiến ({spike['request_count']} reqs / {spike['window_end_min'] - spike['window_start_min']} min)"
                break
                
        timeline.append({
            "time_min": start,
            "expected_requests": reqs_in_interval,
            "risk_level": risk_level,
            "note": note
        })
        
    return timeline
