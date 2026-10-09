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
from src.demand.forecast import forecast_demand, detect_spikes, build_risk_timeline
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
    st.markdown("# NEXT — Dự báo Năng lực & Timeline Rủi ro")
    st.markdown("Phân tích trước các xung đột giữa kế hoạch sản xuất tương lai và công suất nguồn lực. Chạy bằng **Forecast Engine (Code của Cường)**.")
    st.markdown("---")
    
    # 1. Đọc dữ liệu mô phỏng Production Plan
    plan_df = pd.DataFrame([
        {"hour_idx": 0, "line_id": "Line A", "product": "P100", "planned_qty": 450},
        {"hour_idx": 1, "line_id": "Line B", "product": "P200", "planned_qty": 650},
    ])
    bom_mock = {
        "P100": {"bom_packs_per_unit": {"M01": 0.2}},
        "P200": {"bom_packs_per_unit": {"M04": 0.3}}
    }
    
    # 2. Chạy logic Cường
    future_reqs = forecast_demand(plan_df, bom_mock, horizon_hours=2)
    spikes = detect_spikes(future_reqs, window_min=30.0, spike_threshold=1.0)
    # Lấy state từ code hiện tại của Hải Anh để đồng bộ logic
    timeline_c = build_risk_timeline(future_reqs, spikes, state, interval_min=30.0)
    
    # 3. Hiển thị UI
    st.markdown("### ⏱️ Timeline Mốc thời gian & Trạng thái Rủi ro")
    
    for row in timeline_c:
        risk = row["risk_level"].capitalize()
        if risk == "High" or risk == "Critical":
            border_c = "#EF4444"
            bg_c = "rgba(239, 68, 68, 0.1)"
            risk = "High"
        elif risk == "Medium":
            border_c = "#F59E0B"
            bg_c = "rgba(245, 158, 11, 0.1)"
        else:
            border_c = "#10B981"
            bg_c = "rgba(16, 185, 129, 0.1)"
            
        time_label = f"Phút {row['time_min']} - {row['time_min'] + 30}"
        
        st.markdown(f"""
            <div style="background-color: #111827; border: 1px solid #1F2937; border-left: 5px solid {border_c}; padding: 14px; border-radius: 8px; margin-bottom: 12px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <b style="font-size: 1.1rem; color: #F3F4F6;">⏰ {time_label}</b>
                    <span style="background-color: {bg_c}; color: {border_c}; padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: bold;">Rủi ro: {risk}</span>
                </div>
                <hr style="border-color: #1F2937; margin: 8px 0;">
                <p style="margin: 0; color: #9CA3AF;"><b>Số lượt yêu cầu vật tư (Requests):</b> <span style="color: #F3F4F6;">{row['expected_requests']} lượt</span></p>
                <p style="margin: 4px 0 0 0; color: #9CA3AF;"><b>Đánh giá tự động:</b> <i style="color: #E5E7EB;">{row['note']}</i></p>
            </div>
        """, unsafe_allow_html=True)

elif menu == " WHAT-IF (Scenario Lab)":
    st.markdown("#  WHAT-IF — Mô phỏng Kịch bản & Tùy chỉnh Động")
    st.markdown("Hệ thống **Decision Engine (Code của Cường)** sẽ chấm điểm tự động và xếp hạng các kịch bản!")
    st.markdown("---")
    
    # 1. Đọc trọng số từ config của Cường
    with open("config/scenarios.yaml", "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
        
    st.markdown("### 🏆 Bảng Xếp hạng Kịch bản")
    
    # 2. Giả lập kết quả trả về từ Twin (vì Nhân chưa code)
    fake_results = {
        "BASELINE": {
            "metrics": {
                "throughput_count": 45, "avg_lead_time_min": 25.0, "avg_total_wait_min": 15.0,
                "unfinished_count": 12, "utilization": {"amr": 0.85, "packer": 0.5, "agv": 0.4},
                "peak_queue": {"amr": 6, "packer": 1, "agv": 0}
            }
        },
        "+2AMR (Đề xuất)": {
            "metrics": {
                "throughput_count": 55, "avg_lead_time_min": 17.0, "avg_total_wait_min": 8.0,
                "unfinished_count": 2, "utilization": {"amr": 0.65, "packer": 0.65, "agv": 0.5},
                "peak_queue": {"amr": 2, "packer": 3, "agv": 1}
            }
        },
        "+2WORKER": {
            "metrics": {
                "throughput_count": 48, "avg_lead_time_min": 24.0, "avg_total_wait_min": 14.0,
                "unfinished_count": 9, "utilization": {"amr": 0.88, "packer": 0.3, "agv": 0.45},
                "peak_queue": {"amr": 7, "packer": 0, "agv": 0}
            }
        }
    }
    
    # 3. CHẠY HÀM CỦA CƯỜNG
    ranked = rank(fake_results, weights=cfg["ranking_weights"])
    
    # 4. Hiển thị UI
    for r in ranked:
        is_top = (r.rank == 1)
        border = "2px solid #3B82F6" if is_top else "1px solid #1F2937"
        
        with st.container():
            st.markdown(f"""
            <div style="background-color: #111827; border: {border}; padding: 20px; border-radius: 10px; margin-bottom: 20px;">
                <h3 style="margin-top: 0; color: {'#3B82F6' if is_top else '#F3F4F6'};">
                    { "🏆 Hạng 1:" if is_top else f"Hạng {r.rank}:" } {r.scenario_id} (Điểm: {r.total_score:.2f})
                </h3>
                <p style="color: #9CA3AF; white-space: pre-wrap;">{r.explanation}</p>
            </div>
            """, unsafe_allow_html=True)
            
            # Hiển thị số liệu chênh lệch (Delta)
            c1, c2, c3, c4 = st.columns(4)
            c1.metric("Throughput", f"{fake_results[r.scenario_id]['metrics']['throughput_count']} thùng", f"{r.kpi_deltas.get('throughput_count', 0):+.0f} thùng")
            c2.metric("Lead Time", f"{fake_results[r.scenario_id]['metrics']['avg_lead_time_min']:.1f} m", f"{r.kpi_deltas.get('avg_lead_time_min', 0):+.1f} m", delta_color="inverse")
            c3.metric("Wait Time", f"{fake_results[r.scenario_id]['metrics']['avg_total_wait_min']:.1f} m", f"{r.kpi_deltas.get('avg_total_wait_min', 0):+.1f} m", delta_color="inverse")
            c4.metric("Unfinished", f"{fake_results[r.scenario_id]['metrics']['unfinished_count']}", f"{r.kpi_deltas.get('unfinished_count', 0):+.0f}", delta_color="inverse")
            st.markdown("<br>", unsafe_allow_html=True)

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