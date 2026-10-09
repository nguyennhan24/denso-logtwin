from dataclasses import dataclass
from typing import Any

@dataclass
class BottleneckResult:
    primary: str | None
    secondary: str | None
    utilizations: dict[str, float]
    peak_queues: dict[str, int]
    wait_per_stage: dict[str, float]
    explanation: str

@dataclass
class RankedScenario:
    rank: int
    scenario_id: str
    total_score: float
    kpi_scores: dict[str, float]
    kpi_deltas: dict[str, float]
    kpi_delta_pcts: dict[str, float]
    bottleneck: BottleneckResult
    explanation: str

def detect_bottleneck(metrics: dict[str, Any], events: list[dict[str, Any]] | None = None) -> BottleneckResult:
    """Phát hiện bottleneck từ metrics và events."""
    utils = metrics.get("utilization", {"amr": 0.0, "packer": 0.0, "agv": 0.0})
    queues = metrics.get("peak_queue", {"amr": 0, "packer": 0, "agv": 0})
    
    # Wait contribution logic (simplified for v0.1 without full events parsing if not provided)
    wait_per_stage = {"amr": 0.0, "packer": 0.0, "agv": 0.0}
    
    stressed_stages = [st for st, u in utils.items() if u >= 0.80]
    
    primary = None
    secondary = None
    explanation_parts = []
    
    if stressed_stages:
        # Sort by utilization descending
        stressed_stages.sort(key=lambda st: utils[st], reverse=True)
        primary = stressed_stages[0]
        
        explanation_parts.append(f"{primary.upper()} là bottleneck chính (utilization {utils[primary]:.0%}, peak queue {queues[primary]}).")
        
        if len(stressed_stages) > 1:
            if utils[primary] - utils[stressed_stages[1]] <= 0.10:
                secondary = stressed_stages[1]
                explanation_parts.append(f"{secondary.upper()} là bottleneck phụ (utilization {utils[secondary]:.0%}).")
                
    else:
        explanation_parts.append("Không có bottleneck rõ ràng — hệ thống cân bằng (tất cả utilization < 80%).")
        
    # Cross-check with peak queue
    for st in ("amr", "packer", "agv"):
        if st != primary and queues[st] > 5:
            if primary and queues[st] > queues[primary]:
                explanation_parts.append(f"Cảnh báo: Peak queue của {st.upper()} ({queues[st]}) cao hơn {primary.upper()}, gợi ý ùn tắc cục bộ.")

    return BottleneckResult(
        primary=primary,
        secondary=secondary,
        utilizations=utils,
        peak_queues=queues,
        wait_per_stage=wait_per_stage,
        explanation=" ".join(explanation_parts)
    )

def generate_explanation(ranked: RankedScenario, baseline_bn: str | None) -> str:
    """Sinh giải thích tự nhiên cho 1 scenario."""
    parts = [f"Kịch bản {ranked.scenario_id}:"]
    
    tp_delta = ranked.kpi_deltas.get("throughput_count", 0)
    tp_pct = ranked.kpi_delta_pcts.get("throughput_count", 0.0)
    if tp_delta > 0:
        parts.append(f"  • Giao thêm được {tp_delta} request (+{tp_pct:.0f}%)")
    elif tp_delta == 0:
        parts.append(f"  • Throughput không đổi — thêm tài nguyên không tăng năng suất")
        
    lt_delta = ranked.kpi_deltas.get("avg_lead_time_min", 0)
    lt_pct = ranked.kpi_delta_pcts.get("avg_lead_time_min", 0.0)
    if lt_delta < 0:
        parts.append(f"  • Lead time giảm {abs(lt_delta):.1f} phút ({lt_pct:.0f}%)")
        
    bn = ranked.bottleneck
    if bn.primary:
        parts.append(f"  • Bottleneck: {bn.primary} (utilization {bn.utilizations[bn.primary]:.0%})")
        if bn.primary == baseline_bn:
            parts.append(f"    → Bottleneck chưa thay đổi, cần thêm capacity {bn.primary}")
        else:
            baseline_name = baseline_bn if baseline_bn else "None"
            parts.append(f"    → Bottleneck chuyển từ {baseline_name} sang {bn.primary}")
            
    uf = ranked.kpi_deltas.get("unfinished_count", 0)
    if uf < 0:
        parts.append(f"  • Giảm {abs(int(uf))} request không kịp giao")
        
    return "\n".join(parts)

def rank(
    results: dict[str, dict[str, Any]],
    weights: dict[str, float] | None = None
) -> list[RankedScenario]:
    """Xếp hạng scenario dựa trên weighted normalized score."""
    if "BASELINE" not in results:
        raise ValueError("Must include 'BASELINE' in results")
        
    if weights is None:
        weights = {
            "throughput_count": 0.30,
            "avg_lead_time_min": 0.25,
            "avg_total_wait_min": 0.20,
            "unfinished_count": 0.15,
            "peak_queue_max": 0.10
        }
        
    kpi_dirs = {
        "throughput_count": 1,
        "avg_lead_time_min": -1,
        "avg_total_wait_min": -1,
        "unfinished_count": -1,
        "peak_queue_max": -1
    }
    
    # B1 & B2: Thu thập và tìm min/max
    kpi_stats = {}
    for kpi, d in kpi_dirs.items():
        vals = []
        for res in results.values():
            m = res.get("metrics", {})
            if kpi == "peak_queue_max":
                pq = m.get("peak_queue", {"amr": 0, "packer": 0, "agv": 0})
                v = max(pq.values()) if pq else 0
            else:
                v = m.get(kpi)
                if v is None: v = 0.0 # fallback
            vals.append(v)
            
        best = max(vals) if d == 1 else min(vals)
        worst = min(vals) if d == 1 else max(vals)
        kpi_stats[kpi] = {"best": best, "worst": worst}
        
    ranked_list = []
    baseline_metrics = results["BASELINE"].get("metrics", {})
    baseline_bn = detect_bottleneck(baseline_metrics).primary
    
    for scid, res in results.items():
        m = res.get("metrics", {})
        
        scores = {}
        deltas = {}
        pcts = {}
        
        for kpi, d in kpi_dirs.items():
            if kpi == "peak_queue_max":
                pq = m.get("peak_queue", {"amr": 0, "packer": 0, "agv": 0})
                val = max(pq.values()) if pq else 0
                bpq = baseline_metrics.get("peak_queue", {"amr": 0, "packer": 0, "agv": 0})
                base_val = max(bpq.values()) if bpq else 0
            else:
                val = m.get(kpi, 0.0)
                if val is None: val = 0.0
                base_val = baseline_metrics.get(kpi, 0.0)
                if base_val is None: base_val = 0.0
                
            best = kpi_stats[kpi]["best"]
            worst = kpi_stats[kpi]["worst"]
            
            # Normalize score
            if best == worst:
                scores[kpi] = 1.0
            else:
                if d == 1:
                    scores[kpi] = (val - worst) / (best - worst)
                else:
                    scores[kpi] = (worst - val) / (worst - best)
                    
            # Deltas
            deltas[kpi] = val - base_val
            pcts[kpi] = (deltas[kpi] / base_val * 100) if base_val != 0 else 0.0
            
        total_weight = sum(weights.values())
        total_score = sum(scores[k] * weights.get(k, 0.0) for k in scores) / total_weight if total_weight else 0.0
        
        bn = detect_bottleneck(m, res.get("events"))
        
        rs = RankedScenario(
            rank=0, # temp
            scenario_id=scid,
            total_score=total_score,
            kpi_scores=scores,
            kpi_deltas=deltas,
            kpi_delta_pcts=pcts,
            bottleneck=bn,
            explanation=""
        )
        rs.explanation = generate_explanation(rs, baseline_bn)
        ranked_list.append(rs)
        
    ranked_list.sort(key=lambda x: x.total_score, reverse=True)
    for i, rs in enumerate(ranked_list):
        rs.rank = i + 1
        
    return ranked_list
