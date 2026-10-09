"""Bước 2 — validate: kiểm hình thức + nội dung từng dòng, gắn cờ, KHÔNG xóa im lặng.

Dòng lỗi vào bảng cách ly (quarantine: feed, row_ref, check, reason); dòng sạch đi tiếp.
CỐ Ý không kiểm liên kết khóa giữa các feed ở đây: ID chưa chuẩn hóa ("PK12" vs "PICK-00012")
nên kiểm lúc này sẽ gắn cờ sai hàng loạt. Việc đó làm ở mapper.py.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from . import FEEDS
from .ingest import COLUMNS, TIME_COLUMNS

QUARANTINE_COLUMNS = ["feed", "row_ref", "check", "reason"]

# Cột bắt buộc không được trống (deliver_epoch, task_id, wms_ref được phép trống tùy dòng)
REQUIRED = {
    "wms": ["pick_no", "created_at", "material_code", "line_code", "qty_packs", "need_by"],
    "amr": ["ts", "robot_id", "event"],
    "agv": ["job_id", "agv_id", "label", "station_code", "load_epoch"],
    "production": ["line_code", "hour_start", "product", "planned_qty"],
    "labor": ["date", "worker_id", "slot", "shift", "shift_start", "shift_end", "break_start", "break_end"],
    "excel": ["worker", "pick", "start", "end", "entered_at"],
}
# Cột phải là số nguyên (cột ID như excel.pick KHÔNG nằm đây: ID lệch dạng để mapper xử lý)
INTEGER_COLUMNS = {"wms": ["qty_packs"], "production": ["planned_qty"]}

AMR_EVENTS = {"DISPATCH", "ARRIVE_GATE", "RELEASE", "RETURNED", "FAULT", "RECOVER"}
RESOURCE_EVENTS = {"FAULT", "RECOVER"}     # sự kiện của robot, không thuộc task nào


def _minutes(a: pd.Series, b: pd.Series) -> pd.Series:
    return (b - a).dt.total_seconds() / 60


def _outside(values: pd.Series, rng: dict) -> pd.Series:
    """True nếu giá trị có thật và nằm ngoài [min, max]. Giá trị trống đã được kiểm ở bước khác."""
    return values.notna() & ((values < rng["min"]) | (values > rng["max"]))


def validate_feed(name: str, df: pd.DataFrame, cfg: dict) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Trả về (dòng sạch, dòng lỗi). Một dòng có thể có nhiều lỗi -> nhiều dòng trong bảng lỗi."""
    issues = []

    def flag(mask: pd.Series, check: str, reason: str):
        for ref in df.loc[mask, "row_ref"]:
            issues.append({"feed": name, "row_ref": ref, "check": check, "reason": reason})

    # 1) Hình thức: đủ cột. Thiếu cột thì cả feed không dùng được.
    missing_cols = [c for c in COLUMNS[name] if c not in df.columns]
    if missing_cols:
        for c in missing_cols:
            flag(pd.Series(True, index=df.index), f"schema:{c}", "missing_column")
        return df.iloc[0:0], pd.DataFrame(issues, columns=QUARANTINE_COLUMNS)

    # 2) Không trống ở cột bắt buộc. Cột giờ trống/không đọc được -> missing_timestamp
    for col in REQUIRED[name]:
        reason = "missing_timestamp" if col in TIME_COLUMNS[name] else "missing_value"
        flag(df[col].isna(), f"required:{col}", reason)

    # 3) Đúng kiểu: số nguyên
    numbers = {}
    for col in INTEGER_COLUMNS.get(name, []):
        num = pd.to_numeric(df[col], errors="coerce")
        bad = df[col].notna() & (num.isna() | (num % 1 != 0))
        flag(bad, f"type:{col}", "not_a_number")
        numbers[col] = num

    # 4) Nội dung: giá trị trong khoảng hợp lý (ngưỡng ở config)
    r = cfg["valid_ranges"]
    if name == "wms":
        flag(_outside(numbers["qty_packs"], r["qty_packs"]), "range:qty_packs", "out_of_range")
        flag(_outside(_minutes(df["created_at"], df["need_by"]), r["need_by_min"]), "range:need_by", "out_of_range")
    elif name == "amr":
        ev = df["event"].str.strip().str.upper()
        flag(ev.notna() & ~ev.isin(AMR_EVENTS), "domain:event", "invalid_event")
        flag((ev == "DISPATCH") & df["wms_ref"].isna(), "required_if_dispatch:wms_ref", "missing_value")
        needs_task = ev.isin(AMR_EVENTS - RESOURCE_EVENTS)
        flag(needs_task & df["task_id"].isna(), "required_if_task_event:task_id", "missing_value")
    elif name == "agv":
        flag(_outside(_minutes(df["load_epoch"], df["deliver_epoch"]), r["agv_trip_min"]),
             "range:deliver_minus_load", "out_of_range")
    elif name == "production":
        flag(_outside(numbers["planned_qty"], r["planned_qty"]), "range:planned_qty", "out_of_range")
    elif name == "labor":
        flag(df["shift_end"] <= df["shift_start"], "range:shift_end_after_start", "out_of_range")
        inside = (df["break_start"] >= df["shift_start"]) & (df["break_end"] <= df["shift_end"]) \
            & (df["break_end"] > df["break_start"])
        flag(df["break_start"].notna() & df["break_end"].notna() & ~inside, "range:break_inside_shift", "out_of_range")
    elif name == "excel":
        flag(_outside(_minutes(df["start"], df["end"]), r["pack_min"]), "range:end_minus_start", "out_of_range")

    bad_refs = {i["row_ref"] for i in issues}
    clean = df[~df["row_ref"].isin(bad_refs)].copy()
    for col, num in numbers.items():
        clean[col] = num[clean.index].astype("int64")
    if name == "amr":
        clean["event"] = clean["event"].str.strip().str.upper()
    return clean.reset_index(drop=True), pd.DataFrame(issues, columns=QUARANTINE_COLUMNS)


def validate_all(feeds: dict[str, pd.DataFrame], cfg: dict) -> tuple[dict[str, pd.DataFrame], pd.DataFrame]:
    clean, problems = {}, []
    for name in FEEDS:
        clean[name], q = validate_feed(name, feeds[name], cfg)
        problems.append(q)
    return clean, pd.concat(problems, ignore_index=True)


def write_silver(clean: dict[str, pd.DataFrame], quarantine: pd.DataFrame, out_dir: str | Path) -> Path:
    """data/silver/<ngày>_seed<seed>/: 6 bảng sạch (parquet giữ được kiểu datetime tz) + quarantine.csv."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    for name, df in clean.items():
        df.to_parquet(out / f"{name}.parquet", index=False)
    quarantine.to_csv(out / "quarantine.csv", index=False)
    return out
