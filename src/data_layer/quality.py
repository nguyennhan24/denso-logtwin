"""quality.py — Báo cáo chất lượng dữ liệu tại as_of (cho Dashboard và trang Validation).

Vì sao quan trọng: nhà máy thật thường không biết dữ liệu của mình "bẩn" tới đâu. Báo cáo này
cho thấy bao nhiêu dòng bị cách ly, ghép ID được bao nhiêu, Excel phủ được bao nhiêu và
state hiện tại đang phải ước lượng bao nhiêu lượt — tức là mức độ tin cậy của tab NOW.

Mỗi chỉ số có `label` tiếng Việt để Dashboard hiển thị thẳng, không cần tự đặt tên.
"""
from __future__ import annotations

import pandas as pd

from .freshness import freshness_report
from .ingest import FEEDS, RawFeeds, min_to_iso
from .mapper import match_stats
from .state_builder import infer_wip

FEED_LABEL = {
    "wms": "Phiếu kho (WMS)", "amr": "Sự kiện AMR", "agv": "Job AGV",
    "production": "Kế hoạch sản xuất", "labor": "Bảng phân ca", "excel": "Log đóng gói (Excel)",
}


def _metric(key: str, label: str, value, unit: str = "") -> dict:
    return {"key": key, "label": label, "value": value, "unit": unit}


def dq_report(snap: RawFeeds, clean: RawFeeds, quarantine: pd.DataFrame,
              journey: pd.DataFrame, as_of_min: float, cfg: dict) -> dict:
    """Báo cáo chất lượng dữ liệu tại as_of.

    Nhận: snapshot thô (trước kiểm tra), feed sạch, bảng quarantine, journey, as_of, config.
    Trả:  dict gồm `metrics` (danh sách chỉ số phẳng, có nhãn) và các mục chi tiết
          rows / match / excel_coverage / estimated / freshness / quarantine_reasons.
    """
    rows = {}
    for f in FEEDS:
        n_in = int(len(getattr(snap, f)))
        n_q = int(quarantine.loc[quarantine["feed"] == f, "row_index"].nunique())
        rows[f] = {"label": FEED_LABEL[f], "rows_in": n_in, "quarantined": n_q,
                   "quarantine_rate": round(n_q / n_in, 4) if n_in else 0.0}

    match = match_stats(clean, journey)

    # Độ phủ Excel: trong các lượt CHẮC CHẮN đã đóng gói xong (đã lên AGV), bao nhiêu lượt
    # có dòng Excel đã nhập. Thiếu = Excel trễ hoặc bị bỏ sót.
    packed = journey[journey["agv_load_min"].notna()]
    n_cov = int(packed["packer_finish_min"].notna().sum())
    released = int(journey["packer_start_min"].notna().sum())
    excel_cov = {
        "packed_known": int(len(packed)),
        "with_excel": n_cov,
        "coverage": round(n_cov / len(packed), 4) if len(packed) else None,
        "released_without_excel": int((journey["packer_start_min"].notna()
                                       & journey["packer_finish_min"].isna()).sum()),
        "released": released,
    }

    wip, aux = infer_wip(clean, journey, as_of_min, cfg)
    fresh = freshness_report(snap, as_of_min)
    reasons = [{"feed": f, "reason": r, "count": int(n)}
               for (f, r), n in quarantine.groupby(["feed", "reason"]).size().items()]

    total_in = sum(r["rows_in"] for r in rows.values())
    total_q = sum(r["quarantined"] for r in rows.values())
    metrics = [
        _metric("rows_in", "Tổng số dòng đọc vào", total_in, "dòng"),
        _metric("rows_quarantined", "Số dòng bị cách ly", total_q, "dòng"),
        _metric("match_amr", "Tỷ lệ ghép AMR → WMS", match["amr"]["rate"], "tỷ lệ"),
        _metric("match_agv", "Tỷ lệ ghép AGV → WMS", match["agv"]["rate"], "tỷ lệ"),
        _metric("match_excel", "Tỷ lệ ghép Excel → WMS", match["excel"]["rate"], "tỷ lệ"),
        _metric("excel_coverage", "Độ phủ Excel (lượt đã lên AGV có log đóng gói)",
                excel_cov["coverage"], "tỷ lệ"),
        _metric("wip_total", "Số lượt đang trong hệ thống", int(len(wip)), "lượt"),
        _metric("estimated_jobs", "Số lượt phải ước lượng (chưa có Excel)", aux["n_estimated"], "lượt"),
    ] + [
        _metric(f"lag_{f}", f"Độ trễ {FEED_LABEL[f]}", fresh[f]["lag_min"], "phút") for f in FEEDS
    ]

    return {
        "as_of": min_to_iso(as_of_min, snap.day_start),
        "metrics": metrics,
        "rows": rows,
        "match": match,
        "excel_coverage": excel_cov,
        "estimated": {
            "jobs": aux["n_estimated"],
            "pack_estimate_source": aux["estimates"]["pack"]["source"],
            "pack_n_samples": aux["estimates"]["pack"]["n_samples"],
        },
        "freshness": fresh,
        "quarantine_reasons": reasons,
    }
