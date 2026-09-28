"""schemas.py — Kiểm tra 6 feed theo 3 tầng, tách dòng hỏng vào quarantine.

  Tầng 1 (schema, dùng pandera): đủ cột, không null ở cột bắt buộc, số dương, giờ đọc được.
  Tầng 2 (ngữ nghĩa): event hợp lệ, mã line/vật tư có trong master, deliver ≥ load...
  Tầng 3 (tham chiếu): label AGV / pick Excel trỏ về phiếu WMS có thật; task AMR có DISPATCH.

Nguyên tắc "không vứt dữ liệu lỗi": dòng hỏng KHÔNG bị xóa âm thầm; nó nằm trong bảng
quarantine (feed, row_index, reason) và được đếm vào báo cáo chất lượng.
row_index = chỉ số dòng dữ liệu trong CSV gốc (0 = dòng đầu tiên sau header).
"""
from __future__ import annotations

import pandas as pd
import pandera.pandas as pa

from .ids import master_map, pick_no_from_label, pick_no_from_num
from .ingest import FEEDS, RawFeeds

AMR_EVENTS = {"DISPATCH", "ARRIVE_GATE", "RELEASE", "RETURNED", "FAULT", "RECOVER"}
NO_TASK_EVENTS = {"FAULT", "RECOVER"}
QUARANTINE_COLS = ["feed", "row_index", "reason"]

_HHMM = r"^\d{1,2}:\d{2}$"


def _req(*checks: pa.Check) -> pa.Column:
    """Cột bắt buộc, không null. Không ép dtype vì ingest.py đã chuẩn hóa kiểu."""
    return pa.Column(nullable=False, checks=list(checks) or None)


# Tầng 1: cột *_min là giờ đã quy đổi; null nghĩa là chuỗi giờ gốc không đọc được.
SCHEMAS: dict[str, pa.DataFrameSchema] = {
    "wms": pa.DataFrameSchema({
        "pick_no": _req(pa.Check.str_matches(r"^PICK-\d+$")),
        "material_code": _req(),
        "line_code": _req(),
        "qty_packs": _req(pa.Check.gt(0)),
        "created_min": _req(),
        "need_by_min": _req(),
    }),
    "amr": pa.DataFrameSchema({
        "robot_id": _req(),
        "event": _req(),
        "ts_min": _req(),
        "task_id": pa.Column(nullable=True),
        "wms_ref": pa.Column(nullable=True),
    }),
    "agv": pa.DataFrameSchema({
        "job_id": _req(),
        "agv_id": _req(),
        "label": _req(pa.Check.str_matches(r"^PK\d+$")),
        "station_code": _req(),
        "load_min": _req(),
        "deliver_min": pa.Column(nullable=True),
    }),
    "production": pa.DataFrameSchema({
        "line_code": _req(),
        "product": _req(),
        "planned_qty": _req(pa.Check.ge(0)),
        "hour_start_min": _req(),
    }),
    "labor": pa.DataFrameSchema({
        "worker_id": _req(),
        "slot": _req(),
        "shift": _req(),
        "shift_start": _req(pa.Check.str_matches(_HHMM)),
        "shift_end": _req(pa.Check.str_matches(_HHMM)),
    }),
    "excel": pa.DataFrameSchema({
        "worker": _req(),
        "pick": _req(pa.Check.gt(0)),
        "start_min": _req(),
        "end_min": _req(),
        "entered_min": _req(),
    }),
}

# Tên cột gốc để lý do cách ly dễ hiểu với người đọc CSV.
_SOURCE_COL = {
    "created_min": "created_at", "need_by_min": "need_by", "ts_min": "ts",
    "load_min": "load_epoch", "deliver_min": "deliver_epoch", "hour_start_min": "hour_start",
    "start_min": "start", "end_min": "end", "entered_min": "entered_at",
}


def _tier1(feed: str, df: pd.DataFrame) -> list[tuple[int, str]]:
    """Chạy pandera (lazy) và đổi failure_cases thành danh sách (row_index, reason).

    Lỗi cấp cột (thiếu hẳn một cột) không cách ly từng dòng được -> báo lỗi dừng luôn,
    vì đó là lỗi hợp đồng dữ liệu với hệ thống nguồn chứ không phải một dòng bẩn.
    """
    try:
        SCHEMAS[feed].validate(df, lazy=True)
        return []
    except pa.errors.SchemaErrors as e:
        fc = e.failure_cases
    col_level = fc[fc["index"].isna()]
    if len(col_level):
        raise ValueError(f"Feed '{feed}' sai cấu trúc: {col_level[['column', 'check']].to_dict('records')}")
    out = []
    for r in fc.itertuples():
        col = _SOURCE_COL.get(r.column, r.column)
        what = "trống hoặc không đọc được" if r.check == "not_nullable" else f"vi phạm {r.check}"
        out.append((int(r.index), f"tầng 1: {col} {what}"))
    return out


def _flag(mask: pd.Series, reason: str) -> list[tuple[int, str]]:
    return [(int(i), reason) for i in mask[mask.fillna(False).astype(bool)].index]


def _tier2(feed: str, df: pd.DataFrame, master: pd.DataFrame) -> list[tuple[int, str]]:
    """Kiểm tra ngữ nghĩa trong phạm vi một feed + bảng master."""
    out: list[tuple[int, str]] = []
    if feed == "wms":
        lines, mats = master_map(master, "line", "wms"), master_map(master, "material", "wms")
        out += _flag(df["line_code"].notna() & ~df["line_code"].isin(lines),
                     "tầng 2: line_code không có trong master")
        out += _flag(df["material_code"].notna() & ~df["material_code"].isin(mats),
                     "tầng 2: material_code không có trong master")
        out += _flag(df["need_by_min"] < df["created_min"], "tầng 2: need_by trước created_at")
        out += _flag(df["pick_no"].notna() & df["pick_no"].duplicated(), "tầng 2: pick_no trùng")
    elif feed == "amr":
        ev = df["event"]
        out += _flag(ev.notna() & ~ev.isin(AMR_EVENTS), "tầng 2: event không thuộc danh sách cho phép")
        known = ev.isin(AMR_EVENTS)
        no_task = ev.isin(NO_TASK_EVENTS)
        out += _flag(known & ~no_task & df["task_id"].isna(), "tầng 2: sự kiện task thiếu task_id")
        out += _flag(no_task & df["task_id"].notna(), "tầng 2: FAULT/RECOVER không được mang task_id")
        disp = ev == "DISPATCH"
        out += _flag(disp & df["wms_ref"].isna(), "tầng 2: DISPATCH thiếu wms_ref")
        out += _flag(disp & df["task_id"].notna() & df["task_id"].where(disp).duplicated(),
                     "tầng 2: task_id có hai dòng DISPATCH")
    elif feed == "agv":
        stations = master_map(master, "line", "agv")
        out += _flag(df["station_code"].notna() & ~df["station_code"].isin(stations),
                     "tầng 2: station_code không có trong master")
        out += _flag(df["deliver_min"] < df["load_min"], "tầng 2: deliver trước load")
        out += _flag(df["label"].notna() & df["label"].duplicated(), "tầng 2: label bị load hai lần")
    elif feed == "production":
        lines = master_map(master, "line", "wms")
        out += _flag(df["line_code"].notna() & ~df["line_code"].isin(lines),
                     "tầng 2: line_code không có trong master")
    elif feed == "excel":
        out += _flag(df["end_min"] < df["start_min"], "tầng 2: end trước start")
        out += _flag(df["entered_min"] < df["end_min"], "tầng 2: nhập trước khi đóng gói xong")
        out += _flag(df["pick"].notna() & df["pick"].duplicated(), "tầng 2: pick trùng")
    return out


def _tier3(raw: RawFeeds, bad: dict[str, set[int]]) -> dict[str, list[tuple[int, str]]]:
    """Kiểm tra tham chiếu giữa các feed.

    "Phiếu WMS có thật" được xét trên TOÀN BỘ wms đã biết (kể cả dòng WMS bị cách ly vì lỗi
    khác), để một lỗi ở WMS không kéo theo cách ly dây chuyền các dòng AMR/AGV/Excel đúng.
    Riêng task AMR thì cần dòng DISPATCH hợp lệ, vì không có nó thì không ghép được task.
    """
    picks = set(raw.wms["pick_no"].dropna())
    out: dict[str, list[tuple[int, str]]] = {f: [] for f in FEEDS}

    agv_pick = pick_no_from_label(raw.agv["label"])
    out["agv"] += _flag(agv_pick.notna() & ~agv_pick.isin(picks), "tầng 3: label không tìm được phiếu WMS")

    ex_pick = pick_no_from_num(raw.excel["pick"])
    out["excel"] += _flag(ex_pick.notna() & ~ex_pick.isin(picks), "tầng 3: pick không tìm được phiếu WMS")

    amr = raw.amr
    disp = amr["event"] == "DISPATCH"
    out["amr"] += _flag(disp & amr["wms_ref"].notna() & ~amr["wms_ref"].isin(picks),
                        "tầng 3: wms_ref không tìm được phiếu WMS")
    good_disp = disp & ~amr.index.isin(list(bad["amr"]))
    tasks = set(amr.loc[good_disp, "task_id"].dropna())
    need = amr["event"].isin(AMR_EVENTS - NO_TASK_EVENTS - {"DISPATCH"}) & amr["task_id"].notna()
    out["amr"] += _flag(need & ~amr["task_id"].isin(tasks), "tầng 3: task_id không có dòng DISPATCH hợp lệ")

    # Trạm AGV phải khớp line của phiếu WMS (hai hệ thống nói cùng một chuyến)
    st = raw.agv["station_code"].map(master_map(raw.master, "line", "agv"))
    wms_line = raw.wms.drop_duplicates("pick_no").set_index("pick_no")["line_code"].map(
        master_map(raw.master, "line", "wms"))
    expect = agv_pick.map(wms_line)
    out["agv"] += _flag(st.notna() & expect.notna() & (st != expect),
                        "tầng 3: station_code khác line của phiếu WMS")
    return out


def validate_feeds(raw: RawFeeds) -> tuple[RawFeeds, pd.DataFrame]:
    """Kiểm tra 3 tầng cho 6 feed.

    Nhận: RawFeeds (thường là kết quả snapshot).
    Trả:  (clean, quarantine). clean = RawFeeds đã bỏ các dòng lỗi (giữ nguyên index gốc);
          quarantine = DataFrame[feed, row_index, reason], một dòng cho mỗi lý do.
    """
    problems: dict[str, list[tuple[int, str]]] = {}
    for feed, df in raw.feeds().items():
        problems[feed] = _tier1(feed, df) + _tier2(feed, df, raw.master)
    bad = {f: {i for i, _ in p} for f, p in problems.items()}
    for feed, p in _tier3(raw, bad).items():
        problems[feed] += p

    rows = [(f, i, r) for f in FEEDS for i, r in problems[f]]
    quarantine = (pd.DataFrame(rows, columns=QUARANTINE_COLS)
                  .drop_duplicates()
                  .sort_values(["feed", "row_index", "reason"], kind="stable")
                  .reset_index(drop=True))
    quarantine["row_index"] = quarantine["row_index"].astype("int64")

    clean = {}
    for feed, df in raw.feeds().items():
        drop = {i for i, _ in problems[feed]}
        clean[feed] = df[~df.index.isin(list(drop))].copy()
    return raw.with_feeds(**clean), quarantine
