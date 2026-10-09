"""Bước 7 — contract_check: kiểm output đúng contract v0.1 bằng pydantic, ĐÚNG thì mới ghi gold.

Sai contract -> raise ContractError với danh sách lỗi rõ ràng, không tạo/ghi file nào trong gold.
Model bám contracts_v0.1_spec.md mục 2-3. Trường Data Layer thêm vào được phép (extra="allow")
ở cấp state và cấp job, NHƯNG không được có trong `request` và `resources.*`, vì Twin dựng
MaterialRequest(**request) / ResourceStatus(**resource) -> khóa lạ làm Twin lỗi.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import pandas as pd
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, StrictInt, ValidationError, model_validator

from . import FEEDS, STAGES

CONTRACT_VERSION = "0.1"
Stage = Literal["amr", "packer", "agv"]


class ContractError(Exception):
    """Output sai contract. Gold KHÔNG được ghi."""


class MaterialRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_id: str = Field(min_length=1)
    arrival_min: float = Field(ge=0)
    origin_id: str = Field(min_length=1)
    destination_line_id: str = Field(min_length=1)
    material_id: str = Field(min_length=1)
    quantity: StrictInt = Field(gt=0)


class ResourceStatus(BaseModel):
    model_config = ConfigDict(extra="forbid")
    total: StrictInt = Field(ge=0)
    busy: StrictInt = Field(ge=0)
    offline: StrictInt = Field(ge=0)

    @model_validator(mode="after")
    def busy_plus_offline_le_total(self):
        if self.busy + self.offline > self.total:
            raise ValueError(f"busy ({self.busy}) + offline ({self.offline}) > total ({self.total})")
        return self


class QueuedJob(BaseModel):
    model_config = ConfigDict(extra="allow")
    stage: Stage
    request: MaterialRequest
    # Trường thêm: phút lúc tạo đơn so với as_of (đơn mở nên luôn <= 0). arrival_min contract vẫn = 0.
    created_offset_min: float = Field(le=0)

    @model_validator(mode="after")
    def wip_arrival_zero(self):
        if self.request.arrival_min != 0:
            raise ValueError("WIP trong state phải có arrival_min = 0")
        return self


class ActiveJob(BaseModel):
    model_config = ConfigDict(extra="allow")
    stage: Stage
    remaining_min: float = Field(gt=0)
    request: MaterialRequest
    # Trường thêm của Data Layer: remaining_min luôn là ước lượng -> bắt buộc có cờ + cách tính
    is_estimated: bool
    estimate_method: str = Field(min_length=1)
    overdue: bool
    est_remaining_min: float = Field(ge=0)
    completion_unconfirmed: bool
    created_offset_min: float = Field(le=0)

    @model_validator(mode="after")
    def check(self):
        if self.request.arrival_min != 0:
            raise ValueError("WIP trong state phải có arrival_min = 0")
        if not self.is_estimated:
            raise ValueError("remaining_min là ước lượng nên is_estimated phải = true")
        if self.overdue != (self.est_remaining_min == 0):
            raise ValueError(f"overdue = {self.overdue} nhưng est_remaining_min = {self.est_remaining_min} "
                             "(định nghĩa: overdue <=> est_remaining_min = 0)")
        if self.completion_unconfirmed and not self.overdue:
            raise ValueError("completion_unconfirmed chỉ được true khi overdue")
        return self


class CurrentLogisticsState(BaseModel):
    model_config = ConfigDict(extra="allow")
    as_of: AwareDatetime
    resources: dict[Stage, ResourceStatus]
    queued_jobs: list[QueuedJob]
    active_jobs: list[ActiveJob]
    feed_updated_at: dict[str, AwareDatetime]

    @model_validator(mode="after")
    def check(self):
        if set(self.resources) != set(STAGES):
            raise ValueError(f"resources phải có đúng 3 key {STAGES}")
        if set(self.feed_updated_at) != set(FEEDS):
            raise ValueError(f"feed_updated_at phải có đúng 6 key {FEEDS}")
        late = [k for k, v in self.feed_updated_at.items() if v > self.as_of]
        if late:
            raise ValueError(f"feed_updated_at vượt quá as_of: {late}")
        for st in STAGES:
            n_active = sum(j.stage == st for j in self.active_jobs)
            if self.resources[st].busy != n_active:
                raise ValueError(f"resources.{st}.busy = {self.resources[st].busy} nhưng có {n_active} active job")
        ids = [j.request.request_id for j in self.queued_jobs + self.active_jobs]
        dup = sorted({i for i in ids if ids.count(i) > 1})
        if dup:
            raise ValueError(f"request_id bị trùng trong queued/active: {dup}")
        return self


class StateFile(BaseModel):
    """File state_HHMM.json: cùng dạng contracts/mock_v0_1.json mà Twin đang đọc (raw['current_state'])."""
    contract_version: Literal["0.1"]
    current_state: CurrentLogisticsState


def check_outputs(state_doc: dict, order_status: pd.DataFrame) -> None:
    """Raise ContractError nếu state sai contract hoặc order_status vi phạm quy tắc ước lượng."""
    errors = []
    try:
        StateFile.model_validate(state_doc)
    except ValidationError as e:
        errors += [f"state: {'.'.join(map(str, err['loc']))}: {err['msg']}" for err in e.errors()]

    # Kiểm chéo bằng validator chung của nhóm (contracts/validate_contracts.py)
    try:
        from contracts.validate_contracts import check_bundle
        errors += [f"validate_contracts: {e}" for e in check_bundle(state_doc)]
    except ImportError:
        pass
    except Exception as e:      # validator chung crash (vd. kiểu sai) cũng là sai contract
        errors.append(f"validate_contracts crash: {e!r}")

    errors += _check_resource_extras(state_doc.get("current_state", {}))

    # Quy tắc của order_status: giá trị ước lượng phải có cờ + cách tính
    est = order_status["est_remaining_min"].notna() | order_status["stage_is_estimated"]
    no_flag = order_status.loc[est & ~order_status["is_estimated"], "request_id"].tolist()
    no_method = order_status.loc[order_status["is_estimated"] & (order_status["estimate_method"].fillna("") == ""),
                                 "request_id"].tolist()
    if no_flag:
        errors.append(f"order_status: giá trị ước lượng thiếu is_estimated: {no_flag}")
    if no_method:
        errors.append(f"order_status: is_estimated nhưng estimate_method rỗng: {no_method}")

    if errors:
        raise ContractError("Output SAI contract, không ghi gold:\n  - " + "\n  - ".join(errors))


def _check_resource_extras(st: dict) -> list[str]:
    """Trường thêm resource_counts / resource_detail phải khớp nhau và khớp total, offline của contract."""
    errors = []
    res, detail = st.get("resources", {}), st.get("resource_detail", {})
    det = detail.get("amr")
    if det is not None:
        n = sum(len(det.get(k, [])) for k in ("busy", "blocked", "idle", "offline"))
        total = res.get("amr", {}).get("total")
        if n != total:
            errors.append(f"resource_detail.amr: busy + blocked + idle + offline = {n} nhưng resources.amr.total = {total}")
        jobs = [j.get("amr_id") for j in det.get("blocked_jobs", [])]
        if sorted(jobs) != sorted(det.get("blocked", [])):
            errors.append(f"resource_detail.amr.blocked_jobs {jobs} không khớp blocked {det.get('blocked')}")
    for kind, c in st.get("resource_counts", {}).items():
        parts = sum(c.get(k, 0) for k in ("busy", "blocked", "idle", "offline"))
        if parts != c.get("total"):
            errors.append(f"resource_counts.{kind}: busy + blocked + idle + offline = {parts} != total {c.get('total')}")
        for k in ("total", "offline"):
            if c.get(k) != res.get(kind, {}).get(k):
                errors.append(f"resource_counts.{kind}.{k} = {c.get(k)} khác resources.{kind}.{k}")
        for k in ("busy", "blocked", "idle", "offline"):
            ids = detail.get(kind, {}).get(k)
            if isinstance(ids, list) and len(ids) != c.get(k, 0):
                errors.append(f"resource_counts.{kind}.{k} = {c.get(k, 0)} nhưng resource_detail có {len(ids)} ID")
    return errors


def _write_json(path: Path, doc: dict) -> None:
    with open(path, "w", encoding="utf-8") as f:
        json.dump(doc, f, ensure_ascii=False, indent=2)


def write_gold(gold_dir: str | Path, hhmm: str, state_doc: dict, order_status: pd.DataFrame,
               journey: pd.DataFrame, quarantine: pd.DataFrame, quality: dict,
               latest_dir: str | Path | None = None) -> Path:
    """Kiểm trước, đúng mới ghi. Đây là cửa duy nhất để dữ liệu vào data/gold/.

    latest_dir (vd. data/gold/latest/): bản sao của LẦN CHẠY GẦN NHẤT với tên file cố định
    (state.json, order_status.parquet, data_quality.json) để Dashboard đọc một đường dẫn không đổi.
    """
    check_outputs(state_doc, order_status)
    out = Path(gold_dir)
    out.mkdir(parents=True, exist_ok=True)
    _write_json(out / f"state_{hhmm}.json", state_doc)
    order_status.to_parquet(out / f"order_status_{hhmm}.parquet", index=False)
    journey.to_parquet(out / "journey.parquet", index=False)
    quarantine.to_csv(out / "quarantine.csv", index=False)
    _write_json(out / f"data_quality_{hhmm}.json", quality)

    if latest_dir is not None:
        latest = Path(latest_dir)
        latest.mkdir(parents=True, exist_ok=True)
        _write_json(latest / "state.json", state_doc)
        order_status.to_parquet(latest / "order_status.parquet", index=False)
        _write_json(latest / "data_quality.json", quality)
    return out
