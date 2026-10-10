"""planner.py — Đọc production plan (Phase 6, Trung).

Hai nguồn, ghép theo một file plan YAML trong config/plans/:
  base: feed  -> production.csv của ngày trong data/raw (kế hoạch đã công bố, qua tiện ích của Data Layer)
  base: none  -> không lấy feed, chỉ dùng `rows`
  adjust      -> [{from: "14:00", to: "16:00", factor: 3.0}]: nhân planned_qty các giờ trong [from, to)
  rows        -> dòng tuyệt đối (hour_start, line, product, planned_qty), GHI ĐÈ base sau khi adjust

Thứ tự áp dụng: base -> adjust -> rows. Plan theo GIỜ (mỗi dòng phủ [hour_start, hour_start + 60 phút)),
nên from/to/hour_start phải là giờ tròn.

Chỉ đọc production.csv + master.csv. KHÔNG BAO GIỜ đọc _truth/ hay feed sự kiện (wms/amr/agv/excel):
demand tương lai chỉ phụ thuộc plan + BOM + as_of + seed.

Đầu ra: DataFrame PLAN_COLUMNS, hour_start là datetime có múi giờ Asia/Ho_Chi_Minh.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Literal

import pandas as pd
import yaml
from pydantic import BaseModel, ConfigDict, Field, NonNegativeInt, ValidationError, field_validator, model_validator

from src.data_layer.ingest import parse_iso, read_feed, read_master, to_as_of
from src.data_layer.mapper import code_maps

PLAN_COLUMNS = ["hour_start", "line_id", "product", "planned_qty", "plan_id"]
HOUR_MIN = 60
_HH00 = re.compile(r"^(\d{1,2}):(\d{2})$")


class PlanError(ValueError):
    """Plan sai (file YAML, feed production hoặc thời điểm công bố). Thông báo ghi rõ chỗ sai."""


def _full_hour(v: str, *, allow_24: bool = False) -> str:
    m = _HH00.match(str(v).strip())
    if not m:
        raise ValueError(f"'{v}' phải có dạng HH:MM")
    h, mi = int(m.group(1)), int(m.group(2))
    if mi != 0:
        raise ValueError(f"'{v}' phải là giờ tròn (plan theo giờ)")
    if not 0 <= h <= (24 if allow_24 else 23):
        raise ValueError(f"'{v}' ngoài khoảng giờ hợp lệ")
    return f"{h:02d}:00"


class Adjust(BaseModel):
    """Nhân planned_qty các giờ có hour_start trong [from, to)."""
    model_config = ConfigDict(extra="forbid", populate_by_name=True)
    from_: str = Field(alias="from")
    to: str
    factor: float = Field(ge=0)

    @field_validator("from_")
    @classmethod
    def _from(cls, v):
        return _full_hour(v)

    @field_validator("to")
    @classmethod
    def _to(cls, v):
        return _full_hour(v, allow_24=True)

    @model_validator(mode="after")
    def _order(self):
        if self.from_ >= self.to:
            raise ValueError(f"from ({self.from_}) phải trước to ({self.to})")
        return self


class PlanRow(BaseModel):
    """Một dòng plan tuyệt đối. line/product dùng ID chuẩn (L1, P100)."""
    model_config = ConfigDict(extra="forbid")
    hour_start: str
    line: str = Field(min_length=1)
    product: str = Field(min_length=1)
    planned_qty: NonNegativeInt

    @field_validator("hour_start")
    @classmethod
    def _hour(cls, v):
        return _full_hour(v)


class PlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")
    plan_id: str = Field(pattern=r"^[A-Za-z0-9_\-]+$")
    description: str = ""
    base: Literal["feed", "none"] = "feed"
    adjust: list[Adjust] = []
    rows: list[PlanRow] = []

    @model_validator(mode="after")
    def _check(self):
        if self.base == "none" and not self.rows:
            raise ValueError("base: none thì phải có rows")
        keys = [(r.hour_start, r.line, r.product) for r in self.rows]
        dup = sorted({k for k in keys if keys.count(k) > 1})
        if dup:
            raise ValueError(f"rows bị trùng (hour_start, line, product): {dup}")
        return self


def _fmt_errors(e: ValidationError) -> str:
    return "; ".join(f"{'.'.join(map(str, x['loc'])) or '(gốc)'}: {x['msg']}" for x in e.errors())


def resolve_plan_path(plan: str | Path, plans_dir: str | Path = "config/plans") -> Path:
    """'spike_14_16_x3' -> config/plans/spike_14_16_x3.yaml; đường dẫn có sẵn thì giữ nguyên."""
    p = Path(plan)
    if p.suffix in (".yaml", ".yml") or p.exists():
        return p
    return Path(plans_dir) / f"{plan}.yaml"


def load_plan_spec(plan: str | Path, plans_dir: str | Path = "config/plans") -> PlanSpec:
    path = resolve_plan_path(plan, plans_dir)
    if not path.exists():
        names = sorted(x.stem for x in Path(plans_dir).glob("*.yaml"))
        raise PlanError(f"Không thấy plan {path}. Plan có sẵn: {names}")
    try:
        with open(path, encoding="utf-8") as f:
            doc = yaml.safe_load(f)
    except yaml.YAMLError as e:
        raise PlanError(f"{path}: YAML lỗi: {e}") from e
    if not isinstance(doc, dict):
        raise PlanError(f"{path}: plan phải là một YAML mapping")
    try:
        return PlanSpec.model_validate(doc)
    except ValidationError as e:
        raise PlanError(f"{path}: {_fmt_errors(e)}") from e


class _FeedRow(BaseModel):
    """Kiểm từng dòng production.csv sau khi đổi giờ và map mã line."""
    hour_start: pd.Timestamp
    line_id: str = Field(min_length=1)
    product: str = Field(min_length=1)
    planned_qty: NonNegativeInt
    model_config = ConfigDict(arbitrary_types_allowed=True)


def read_feed_plan(raw_dir: str | Path, tz: str = "Asia/Ho_Chi_Minh") -> pd.DataFrame:
    """production.csv -> plan chuẩn (line ID chuẩn qua master.csv, giờ có múi giờ).

    Dùng read_feed/parse_iso/code_maps của Data Layer. Dòng sai -> PlanError liệt kê đủ các dòng.
    """
    raw = read_feed(raw_dir, "production")
    lines = code_maps(read_master(raw_dir)).get(("line", "wms"), {})
    hour = parse_iso(raw["hour_start"], tz)
    errors, rows = [], []
    for i, r in enumerate(raw.itertuples(index=False)):
        h = hour.iloc[i]
        line = lines.get(str(r.line_code).strip())
        problems = []
        if pd.isna(h):
            problems.append(f"hour_start '{r.hour_start}' không đọc được")
        elif h.minute or h.second:
            problems.append(f"hour_start {h.isoformat()} không phải giờ tròn")
        if line is None:
            problems.append(f"line_code '{r.line_code}' không có trong master.csv")
        if not problems:
            try:
                rows.append(_FeedRow(hour_start=h, line_id=line, product=str(r.product).strip(),
                                     planned_qty=r.planned_qty).model_dump())
            except ValidationError as e:
                problems.append(_fmt_errors(e))
        if problems:
            errors.append(f"{r.row_ref}: {'; '.join(problems)}")
    if errors:
        raise PlanError("production.csv sai:\n  " + "\n  ".join(errors))
    df = pd.DataFrame(rows, columns=["hour_start", "line_id", "product", "planned_qty"])
    dup = df.duplicated(["hour_start", "line_id", "product"], keep=False)
    if dup.any():
        raise PlanError(f"production.csv có dòng trùng (hour_start, line, product): {df[dup].to_dict('records')}")
    df["hour_start"] = df["hour_start"].astype(f"datetime64[ns, {tz}]")
    return df


def apply_plan(spec: PlanSpec, base: pd.DataFrame | None, day, tz: str = "Asia/Ho_Chi_Minh") -> pd.DataFrame:
    """base -> adjust -> rows. `day` là ngày (date) của as_of; from/to/hour_start tính theo ngày này."""
    day0 = pd.Timestamp(day).normalize().tz_localize(tz) if pd.Timestamp(day).tzinfo is None \
        else pd.Timestamp(day).tz_convert(tz).normalize()

    def at(hhmm: str) -> pd.Timestamp:
        return day0 + pd.Timedelta(hours=int(hhmm[:2]))

    if spec.base == "feed":
        if base is None:
            raise PlanError(f"Plan '{spec.plan_id}' cần feed production (base: feed) nhưng không có raw_dir")
        plan = base[["hour_start", "line_id", "product", "planned_qty"]].copy()
        other_day = plan["hour_start"].dt.normalize() != day0
        if other_day.any():
            raise PlanError(f"production.csv có giờ không thuộc ngày {day0.date()}: "
                            f"{sorted(plan.loc[other_day, 'hour_start'].astype(str).unique())[:3]}")
    else:
        plan = pd.DataFrame(columns=["hour_start", "line_id", "product", "planned_qty"])
    plan["planned_qty"] = plan["planned_qty"].astype(float)

    for a in spec.adjust:
        hit = (plan["hour_start"] >= at(a.from_)) & (plan["hour_start"] < at(a.to))
        plan.loc[hit, "planned_qty"] *= a.factor

    if spec.rows:
        new = pd.DataFrame([{"hour_start": at(r.hour_start), "line_id": r.line, "product": r.product,
                             "planned_qty": float(r.planned_qty)} for r in spec.rows])
        key = ["hour_start", "line_id", "product"]
        keep = ~plan.set_index(key).index.isin(new.set_index(key).index)
        # Bỏ frame rỗng trước khi concat (base: none hoặc rows ghi đè hết): pandas 2.x báo FutureWarning
        # vì kiểu cột của frame rỗng sẽ không còn quyết định kiểu kết quả.
        parts = [f for f in (plan[keep], new) if not f.empty]
        plan = pd.concat(parts, ignore_index=True)

    plan["hour_start"] = pd.to_datetime(plan["hour_start"]).dt.tz_convert(tz)
    plan["plan_id"] = spec.plan_id
    return plan.sort_values(["hour_start", "line_id", "product"], kind="stable").reset_index(drop=True)[PLAN_COLUMNS]


def load_plan(plan: str | Path, as_of, *, raw_dir: str | Path | None = None,
              plans_dir: str | Path = "config/plans", tz: str = "Asia/Ho_Chi_Minh") -> pd.DataFrame:
    """Đọc plan cho ngày của as_of.

    base: feed -> plan coi là công bố lúc giờ sớm nhất trong production.csv (cùng giả định với Data Layer,
    docs/data_dictionary.md mục 4). as_of trước mốc đó -> PlanError (chưa được biết plan).
    """
    as_of = to_as_of(as_of, tz)
    spec = load_plan_spec(plan, plans_dir)
    base = None
    if spec.base == "feed":
        if raw_dir is None:
            raise PlanError(f"Plan '{spec.plan_id}' cần feed production (base: feed) nhưng không có raw_dir")
        base = read_feed_plan(raw_dir, tz)
        published = base["hour_start"].min()
        if pd.notna(published) and as_of < published:
            raise PlanError(f"as_of {as_of.isoformat()} trước lúc plan được công bố ({published.isoformat()})")
    return apply_plan(spec, base, as_of.date(), tz)
