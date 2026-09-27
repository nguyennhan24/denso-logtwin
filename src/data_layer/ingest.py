"""ingest.py — Đọc 6 feed thô (BRONZE) và "cắt" theo thời điểm as_of.

Vì sao cần:
  - Mỗi hệ thống ghi giờ một kiểu (ISO, epoch giây, "HH:MM"). Muốn so sánh được thì phải đưa
    hết về MỘT trục chung: t_min = số phút tính từ đầu ngày làm việc (day_start).
  - snapshot(as_of) là cơ chế "event replay": chỉ giữ những gì hệ thống ĐÃ BIẾT tại as_of,
    nhờ đó mọi bước sau không thể vô tình nhìn vào tương lai.

Quy tắc: module này KHÔNG bao giờ đọc thư mục _truth/.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import yaml

FEEDS = ("wms", "amr", "agv", "production", "labor", "excel")
DEFAULT_CONFIG = Path(__file__).resolve().parents[2] / "config" / "data_layer.yaml"

# Cột số nguyên của từng feed. Mọi cột khác giữ dạng chuỗi.
# Đọc tất cả dưới dạng chuỗi trước rồi mới ép kiểu, để một ô bẩn ("abc") không làm hỏng
# cả file: ô đó thành NA và sẽ bị schemas.py đưa vào quarantine kèm lý do.
_INT_COLS = {
    "wms": ["qty_packs"],
    "agv": ["load_epoch", "deliver_epoch"],
    "production": ["planned_qty"],
    "excel": ["pick"],
}


def load_config(path: str | Path | None = None) -> dict:
    """Đọc config/data_layer.yaml (không phải config của Generator)."""
    with open(path or DEFAULT_CONFIG, encoding="utf-8") as f:
        return yaml.safe_load(f)


@dataclass
class RawFeeds:
    """6 feed + bảng master của MỘT ngày. Mỗi DataFrame đã có thêm cột *_min (t_min)."""
    wms: pd.DataFrame
    amr: pd.DataFrame
    agv: pd.DataFrame
    production: pd.DataFrame
    labor: pd.DataFrame
    excel: pd.DataFrame
    master: pd.DataFrame
    date: str                 # "2026-10-01", lấy từ tên thư mục
    day_start: datetime       # thời điểm t_min = 0 (có múi giờ)

    def feeds(self) -> dict[str, pd.DataFrame]:
        """6 feed dạng dict (không gồm master) — tiện để lặp."""
        return {k: getattr(self, k) for k in FEEDS}

    def with_feeds(self, **dfs: pd.DataFrame) -> "RawFeeds":
        """Bản sao với vài feed được thay — RawFeeds coi như bất biến (hàm thuần)."""
        return replace(self, **dfs)


# ---------------------------------------------------------------- thời gian
def day_start_of(date: str, cfg: dict) -> datetime:
    """Thời điểm đầu ngày làm việc của `date`, có múi giờ nhà máy."""
    tz = timezone(timedelta(hours=cfg["tz_offset_hours"]))
    h, m = map(int, cfg["day_start"].split(":"))
    return datetime.fromisoformat(date).replace(hour=h, minute=m, tzinfo=tz)


def _minutes_since(ts: pd.Series, start: datetime) -> pd.Series:
    """Series datetime có múi giờ -> số phút (float) kể từ start. NaT -> NaN."""
    return (ts - pd.Timestamp(start)).dt.total_seconds() / 60.0


def iso_to_min(s: pd.Series, start: datetime) -> pd.Series:
    """Cột ISO 8601 -> t_min. Chuỗi không đọc được -> NaN (schemas sẽ cách ly)."""
    ts = pd.to_datetime(s, format="ISO8601", errors="coerce", utc=True)
    return _minutes_since(ts, start)


def epoch_to_min(s: pd.Series, start: datetime) -> pd.Series:
    """Cột epoch giây (AGV) -> t_min. Ô trống -> NaN (chưa giao)."""
    ts = pd.to_datetime(s.astype("Float64"), unit="s", errors="coerce", utc=True)
    return _minutes_since(ts, start)


def hhmm_to_min(s: pd.Series, start: datetime) -> pd.Series:
    """Cột "HH:MM" (Excel nhập tay) ghép với ngày của thư mục -> t_min, độ phân giải phút."""
    parts = s.astype("string").str.extract(r"^\s*(\d{1,2}):(\d{2})\s*$")
    h = pd.to_numeric(parts[0], errors="coerce")
    m = pd.to_numeric(parts[1], errors="coerce")
    ok = (h < 24) & (m < 60)
    minutes = (h * 60 + m - (start.hour * 60 + start.minute)).where(ok)
    return minutes.astype("float64")


def min_to_iso(t_min: float, start: datetime) -> str:
    """t_min -> ISO 8601 có múi giờ (dùng khi xuất ra ngoài)."""
    return (start + timedelta(minutes=float(t_min))).isoformat(timespec="seconds")


def parse_as_of(value: str, date: str, cfg: dict | None = None) -> float:
    """"10:00" hoặc ISO "2026-10-01T10:00:00+07:00" -> as_of_min.

    "HH:MM" hiểu là giờ đồng hồ tại nhà máy trong ngày `date`.
    """
    cfg = cfg or load_config()
    start = day_start_of(date, cfg)
    value = value.strip()
    if "T" in value or len(value) > 5:
        dt = datetime.fromisoformat(value)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=start.tzinfo)
    else:
        h, m = map(int, value.split(":"))
        dt = start.replace(hour=h, minute=m)
    return (dt - start).total_seconds() / 60.0


# ---------------------------------------------------------------- đọc feed
def _to_int(s: pd.Series) -> pd.Series:
    """Chuỗi -> Int64; ô không phải số nguyên (trống, "abc", "2.5") -> NA."""
    num = pd.to_numeric(s, errors="coerce")
    num = num.where(num.isna() | (num == num.round()))
    return num.astype("Int64")


def _read(path: Path, feed: str) -> pd.DataFrame:
    df = pd.read_csv(path, dtype=str, keep_default_na=False, na_values=[""])
    for c in _INT_COLS.get(feed, []):
        if c in df.columns:
            df[c] = _to_int(df[c])
    return df


def load_day(day_dir: str | Path, cfg: dict | None = None) -> RawFeeds:
    """Đọc 6 feed + master.csv của một ngày và thêm các cột t_min.

    Nhận: đường dẫn data/raw/<date>/. Ngày lấy từ tên thư mục.
    Trả:  RawFeeds. Chỉ đọc đúng 7 file theo tên -> không bao giờ chạm vào _truth/.
    """
    cfg = cfg or load_config()
    day_dir = Path(day_dir)
    date = day_dir.name
    start = day_start_of(date, cfg)

    raw = {name: _read(day_dir / f"{name}.csv", name) for name in FEEDS + ("master",)}

    wms = raw["wms"]
    wms["created_min"] = iso_to_min(wms["created_at"], start)
    wms["need_by_min"] = iso_to_min(wms["need_by"], start)

    amr = raw["amr"]
    amr["ts_min"] = iso_to_min(amr["ts"], start)

    agv = raw["agv"]
    agv["load_min"] = epoch_to_min(agv["load_epoch"], start)
    agv["deliver_min"] = epoch_to_min(agv["deliver_epoch"], start)

    production = raw["production"]
    production["hour_start_min"] = iso_to_min(production["hour_start"], start)

    excel = raw["excel"]
    excel["start_min"] = hhmm_to_min(excel["start"], start)
    excel["end_min"] = hhmm_to_min(excel["end"], start)
    excel["entered_min"] = iso_to_min(excel["entered_at"], start)

    return RawFeeds(**raw, date=date, day_start=start)


# ---------------------------------------------------------------- snapshot
def snapshot(raw: RawFeeds, as_of_min: float) -> RawFeeds:
    """Chỉ giữ những dòng hệ thống ĐÃ BIẾT tại as_of (không nhìn tương lai).

    - wms: phiếu đã tạo (created ≤ as_of)
    - amr: sự kiện đã phát (ts ≤ as_of)
    - agv: job đã load (load ≤ as_of); deliver sau as_of coi như còn trống
    - excel: dòng đã được NHẬP (entered_at ≤ as_of) — giờ start/end trong dòng không quyết định
    - production, labor: kế hoạch có từ đầu ngày nên giữ nguyên
    Dòng có giờ không đọc được (NaN) vẫn được giữ để schemas.py cách ly, không bị mất âm thầm.
    """
    def known(t: pd.Series) -> pd.Series:
        return t.isna() | (t <= as_of_min)

    agv = raw.agv[known(raw.agv["load_min"])].copy()
    future = agv["deliver_min"] > as_of_min
    agv.loc[future, "deliver_epoch"] = pd.NA
    agv.loc[future, "deliver_min"] = float("nan")

    return raw.with_feeds(
        wms=raw.wms[known(raw.wms["created_min"])].copy(),
        amr=raw.amr[known(raw.amr["ts_min"])].copy(),
        agv=agv,
        excel=raw.excel[known(raw.excel["entered_min"])].copy(),
        production=raw.production.copy(),
        labor=raw.labor.copy(),
    )


__all__ = [
    "FEEDS", "RawFeeds", "load_config", "load_day", "snapshot", "parse_as_of",
    "day_start_of", "min_to_iso", "iso_to_min", "epoch_to_min", "hhmm_to_min",
]
