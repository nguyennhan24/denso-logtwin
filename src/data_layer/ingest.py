"""Bước 1 — ingest: đọc 6 feed, đổi giờ về Asia/Ho_Chi_Minh, cắt bỏ dữ liệu "tương lai" so với as_of.

Chỉ đọc đúng 6 file feed + master.csv. KHÔNG BAO GIỜ đọc thư mục _truth/ (đáp án của Generator).
Mọi cột giữ đúng tên gốc; chỉ thêm cột `row_ref` để truy ngược về dòng trong file CSV.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from . import FEEDS

# Tên cột thật của từng feed (đọc từ src/generator/feeds.py)
COLUMNS = {
    "wms": ["pick_no", "created_at", "material_code", "line_code", "qty_packs", "need_by"],
    "amr": ["ts", "robot_id", "task_id", "event", "wms_ref"],
    "agv": ["job_id", "agv_id", "label", "station_code", "load_epoch", "deliver_epoch"],
    "production": ["line_code", "hour_start", "product", "planned_qty"],
    "labor": ["date", "worker_id", "slot", "shift", "shift_start", "shift_end", "break_start", "break_end"],
    "excel": ["worker", "pick", "start", "end", "entered_at"],
}

# Cột giờ và định dạng gốc: mỗi hệ thống ghi giờ một kiểu
TIME_COLUMNS = {
    "wms": {"created_at": "iso", "need_by": "iso"},
    "amr": {"ts": "iso"},
    "agv": {"load_epoch": "epoch", "deliver_epoch": "epoch"},
    "production": {"hour_start": "iso"},
    "labor": {"shift_start": "hhmm", "shift_end": "hhmm", "break_start": "hhmm", "break_end": "hhmm"},
    "excel": {"start": "hhmm", "end": "hhmm", "entered_at": "iso"},
}

# Phân loại cột giờ:
#  - SỰ KIỆN THỰC TẾ (liệt kê ở đây): tại as_of chỉ được biết nếu <= as_of. Giá trị > as_of -> NaT.
#  - KẾ HOẠCH / HẠN (các cột giờ còn lại): công bố trước, được phép nằm sau as_of -> giữ nguyên.
EVENT_TIME_COLUMNS = {
    "wms": ["created_at"],                        # need_by là hạn giao (kế hoạch)
    "amr": ["ts"],
    "agv": ["load_epoch", "deliver_epoch"],       # 1 dòng chứa 2 sự kiện LOAD và giao
    "production": [],                             # toàn bộ là kế hoạch theo giờ
    "labor": [],                                  # toàn bộ là phân ca/nghỉ KẾ HOẠCH; nghỉ thực tế không có trong feed
    "excel": ["start", "end", "entered_at"],
}

# Cột quyết định "dòng này đã biết được chưa" tại as_of.
# excel có thời điểm ghi nhận riêng (entered_at); các feed khác chỉ có thời điểm xảy ra sự kiện.
CUT_COLUMN = {"wms": "created_at", "amr": "ts", "agv": "load_epoch", "excel": "entered_at"}

# production/labor là kế hoạch, giả định công bố lúc đầu ngày -> dùng mốc sớm nhất làm giờ công bố
PUBLISH_COLUMN = {"production": "hour_start", "labor": "shift_start"}

_HHMM = re.compile(r"^(\d{1,2}):(\d{2})(?::(\d{2}))?$")


@dataclass
class IngestResult:
    feeds: dict[str, pd.DataFrame]                 # 6 feed đã cắt theo as_of
    master: pd.DataFrame                           # bảng mã tham chiếu
    report: dict = field(default_factory=dict)     # số dòng từng feed
    quarantine: pd.DataFrame = None                # dòng trùng đã bỏ (vẫn ghi lại, không bỏ lặng lẽ)


# ---------- đổi giờ ----------
def parse_iso(s: pd.Series, tz: str) -> pd.Series:
    """Chuỗi ISO 8601 -> datetime tz. Không có múi giờ thì coi là giờ địa phương. Lỗi -> NaT."""
    def one(x):
        if pd.isna(x) or not str(x).strip():
            return pd.NaT
        try:
            t = pd.Timestamp(str(x).strip())
        except (ValueError, TypeError):
            return pd.NaT
        return t.tz_localize(tz) if t.tzinfo is None else t.tz_convert(tz)
    return pd.Series([one(x) for x in s], index=s.index, dtype=f"datetime64[ns, {tz}]")


def parse_epoch(s: pd.Series, tz: str) -> pd.Series:
    """Epoch giây (UTC) -> datetime tz."""
    num = pd.to_numeric(s, errors="coerce")
    return pd.to_datetime(num, unit="s", utc=True).dt.tz_convert(tz).astype(f"datetime64[ns, {tz}]")


def parse_hhmm(s: pd.Series, day: pd.Timestamp, tz: str) -> pd.Series:
    """'14:05' -> datetime của ngày `day`. Feed chỉ có giờ nên phải ghép với ngày đang xử lý."""
    base = pd.Timestamp(day.date())
    def one(x):
        m = _HHMM.match(str(x).strip()) if pd.notna(x) else None
        if not m:
            return pd.NaT
        h, mi, sec = int(m.group(1)), int(m.group(2)), int(m.group(3) or 0)
        if h > 23 or mi > 59 or sec > 59:
            return pd.NaT
        return (base + pd.Timedelta(hours=h, minutes=mi, seconds=sec)).tz_localize(tz)
    return pd.Series([one(x) for x in s], index=s.index, dtype=f"datetime64[ns, {tz}]")


def to_as_of(as_of, tz: str) -> pd.Timestamp:
    t = pd.Timestamp(as_of)
    return t.tz_localize(tz) if t.tzinfo is None else t.tz_convert(tz)


# ---------- đọc ----------
def read_feed(raw_dir: Path, name: str) -> pd.DataFrame:
    """Đọc 1 feed, mọi cột là chuỗi (kiểu dữ liệu để validate kiểm, không để pandas tự đoán)."""
    path = Path(raw_dir) / f"{name}.csv"
    if not path.exists():
        raise FileNotFoundError(f"Thiếu feed {path}")
    df = pd.read_csv(path, dtype=str)
    # +2: số dòng trong file (dòng 1 là header) -> người xem mở CSV là thấy ngay
    df.insert(0, "row_ref", [f"{name}.csv:{i + 2}" for i in range(len(df))])
    return df


def read_master(raw_dir: Path) -> pd.DataFrame:
    return pd.read_csv(Path(raw_dir) / "master.csv", dtype=str)


def ingest(raw_dir: str | Path, as_of, cfg: dict) -> IngestResult:
    """Đọc 6 feed và chỉ giữ những gì hệ thống "đã biết" tại as_of."""
    tz = cfg["timezone"]
    as_of = to_as_of(as_of, tz)
    feeds, report, dup_rows = {}, {}, []

    for name in FEEDS:
        raw = read_feed(raw_dir, name)
        df = raw.copy()
        # Dòng trùng hoàn toàn = mọi cột gốc giống nhau (không tính row_ref)
        orig_cols = [c for c in raw.columns if c != "row_ref"]
        is_dup = raw.duplicated(subset=orig_cols, keep="first")

        for col, fmt in TIME_COLUMNS[name].items():
            if col not in df:
                continue      # thiếu cột -> validate sẽ báo
            if fmt == "iso":
                df[col] = parse_iso(df[col], tz)
            elif fmt == "epoch":
                df[col] = parse_epoch(df[col], tz)
            else:
                df[col] = parse_hhmm(df[col], as_of, tz)

        keep = pd.Series(True, index=df.index)
        cut = CUT_COLUMN.get(name)
        if cut in df:
            # Giờ trống vẫn giữ lại để validate cách ly (không biết nên không bỏ lặng lẽ)
            keep = df[cut].isna() | (df[cut] <= as_of)
        pub = PUBLISH_COLUMN.get(name)
        if pub in df and df[pub].notna().any() and df[pub].min() > as_of:
            keep[:] = False   # kế hoạch chưa được công bố
        df = df[keep].copy()

        # Dòng được giữ nhưng có thể chứa sự kiện sau as_of (vd. AGV đã LOAD, chưa giao):
        # mọi ô sự kiện > as_of đặt thành NaT vì lúc đó chưa ai biết
        masked = 0
        for col in EVENT_TIME_COLUMNS[name]:
            if col in df:
                future = df[col] > as_of
                masked += int(future.sum())
                df.loc[future, col] = pd.NaT

        dup_here = is_dup[df.index]
        dup_rows += [{"feed": name, "row_ref": r, "check": "ingest:exact_duplicate", "reason": "duplicate_row"}
                     for r in df.loc[dup_here, "row_ref"]]
        df = df[~dup_here]

        sort_col = cut or pub
        if sort_col in df:
            df = df.sort_values(sort_col, kind="stable", na_position="last")
        feeds[name] = df.reset_index(drop=True)
        report[name] = {
            "raw_rows": len(raw),
            "future_rows_cut": int((~keep).sum()),
            "future_cells_masked": masked,
            "duplicates_dropped": int(dup_here.sum()),
            "rows_at_as_of": len(feeds[name]),
        }

    quarantine = pd.DataFrame(dup_rows, columns=["feed", "row_ref", "check", "reason"])
    return IngestResult(feeds=feeds, master=read_master(raw_dir), report=report, quarantine=quarantine)
