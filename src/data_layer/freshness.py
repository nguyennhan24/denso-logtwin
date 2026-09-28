"""freshness.py — Mỗi feed được cập nhật lần cuối lúc nào (tính tại as_of) và trễ bao lâu.

Vì sao cần: tab NOW phải cho người vận hành biết state đang dựa trên dữ liệu "tươi" tới đâu.
Excel trễ tới 2 giờ -> nếu không hiển thị độ trễ, người xem sẽ tưởng packer đang rảnh/bận sai.

Loại nguồn:
  stream  — amr, agv: mỗi sự kiện đẩy lên ngay
  event   — wms: một dòng khi tạo phiếu
  manual  — excel: người nhập tay theo đợt
  plan    — production, labor: kế hoạch có từ đầu ngày
"""
from __future__ import annotations

import pandas as pd

from .ingest import FEEDS, RawFeeds, min_to_iso, snapshot

SOURCE_TYPE = {
    "wms": "event", "amr": "stream", "agv": "stream",
    "production": "plan", "labor": "plan", "excel": "manual",
}


def _latest_min(snap: RawFeeds, as_of_min: float) -> dict[str, float]:
    """Mốc mới nhất (phút) mà mỗi feed đã phát ra, không vượt as_of.

    Kế hoạch (production, labor) được công bố lúc đầu ngày -> mốc = 0, dù bên trong có
    các giờ trong tương lai (hour_start 21:00 là giờ kế hoạch, không phải giờ cập nhật).
    Feed chưa có dòng nào thì coi như cập nhật lần cuối lúc đầu ngày.
    """
    def mx(*cols: pd.Series) -> float:
        vals = pd.concat(cols).dropna() if cols else pd.Series(dtype=float)
        vals = vals[vals <= as_of_min]
        return float(vals.max()) if len(vals) else 0.0

    return {
        "wms": mx(snap.wms["created_min"]),
        "amr": mx(snap.amr["ts_min"]),
        "agv": mx(snap.agv["load_min"], snap.agv["deliver_min"]),
        "production": 0.0,
        "labor": 0.0,
        "excel": mx(snap.excel["entered_min"]),
    }


def feed_updated_at(snap: RawFeeds, as_of_min: float) -> dict[str, str]:
    """Đúng 6 key của contract, giá trị ISO 8601 +07:00, không key nào vượt as_of."""
    s = snapshot(snap, as_of_min)
    latest = _latest_min(s, as_of_min)
    return {k: min_to_iso(min(latest[k], as_of_min), s.day_start) for k in FEEDS}


def freshness_report(snap: RawFeeds, as_of_min: float) -> dict[str, dict]:
    """Như feed_updated_at nhưng thêm độ trễ (phút) và loại nguồn — cho báo cáo chất lượng."""
    s = snapshot(snap, as_of_min)
    latest = _latest_min(s, as_of_min)
    return {
        k: {
            "updated_at": min_to_iso(min(latest[k], as_of_min), s.day_start),
            "lag_min": round(max(as_of_min - latest[k], 0.0), 2),
            "source_type": SOURCE_TYPE[k],
        }
        for k in FEEDS
    }
