import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime
import json
import os
import sys
from pathlib import Path

# Nối đường dẫn để import code thư mục mẹ (src)
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))
from src.scenario.ranking import rank
from dataclasses import asdict

from contracts.validate_contracts import check_bundle
from src.demand.planner import load_plan
from src.demand.conversion import (
    CONTRACT_FIELDS,
    build_demand_table,
    load_bom,
)
from src.twin.gold import load_gold_state
from src.twin.entities import MaterialRequest, ScenarioConfig
from src.scenario.scenarios import build_baseline, build_whatif_scenarios
from src.scenario.runner import run_scenarios
import yaml

# ==========================================
# CẤU HÌNH GIAO DIỆN & CUSTOM CSS HIỆN ĐẠI
# ==========================================
st.set_page_config(
    page_title="Digital Twin Control Tower — DENSO D2",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
    <style>
    .stApp {
        background-color: #0B0F19;
        color: #F3F4F6;
    }
    [data-testid="stSidebar"] {
        background-color: #111827;
        border-right: 1px solid #1F2937;
    }
    .stButton>button {
        width: 100%;
        background-color: #1F2937;
        color: #E5E7EB;
        border: 1px solid #374151;
        border-radius: 8px;
        padding: 0.6rem 1rem;
        font-weight: 600;
        transition: all 0.3s ease;
    }
    .stButton>button:hover {
        background-color: #2563EB;
        color: #FFFFFF;
        border-color: #3B82F6;
        box-shadow: 0 4px 12px rgba(37, 99, 235, 0.3);
    }
    [data-testid="stMetric"] {
        background-color: #111827;
        border: 1px solid #1F2937;
        padding: 16px;
        border-radius: 10px;
        box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1);
    }
    [data-testid="stMetricLabel"] {
        color: #9CA3AF !important;
        font-size: 0.85rem !important;
    }
    [data-testid="stMetricValue"] {
        color: #F9FAFB !important;
        font-weight: 700 !important;
    }
    h1, h2, h3 {
        color: #F9FAFB;
        font-family: 'Inter', sans-serif;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# MOCK DATA & CONTRACTS
# ==========================================
ROOT = Path(__file__).resolve().parents[1]
GOLD_DIR = ROOT / "data" / "gold" / "latest"

def load_now_data():
    """Mỗi lần rerun đọc lại gold, không cache hoặc fallback mock."""
    from contracts.validate_contracts import check_bundle

    with (GOLD_DIR / "state.json").open(encoding="utf-8") as stream:
        doc = json.load(stream)

    if doc.get("contract_version") != "0.1":
        raise ValueError("NOW chỉ hỗ trợ contract v0.1")

    if not isinstance(doc.get("current_state"), dict):
        raise ValueError("Gold thiếu current_state")

    errors = check_bundle(doc)
    if errors:
        raise ValueError("; ".join(errors))

    current_state = doc["current_state"]

    with (GOLD_DIR / "data_quality.json").open(
        encoding="utf-8"
    ) as stream:
        quality = json.load(stream)

    if quality["as_of"] != current_state["as_of"]:
        raise ValueError(
            "State và quality khác snapshot; "
            "hãy làm mới sau khi Data Layer chạy xong"
        )

    with (ROOT / "config" / "scenarios.yaml").open(
        encoding="utf-8"
    ) as stream:
        thresholds = yaml.safe_load(stream)["exception_severity"]

    return current_state, quality, thresholds

def run_dashboard_scenarios():
    """Sinh demand và chạy các scenario từ snapshot hiện tại."""
    plan_id = st.sidebar.selectbox(
        "Kế hoạch dự báo",
        ["baseline", "spike_14_16_x3"],
        key="forecast_plan",
    )
    seed = int(st.sidebar.number_input(
        "Seed dữ liệu",
        min_value=0,
        value=42,
        step=1,
        key="forecast_seed",
    ))

    try:
        twin_state = load_gold_state(GOLD_DIR / "state.json")

        # Đảm bảo NOW và dự báo dùng cùng thời điểm snapshot.
        if twin_state.as_of != state["as_of"]:
            raise ValueError("Snapshot vừa thay đổi; hãy làm mới dashboard.")

        as_of = pd.Timestamp(twin_state.as_of)
        raw_dir = (
            ROOT / "data" / "raw"
            / f"{as_of.date().isoformat()}_seed{seed}"
        )
        if not raw_dir.is_dir():
            raise FileNotFoundError(f"Không có dữ liệu raw: {raw_dir}")

        with (ROOT / "config" / "scenarios.yaml").open(
            encoding="utf-8"
        ) as stream:
            cfg = yaml.safe_load(stream)

        horizon = float(cfg["default_horizon_min"])

        plan = load_plan(
            plan_id,
            twin_state.as_of,
            raw_dir=raw_dir,
            plans_dir=ROOT / "config" / "plans",
        )
        demand_table = build_demand_table(
            plan,
            load_bom(ROOT / "config" / "generator.yaml"),
            twin_state.as_of,
            horizon,
            seed,
            "expected",
        )
        raw_demand = demand_table[CONTRACT_FIELDS].to_dict("records")

        errors = check_bundle(
            {
                "current_state": asdict(twin_state),
                "demand": raw_demand,
            },
            horizon_min=horizon,
        )
        if errors:
            raise ValueError("; ".join(errors))

        baseline = build_baseline(
            asdict(twin_state),
            horizon_min=horizon,
            sample_every_min=cfg["default_sample_every_min"],
            seed=seed,
            service_min=cfg["default_service_min"],
        )
        configs = [
            ScenarioConfig(**item)
            for item in [
                baseline,
                *build_whatif_scenarios(baseline, cfg["scenarios"]),
            ]
        ]

        with st.spinner("Đang chạy mô phỏng..."):
            results = run_scenarios(
                configs,
                [MaterialRequest(**item) for item in raw_demand],
                initial_state=twin_state,
            )

    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
        st.error(f"Không chạy được dự báo: {error}")
        st.stop()

    st.caption(
        f"Snapshot: {twin_state.as_of} | Plan: {plan_id} | "
        f"Horizon: {horizon:g} phút | Demand mới: {len(raw_demand)} lượt"
    )
    if plan_id == "spike_14_16_x3":
        st.info("Kế hoạch giả định: demand tăng 3 lần trong khung 14:00–16:00.")

    return results, cfg, as_of

@st.cache_data
def load_mock_simulation_timeline():
    return pd.DataFrame([
        {"time": "14:30", "queue_length": 8, "risk_level": "Low", "status": "Normal"},
        {"time": "14:45", "queue_length": 14, "risk_level": "Medium", "status": "Warning: Queue building up"},
        {"time": "15:00", "queue_length": 22, "risk_level": "High", "status": "Bottleneck at Pack Station"},
        {"time": "15:15", "queue_length": 18, "risk_level": "Medium", "status": "Recovering"},
        {"time": "15:30", "queue_length": 9, "risk_level": "Low", "status": "Normal"},
    ])

@st.cache_data
def load_validation_report():
    path = ROOT / "data" / "results" / "phase8_validation.json"

    with path.open(encoding="utf-8") as stream:
        report = json.load(stream)

    if report.get("report_version") != "0.1":
        raise ValueError("Report Phase 8 không đúng version")

    if not {"DEFAULT", "CALIBRATED"} <= set(
        report.get("models", {})
    ):
        raise ValueError("Report thiếu kết quả DEFAULT/CALIBRATED")

    return report

try:
    state, quality, thresholds = load_now_data()
except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as error:
    st.error(f"Không đọc được dữ liệu gold: {error}")
    st.stop()

# ==========================================
# SIDEBAR NAVIGATION
# ==========================================
st.sidebar.markdown("### 🏭 NEXUS LOGISTICS")
st.sidebar.markdown("<p style='color: #9CA3AF; font-size: 0.8rem;'>Digital Twin Control Tower v2.4</p>", unsafe_allow_html=True)
st.sidebar.markdown("---")

if "menu_selection" not in st.session_state:
    st.session_state["menu_selection"] = " NOW (Control Room)"

st.sidebar.markdown("**ĐIỀU HƯỚNG HỆ THỐNG**")

if st.sidebar.button("  NOW — Real-time State", use_container_width=True):
    st.session_state["menu_selection"] = " NOW (Control Room)"

if st.sidebar.button(" NEXT — Risk Timeline", use_container_width=True):
    st.session_state["menu_selection"] = "NEXT (Risk Timeline)"

if st.sidebar.button("  WHAT-IF — Scenario Lab", use_container_width=True):
    st.session_state["menu_selection"] = " WHAT-IF (Scenario Lab)"

if st.sidebar.button("  Validation & Trust", use_container_width=True):
    st.session_state["menu_selection"] = " Validation & Trust"

menu = st.session_state["menu_selection"]

st.sidebar.markdown("---")
st.sidebar.caption(f"Snapshot dữ liệu: {state['as_of']}")

# ==========================================
# MAIN CONTENT
# ==========================================

if menu == " NOW (Control Room)":
    st.markdown("#  NOW — Trạng thái tại snapshot")
    st.caption(f"Snapshot: {state['as_of']}")

    if st.button("Làm mới dữ liệu gold"):
        st.rerun()

    queued = state["queued_jobs"]
    active = state["active_jobs"]
    all_jobs = queued + active

    lines = sorted({
        job["request"]["destination_line_id"]
        for job in all_jobs
    })

    selected_line = st.selectbox(
        "Lọc đơn theo line",
        ["Toàn nhà máy", *lines],
    )

    def belongs(job):
        return (
            selected_line == "Toàn nhà máy"
            or job["request"]["destination_line_id"] == selected_line
        )

    selected_queued = [job for job in queued if belongs(job)]
    selected_active = [job for job in active if belongs(job)]

    amr_counts = (
        state.get("resource_counts", {}).get("amr", {})
    )

    cols = st.columns(5)
    cols[0].metric(
        "Đơn mở",
        len(selected_queued) + len(selected_active),
    )
    cols[1].metric("Đơn đang chờ", len(selected_queued))
    cols[2].metric("Đơn đang phục vụ", len(selected_active))
    cols[3].metric(
        "AMR rảnh toàn nhà máy",
        amr_counts.get("idle", "—"),
    )
    cols[4].metric(
        "AMR giữ tại cổng",
        amr_counts.get("blocked", "—"),
    )

    st.markdown("### Kho → AMR → Packer → AGV → Line")

    for column, stage in zip(
        st.columns(3), ("amr", "packer", "agv")
    ):
        column.metric(
            f"{stage.upper()} — đang chờ",
            sum(job["stage"] == stage for job in selected_queued),
        )
        column.metric(
            f"{stage.upper()} — đang phục vụ",
            sum(job["stage"] == stage for job in selected_active),
        )

    resources = state["resources"]

    ratios = {
        stage: (
            resource["busy"]
            / (resource["total"] - resource["offline"])
            if resource["total"] > resource["offline"]
            else 0.0
        )
        for stage, resource in resources.items()
    }

    total_queue = len(queued)
    max_ratio = max(ratios.values())
    has_offline = any(
        resource["offline"] > 0
        for resource in resources.values()
    )

    st.markdown("### Nguồn lực và cảnh báo toàn nhà máy")
    st.caption(
        "Nguồn lực dùng chung toàn nhà máy; "
        "bộ lọc line chỉ lọc đơn."
    )

    if (
        has_offline
        or max_ratio >= thresholds["critical_utilization_min"]
        or total_queue > thresholds["critical_queue_min"]
    ):
        st.error(
            "CRITICAL — nguồn lực offline, "
            "mức bận hoặc hàng đợi vượt ngưỡng."
        )
    elif (
        max_ratio >= thresholds["warning_utilization_min"]
        or total_queue >= thresholds["warning_queue_min"]
    ):
        st.warning(
            "WARNING — mức bận hoặc hàng đợi "
            "đạt ngưỡng cảnh báo."
        )
    else:
        st.success(
            "NORMAL — mức bận và hàng đợi trong ngưỡng."
        )

    st.dataframe(pd.DataFrame([
        {
            "Khâu": stage.upper(),
            "Tổng": resource["total"],
            "Offline": resource["offline"],
            "Job đang phục vụ": resource["busy"],
            "Tỷ lệ bận": ratios[stage],
        }
        for stage, resource in resources.items()
    ]))

    rows = []

    for status, jobs in (
        ("Đang chờ", selected_queued),
        ("Đang phục vụ", selected_active),
    ):
        for job in jobs:
            request = job["request"]
            note = ""

            if job.get("completion_unconfirmed"):
                note = "Có thể đã xong, chờ Excel"
            elif job.get("overdue"):
                note = "Quá thời gian ước lượng"

            rows.append({
                "Đơn": request["request_id"],
                "Line": request["destination_line_id"],
                "Khâu": job["stage"].upper(),
                "Trạng thái": status,
                "Còn lại ước lượng (phút)": job.get(
                    "est_remaining_min"
                ),
                "Ghi chú": note,
            })

    st.markdown("### Các đơn đang mở")

    if rows:
        st.dataframe(pd.DataFrame(rows))
    else:
        st.info("Không có đơn mở trong phạm vi đã chọn.")

    st.markdown("### Độ tươi và chất lượng dữ liệu")

    st.dataframe(pd.DataFrame([
        {
            "Nguồn": name,
            "Loại": info["source_type"],
            "Cập nhật cuối": info["last_update"],
            "Tuổi dữ liệu (phút)": info["age_min"],
            "Trạng thái": "STALE" if info["stale"] else "FRESH",
        }
        for name, info in state["source_freshness"].items()
    ]))

    cols = st.columns(3)
    cols[0].metric("Dòng cách ly", quality["quarantine_total"])

    link_rate = quality["link_rate"]["overall"]["rate"]
    estimate_ratio = quality["estimated"]["open_orders_estimate_ratio"]

    cols[1].metric(
        "Tỷ lệ nối ID",
        f"{link_rate:.0%}" if link_rate is not None else "—",
    )
    cols[2].metric(
        "Đơn có ước lượng",
        f"{estimate_ratio:.0%}" if estimate_ratio is not None else "—",
    )

elif menu == "NEXT (Risk Timeline)":
    st.markdown("# NEXT — Timeline rủi ro")
    results, cfg, as_of = run_dashboard_scenarios()
    baseline = results["BASELINE"]

    st.markdown("### Dự báo với nguồn lực hiện tại")
    metrics = baseline["metrics"]
    c1, c2 = st.columns(2)
    c1.metric("Giao hoàn tất trong horizon", metrics["throughput_count"])
    c2.metric("Chưa hoàn tất cuối horizon", metrics["unfinished_count"])

    limits = cfg["bottleneck"]
    rows = []

    for point in baseline["timeline"]:
        queues = point["queue_length"]
        peak = max(queues.values())

        # Cảnh báo theo queue tại từng mốc lấy mẫu của Twin.
        if peak >= limits["peak_queue_severe"]:
            risk = "Cao"
        elif peak >= limits["peak_queue_notable"]:
            risk = "Trung bình"
        else:
            risk = "Thấp"

        rows.append({
            "Thời gian": as_of + pd.Timedelta(
                minutes=point["time_min"]
            ),
            "AMR": queues["amr"],
            "Packer": queues["packer"],
            "AGV": queues["agv"],
            "Đã giao": point["delivered_count"],
            "Đã đến nhưng chưa xong": point["unfinished_arrived_count"],
            "Rủi ro queue": risk,
        })

    timeline = pd.DataFrame(rows)
    st.line_chart(
        timeline.set_index("Thời gian")[["AMR", "Packer", "AGV"]]
    )
    st.dataframe(timeline, use_container_width=True, hide_index=True)
    st.caption(
        "Biểu đồ thể hiện số lượt đang chờ tại từng khâu. "
        "Mức rủi ro được tính theo queue tại các mốc lấy mẫu, "
        "chưa đánh giá nguy cơ trễ hạn vật tư."
    )
elif menu == " WHAT-IF (Scenario Lab)":
    st.markdown("# WHAT-IF — So sánh kịch bản")
    results, cfg, as_of = run_dashboard_scenarios()

    weights = dict(cfg["ranking_weights"])

    # Không dùng KPI thiếu dữ liệu để chấm điểm.
    missing_time_kpis = [
        key
        for key in ("avg_lead_time_min", "avg_total_wait_min")
        if any(
            result["metrics"].get(key) is None
            for result in results.values()
        )
    ]
    for key in missing_time_kpis:
        weights[key] = 0.0

    if missing_time_kpis:
        st.info(
            "Một số kịch bản chưa có lượt giao hoàn tất. "
            "Lead/wait thiếu dữ liệu được hiển thị là —; "
            "KPI tương ứng không tham gia chấm điểm."
        )

    ranked = rank(results, weights=weights)

    summary = []
    for item in ranked:
        metrics = results[item.scenario_id]["metrics"]
        summary.append({
            "Hạng": item.rank,
            "Kịch bản": item.scenario_id,
            "Điểm": round(item.total_score, 3),
            "Giao hoàn tất": metrics["throughput_count"],
            "Chênh lệch giao so baseline": item.kpi_deltas[
                "throughput_count"
            ],
            "Chưa hoàn tất": metrics["unfinished_count"],
            "Lead (phút)": metrics["avg_lead_time_min"],
            "Wait (phút)": metrics["avg_total_wait_min"],
            "Bottleneck": item.bottleneck.primary or "—",
        })

    st.dataframe(
        pd.DataFrame(summary),
        use_container_width=True,
        hide_index=True,
    )

    st.markdown("### Chi tiết KPI")
    for item in ranked:
        metrics = results[item.scenario_id]["metrics"]
        with st.expander(
            f"#{item.rank} · {item.scenario_id} · "
            f"Điểm {item.total_score:.3f}",
            expanded=item.rank == 1,
        ):
            cols = st.columns(4)
            cols[0].metric(
                "Giao hoàn tất (lượt)",
                metrics["throughput_count"],
                f"{item.kpi_deltas['throughput_count']:+.0f}",
            )
            cols[1].metric(
                "Chưa hoàn tất",
                metrics["unfinished_count"],
                f"{item.kpi_deltas['unfinished_count']:+.0f}",
                delta_color="inverse",
            )
            for col, key, label in (
                (cols[2], "avg_lead_time_min", "Lead (phút)"),
                (cols[3], "avg_total_wait_min", "Wait (phút)"),
            ):
                value = metrics[key]
                col.metric(
                    label,
                    "—" if value is None else f"{value:.1f}",
                    (
                        f"{item.kpi_deltas[key]:+.1f}"
                        if key not in missing_time_kpis else None
                    ),
                    delta_color="inverse",
                )

            # Explanation hiện tại coi None là 0, nên tránh hiển thị
            # giải thích thời gian sai khi có KPI thiếu dữ liệu.
            if missing_time_kpis:
                st.write(item.bottleneck.explanation)
            else:
                st.text(item.explanation)
                
elif menu == " Validation & Trust":
    st.markdown("#  Validation & Trust")

    try:
        report = load_validation_report()
    except (OSError, ValueError, KeyError, TypeError) as error:
        st.error(f"Không đọc được báo cáo Phase 8: {error}")
        st.stop()

    st.caption(
        "Backtest với demand thực tế của Generator; "
        "tham số được hiệu chỉnh trên các ngày train riêng."
    )

    name = st.selectbox(
        "Bộ tham số",
        ["CALIBRATED", "DEFAULT"],
    )
    model = report["models"][name]

    rate = model["bottleneck_agreement_pct"]
    st.metric(
        "Bottleneck agreement theo ngày",
        f"{rate:.1f}%",
    )

    if model["gate"]["passed"]:
        st.success(
            "Đạt mục tiêu agreement ≥80% "
            "trên ít nhất 5 ngày test."
        )
    else:
        st.warning(
            "Chưa đạt mục tiêu bottleneck agreement."
        )

    st.subheader("Sai số theo cửa sổ")

    st.dataframe(
        pd.DataFrame([
            {
                "KPI": key,
                "MAE": value["mae"],
                "MAPE (%)": value["mape_pct"],
                "Mẫu MAE": value["valid_count"],
                "Mẫu MAPE": value["percentage_count"],
                "Tổng cửa sổ": value["total_count"],
            }
            for key, value in model["summary"].items()
        ]),
        use_container_width=True,
    )

    st.subheader("Trước và sau hiệu chỉnh")
    st.caption(
        "mae_delta < 0 nghĩa là sai số giảm; "
        "> 0 nghĩa là tăng."
    )
    st.dataframe(
        pd.DataFrame(report["comparison"]),
        use_container_width=True,
    )

    st.subheader("Bottleneck từng ngày")
    st.table(pd.DataFrame([
        {
            "Ngày": day["date"],
            "Seed": day["seed"],
            "Actual": day["actual_primary"] or "Không nghẽn",
            "Predicted": (
                day["predicted_primary"] or "Không nghẽn"
            ),
            "Khớp": day["match"],
        }
        for day in model["days"]
    ]))

    day = st.selectbox(
        "Ngày xem biểu đồ",
        [item["date"] for item in model["days"]],
    )
    windows = [
        item for item in model["windows"]
        if item["date"] == day
    ]

    st.subheader("Throughput trong từng cửa sổ")
    st.line_chart(
        pd.DataFrame([
            {
                "Thời điểm": pd.Timestamp(item["as_of"]),
                "Actual": (
                    item["actual_metrics"]["throughput_count"]
                ),
                "Predicted": (
                    item["predicted_metrics"]["throughput_count"]
                ),
            }
            for item in windows
        ]).set_index("Thời điểm")
    )

    at = st.selectbox(
        "Snapshot xem queue",
        [item["as_of"] for item in windows],
    )
    window = next(
        item for item in windows
        if item["as_of"] == at
    )
    stage = st.selectbox(
        "Stage",
        ["amr", "packer", "agv"],
    )

    st.line_chart(
        pd.DataFrame({
            "Phút từ snapshot": [
                point["time_min"]
                for point in window["actual_timeline"]
            ],
            "Actual": [
                point["queue"][stage]
                for point in window["actual_timeline"]
            ],
            "Predicted": [
                point["queue"][stage]
                for point in window["predicted_timeline"]
            ],
        }).set_index("Phút từ snapshot")
    )

    st.subheader("Kết luận từ kết quả")
    st.write(model["narrative"])

    with st.expander("Phạm vi và giới hạn đánh giá"):
        for limitation in report["limitations"]:
            st.write(f"• {limitation}")

        # ==========================================
# CONTRACT ADAPTER LAYER (Cầu nối dữ liệu thật)
# ==========================================
RESULTS_DIR = Path("data/results")

def load_simulation_result_timeline():
    """
    Đọc SimulationResult contract từ Twin Engine của Nhân.
    Nếu file thật chưa tồn tại, trả về DataFrame timeline giả lập.
    """
    sim_file = RESULTS_DIR / "simulation_timeline.json"
    if sim_file.exists():
        try:
            df = pd.read_json(sim_file)
            if not df.empty:
                return df
        except Exception:
            pass
            
    # Fallback Mock
    return pd.DataFrame([
        {"time": "14:30", "queue_length": 8, "risk_level": "Low", "status": "Normal"},
        {"time": "14:45", "queue_length": 14, "risk_level": "Medium", "status": "Warning: Queue building up"},
        {"time": "15:00", "queue_length": 22, "risk_level": "High", "status": "Bottleneck at Pack Station"},
        {"time": "15:15", "queue_length": 18, "risk_level": "Medium", "status": "Recovering"},
        {"time": "15:30", "queue_length": 9, "risk_level": "Low", "status": "Normal"},
    ])