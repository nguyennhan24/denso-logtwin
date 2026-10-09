"""Bước 5 — freshness: dữ liệu "tươi" tới đâu, ở hai mức.

- Theo nguồn: mỗi feed có loại (stream/batch/manual), last_update (<= as_of), age_min, stale. Ngưỡng ở config.
- Theo đơn: lần cuối thấy một sự kiện của đơn, đã bao lâu, có quá ngưỡng không.
"""
from __future__ import annotations

import pandas as pd

from . import FEEDS
from .ingest import PUBLISH_COLUMN

# Cột cho biết "nguồn vừa gửi dữ liệu mới" của từng feed
UPDATE_COLUMNS = {
    "wms": ["created_at"],
    "amr": ["ts"],
    "agv": ["load_epoch", "deliver_epoch"],
    "excel": ["entered_at"],         # thời điểm nhập, không phải thời điểm đóng gói
}
# Mốc sự kiện của một đơn trong journey
ORDER_EVENT_COLUMNS = ["created_at", "amr_dispatch_at", "amr_arrive_gate_at", "packer_start_at",
                       "packer_end_at", "agv_load_at", "delivered_at"]


def _last_update(name: str, df: pd.DataFrame, as_of: pd.Timestamp):
    if name in PUBLISH_COLUMN:
        # Kế hoạch (production/labor) không có cột giờ cập nhật: giả định công bố lúc mốc sớm nhất trong bảng
        col = df[PUBLISH_COLUMN[name]].dropna()
        return col.min() if len(col) else None
    times = pd.concat([df[c] for c in UPDATE_COLUMNS[name] if c in df]).dropna()
    times = times[times <= as_of]          # ingest đã cắt, kẹp thêm cho chắc
    return times.max() if len(times) else None


def source_freshness(feeds: dict[str, pd.DataFrame], as_of: pd.Timestamp, cfg: dict) -> dict[str, dict]:
    """Dùng feed sau ingest (trước validate): dòng lỗi vẫn chứng tỏ nguồn còn đang gửi dữ liệu."""
    out = {}
    for name in FEEDS:
        conf = cfg["feeds"][name]
        last = _last_update(name, feeds[name], as_of)
        age = None if last is None else round((as_of - last).total_seconds() / 60, 2)
        out[name] = {
            "source_type": conf["type"],
            "last_update": None if last is None else last.isoformat(),
            "age_min": age,
            "stale_after_min": conf["stale_after_min"],
            # chưa có dữ liệu nào cũng tính là stale
            "stale": bool(age is None or age > conf["stale_after_min"]),
        }
    return out


def attach_source_freshness(state: dict, fresh: dict, as_of: pd.Timestamp, cfg: dict) -> dict:
    """Gắn freshness theo nguồn vào CurrentLogisticsState.

    Contract đòi đủ 6 timestamp. Nguồn chưa có dữ liệu nào (vd. Excel trước 08:00) thì dùng giờ đầu ngày
    và vẫn có stale = true trong source_freshness (xem docs/contract_change_proposal.md)."""
    h, m = map(int, cfg["day_start"].split(":"))
    day_start = min(as_of.normalize() + pd.Timedelta(hours=h, minutes=m), as_of)
    state["feed_updated_at"] = {
        name: f["last_update"] or day_start.isoformat() for name, f in fresh.items()
    }
    state["source_freshness"] = fresh
    return state


def add_order_freshness(order_status: pd.DataFrame, journey: pd.DataFrame, as_of: pd.Timestamp,
                        cfg: dict) -> pd.DataFrame:
    """Thêm last_update_at, age_min, stale vào order_status. Đơn lâu không có sự kiện mới -> stale."""
    events = journey.set_index("request_id")[ORDER_EVENT_COLUMNS]
    last = events.max(axis=1)
    out = order_status.copy()
    out["last_update_at"] = last.reindex(out["request_id"]).set_axis(out.index)
    out["age_min"] = (as_of - out["last_update_at"]).dt.total_seconds() / 60
    out["stale"] = out["age_min"] > cfg["order_stale_after_min"]
    return out
