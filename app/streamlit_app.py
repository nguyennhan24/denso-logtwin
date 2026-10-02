import streamlit as st
import pandas as pd
import numpy as np
from datetime import datetime

# ==========================================
# CẤU HÌNH GIAO DIỆN & CUSTOM CSS
# ==========================================
st.set_page_config(
    page_title="Digital Twin Control Tower — DENSO D2",
    layout="wide",
    initial_sidebar_state="expanded"
)


st.markdown("""
    <style>
    /* Tổng thể font và màu nền */
    .stApp {
        background-color: #0B0F19;
        color: #F3F4F6;
    }
    
    /* Sidebar hiện đại */
    [data-testid="stSidebar"] {
        background-color: #111827;
        border-right: 1px solid #1F2937;
    }
    
    /* Tùy chỉnh các nút bấm điều hướng sidebar thành card/box */
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
    
    /* Metric Cards dạng hộp nổi */
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
    
    /* Tiêu đề mục */
    h1, h2, h3 {
        color: #F9FAFB;
        font-family: 'Inter', sans-serif;
    }
    </style>
""", unsafe_allow_html=True)

# ==========================================
# MOCK DATA & CONTRACTS
# ==========================================
@st.cache_data
def load_mock_current_state():
    return {
        "timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "active_amrs": 8,
        "busy_amrs": 6,
        "offline_amrs": 1,
        "active_workers": 3,
        "queue_length_packs": 12,
        "avg_wait_time_min": 4.5,
        "throughput_rate": 45,
        "exceptions": [
            {"id": "EX-001", "line": "Line A", "type": "AMR Battery Low", "severity": "Medium", "time": "14:15"},
            {"id": "EX-002", "line": "Line C", "type": "Queue Overflow (>15 packs)", "severity": "High", "time": "14:22"},
        ]
    }

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
def load_mock_scenarios():
    return {
        "Baseline": {"amr": 8, "workers": 3, "throughput": 45, "avg_wait": 4.5, "bottleneck_risk": "Medium"},
        "Scenario A (+2 AMR)": {"amr": 10, "workers": 3, "throughput": 52, "avg_wait": 3.1, "bottleneck_risk": "Low"},
        "Scenario B (+1 Worker)": {"amr": 8, "workers": 4, "throughput": 58, "avg_wait": 2.2, "bottleneck_risk": "Low"},
        "Scenario C (+2 AMR & +1 Worker)": {"amr": 10, "workers": 4, "throughput": 68, "avg_wait": 1.2, "bottleneck_risk": "Very Low"}
    }

state = load_mock_current_state()

# ==========================================
# SIDEBAR NAVIGATION 
# ==========================================
st.sidebar.markdown("### 🏭 NEXUS LOGISTICS")
st.sidebar.markdown("<p style='color: #9CA3AF; font-size: 0.8rem;'>Digital Twin Control Tower v2.4</p>", unsafe_allow_html=True)
st.sidebar.markdown("---")

if "menu_selection" not in st.session_state:
    st.session_state["menu_selection"] = "🔴 NOW (Control Room)"

st.sidebar.markdown("**ĐIỀU HƯỚNG HỆ THỐNG**")

if st.sidebar.button("🔴  NOW — Real-time State", use_container_width=True):
    st.session_state["menu_selection"] = "🔴 NOW (Control Room)"

if st.sidebar.button("🔮  NEXT — Risk Timeline", use_container_width=True):
    st.session_state["menu_selection"] = "🔮 NEXT (Risk Timeline)"

if st.sidebar.button("⚡  WHAT-IF — Scenario Lab", use_container_width=True):
    st.session_state["menu_selection"] = "⚡ WHAT-IF (Scenario Lab)"

if st.sidebar.button("📊  Validation & Trust", use_container_width=True):
    st.session_state["menu_selection"] = "📊 Validation & Trust"

menu = st.session_state["menu_selection"]

st.sidebar.markdown("---")
st.sidebar.success("🟢 **System Status:** Online\n\n⚡ **Engine:** Synchronized")

# ==========================================
# MAIN CONTENT
# ==========================================

if menu == "🔴 NOW (Control Room)":
    st.markdown("# 🔴 NOW — Trạng thái Vận hành Thời gian thực")
    st.markdown("Giám sát trực quan luồng vật lý machine-to-people và các điểm nghẽn tức thời trong nhà máy.")
    st.markdown("---")
    
    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("AMR Hoạt động", f"{state['busy_amrs']}/{state['active_amrs']}", delta="-1 offline", delta_color="inverse")
    col2.metric("Nhân sự bốc Pack", f"{state['active_workers']} người")
    col3.metric("Độ dài hàng đợi", f"{state['queue_length_packs']} thùng", delta="+3 định mức", delta_color="inverse")
    col4.metric("Thời gian chờ TB", f"{state['avg_wait_time_min']} phút")
    col5.metric("Throughput", f"{state['throughput_rate']} packs/h", delta="+4.2%")
    
    st.markdown("<br>", unsafe_allow_html=True)
    
    col_left, col_right = st.columns([2, 1])
    with col_left:
        st.markdown("### 🗺️ Sơ đồ Luồng Vật lý Kho vận (Machine-to-People)")
        st.info("💡 **Trạm trung chuyển trung tâm:** Đang ghi nhận mật độ dồn ứ cao tại Line C do thao tác bốc pack thủ công bị chậm nhịp so với tốc độ cấp hàng của robot AMR.")
        
        chart_data = pd.DataFrame(
            np.random.randn(20, 2) / [50, 50] + [21.0285, 105.8542],
            columns=['lat', 'lon']
        )
        st.map(chart_data, zoom=14)

    with col_right:
        st.markdown("### ⚠️ Cảnh báo & Bất thường")
        for ex in state["exceptions"]:
            border_color = "#EF4444" if ex["severity"] == "High" else "#F59E0B"
            st.markdown(f"""
                <div style="background-color: #111827; border-left: 4px solid {border_color}; padding: 10px; border-radius: 6px; margin-bottom: 10px;">
                    <b style="color: #F3F4F6;">{ex['line']}</b> — <span style="color: {border_color};">{ex['type']}</span><br>
                    <small style="color: #9CA3AF;">Thời gian: {ex['time']} | Mức độ: {ex['severity']}</small>
                </div>
            """, unsafe_allow_html=True)
        
        st.markdown("<br>", unsafe_allow_html=True)
        if st.button("🔄 Làm mới Trạng thái Dữ liệu"):
            st.rerun()

elif menu == "🔮 NEXT (Risk Timeline)":
    st.markdown("# 🔮 NEXT — Dự báo Năng lực & Timeline Rủi ro")
    st.markdown("Phân tích trước các xung đột giữa kế hoạch sản xuất tương lai và công suất nguồn lực.")
    st.markdown("---")
    
    timeline_df = load_mock_simulation_timeline()
    
    st.markdown("### 📈 Biểu đồ Xu hướng Hàng đợi Dự báo")
    st.line_chart(timeline_df.set_index("time")["queue_length"], color="#3B82F6")
    
    st.markdown("### ⏱️ Timeline Mốc thời gian & Trạng thái Rủi ro")
    
    for index, row in timeline_df.iterrows():
        risk = row["risk_level"]
        if risk == "High":
            border_c = "#EF4444"
            bg_c = "rgba(239, 68, 68, 0.1)"
        elif risk == "Medium":
            border_c = "#F59E0B"
            bg_c = "rgba(245, 158, 11, 0.1)"
        else:
            border_c = "#10B981"
            bg_c = "rgba(16, 185, 129, 0.1)"
            
        st.markdown(f"""
            <div style="background-color: #111827; border: 1px solid #1F2937; border-left: 5px solid {border_c}; padding: 14px; border-radius: 8px; margin-bottom: 12px;">
                <div style="display: flex; justify-content: space-between; align-items: center;">
                    <b style="font-size: 1.1rem; color: #F3F4F6;">⏰ {row['time']}</b>
                    <span style="background-color: {bg_c}; color: {border_c}; padding: 3px 10px; border-radius: 12px; font-size: 0.8rem; font-weight: bold;">Rủi ro: {risk}</span>
                </div>
                <hr style="border-color: #1F2937; margin: 8px 0;">
                <p style="margin: 0; color: #9CA3AF;"><b>Độ dài hàng đợi dự kiến:</b> <span style="color: #F3F4F6;">{row['queue_length']} thùng</span></p>
                <p style="margin: 4px 0 0 0; color: #9CA3AF;"><b>Trạng thái hệ thống:</b> <i style="color: #E5E7EB;">{row['status']}</i></p>
            </div>
        """, unsafe_allow_html=True)

elif menu == "⚡ WHAT-IF (Scenario Lab)":
    st.markdown("# ⚡ WHAT-IF — Mô phỏng Kịch bản & So sánh Giải pháp")
    st.markdown("Đánh giá tác động thay đổi chính sách, thiết bị hoặc nhân sự trước khi triển khai thực tế.")
    st.markdown("---")
    
    scenarios = load_mock_scenarios()
    selected_scenarios = st.multiselect(
        "Chọn các kịch bản muốn so sánh:",
        options=list(scenarios.keys()),
        default=["Baseline", "Scenario A (+2 AMR)", "Scenario B (+1 Worker)"]
    )
    
    if selected_scenarios:
        comparison_data = []
        for s in selected_scenarios:
            row = {"Kịch bản": s, **scenarios[s]}
            comparison_data.append(row)
        
        comp_df = pd.DataFrame(comparison_data)
        st.markdown("### 📊 Bảng so sánh hiệu năng")
        st.dataframe(comp_df, use_container_width=True)
        
        st.success("💡 **Đề xuất chiến lược tối ưu:** Tăng cường 1 nhân sự bốc pack (Scenario B) mang lại hiệu quả cải thiện throughput vượt trội với chi phí tối ưu hơn so với việc đầu tư thêm đội xe AMR.")

elif menu == "📊 Validation & Trust":
    st.markdown("# 📊 Validation & Trust — Kiểm định Độ tin cậy Mô hình")
    st.markdown("Minh bạch hóa cơ sở dữ liệu và các chỉ số backtest để đảm bảo độ tin cậy tuyệt đối.")
    st.markdown("---")
    
    col_v1, col_v2 = st.columns(2)
    with col_v1:
        st.markdown("### 📈 Chỉ số Sai số Lịch sử (Backtest Metrics)")
        metrics_df = pd.DataFrame([
            {"Chỉ số đánh giá": "Throughput Error", "Sai số (MAPE)": "4.2%", "Trạng thái": "Đạt chuẩn (< 5%)"},
            {"Chỉ số đánh giá": "Average Wait Time Error", "Sai số (MAPE)": "6.8%", "Trạng thái": "Đạt chuẩn (< 10%)"},
            {"Chỉ số đánh giá": "Bottleneck Agreement Rate", "Tỷ lệ khớp": "91.5%", "Trạng thái": "Đạt chuẩn (> 90%)"}
        ])
        st.table(metrics_df)
        
    with col_v2:
        st.markdown("### 📝 Cơ sở Khoa học & Vận hành")
        st.markdown("""
        * **Calibrated Real-world Data:** Tham số tốc độ AMR và thời gian thao tác bốc pack được hiệu chuẩn trực tiếp từ lịch sử vận hành thực tế.
        * **Discrete-Event Simulation (DES):** Mô phỏng chính xác cơ chế hàng đợi tại từng nút thắt cổ chai thay vì ước lượng tuyến tính đơn giản.
        * **High Validation Accuracy:** Tỷ lệ dự báo chính xác điểm nghẽn đạt trên 91% trong các bài kiểm tra tải cao giả lập.
        """)