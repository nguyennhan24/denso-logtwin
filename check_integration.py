import json
import yaml
import pandas as pd
import sys

from src.scenario.scenarios import build_baseline, build_whatif_scenarios, validate_scenario
from src.scenario.ranking import rank
from src.demand.forecast import forecast_demand, detect_spikes, build_risk_timeline

def main():
    print("=== KIỂM TRA TÍCH HỢP: PHẦN CỦA CƯỜNG ===")
    
    # 1. Đọc mock data (Mô phỏng output của Trung)
    try:
        with open("contracts/mock_v0_1.json", "r", encoding="utf-8") as f:
            mock_data = json.load(f)
        state = mock_data["current_state"]
        print("✅ [Data Layer -> Decision] Đọc thành công CurrentLogisticsState từ Trung.")
    except Exception as e:
        print(f"❌ Lỗi đọc mock data: {e}")
        return

    # 2. Test scenarios.py
    try:
        baseline = build_baseline(state)
        
        with open("config/scenarios.yaml", "r", encoding="utf-8") as f:
            yaml_config = yaml.safe_load(f)
            
        whatifs = build_whatif_scenarios(baseline, yaml_config["scenarios"][:3]) # Lấy 3 kịch bản đầu
        
        errs = validate_scenario(baseline, state)
        if errs:
            print(f"❌ Baseline không hợp lệ: {errs}")
        else:
            print(f"✅ [Decision -> Twin] Sinh thành công {len(whatifs)} kịch bản WHAT-IF hợp lệ để đẩy cho Nhân.")
    except Exception as e:
        print(f"❌ Lỗi khi sinh scenario: {e}")
        return

    # 3. Test ranking.py (Mô phỏng output của Nhân)
    try:
        # Giả lập kết quả Twin trả về (SimulationResult)
        fake_results = {
            "BASELINE": {
                "metrics": {
                    "throughput_count": 10,
                    "avg_lead_time_min": 25.0,
                    "avg_total_wait_min": 15.0,
                    "unfinished_count": 5,
                    "utilization": {"amr": 0.85, "packer": 0.5, "agv": 0.4},
                    "peak_queue": {"amr": 6, "packer": 1, "agv": 0}
                },
                "events": []
            },
            "+1AMR": {
                "metrics": {
                    "throughput_count": 15,
                    "avg_lead_time_min": 15.0,
                    "avg_total_wait_min": 5.0,
                    "unfinished_count": 0,
                    "utilization": {"amr": 0.60, "packer": 0.6, "agv": 0.5},
                    "peak_queue": {"amr": 2, "packer": 2, "agv": 1}
                },
                "events": []
            }
        }
        
        ranked = rank(fake_results, weights=yaml_config["ranking_weights"])
        print("\n✅ [Twin -> Decision -> UI] Kết quả xếp hạng gửi cho UI của Hải Anh:")
        for r in ranked:
            print(f"\n[Rank {r.rank}] {r.scenario_id} (Score: {r.total_score:.2f})")
            print(f"   => {r.explanation.replace(chr(10), chr(10) + '   => ')}")
            
    except Exception as e:
        print(f"❌ Lỗi khi xếp hạng kịch bản: {e}")
        return
        
    # 4. Test forecast.py
    try:
        plan_df = pd.DataFrame([
            {"hour_idx": 0, "line_id": "L1", "product": "P100", "planned_qty": 200},
            {"hour_idx": 1, "line_id": "L1", "product": "P100", "planned_qty": 250},
        ])
        
        bom_mock = {"P100": {"bom_packs_per_unit": {"M01": 0.2}}}
        
        future_reqs = forecast_demand(plan_df, bom_mock, horizon_hours=2)
        spikes = detect_spikes(future_reqs, window_min=30.0, spike_threshold=1.0)
        timeline = build_risk_timeline(future_reqs, spikes, state, interval_min=30.0)
        
        print("\n✅ [Data Layer -> Decision -> UI] Forecast sinh ra timeline cho tab NEXT:")
        for t in timeline:
            print(f"   Phút {t['time_min']}: {t['expected_requests']} requests -> Rủi ro: {t['risk_level'].upper()} ({t['note']})")
            
    except Exception as e:
        print(f"❌ Lỗi khi chạy forecast: {e}")
        return

if __name__ == "__main__":
    main()
