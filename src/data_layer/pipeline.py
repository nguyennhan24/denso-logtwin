"""pipeline.run(raw_dir, as_of): chạy đủ 7 bước của Data Layer.

1 ingest -> 2 validate (+silver) -> 3 mapper (journey) -> 4 state_builder -> 5 freshness
-> 6 báo cáo chất lượng -> 7 contract_check (+gold)
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from . import FEEDS, load_config
from .contract_check import CONTRACT_VERSION, check_outputs, write_gold
from .freshness import add_order_freshness, attach_source_freshness, source_freshness
from .ingest import IngestResult, ingest, to_as_of
from .mapper import build_journey, link_feeds, print_link_stats
from .state_builder import (agv_fleet_status, amr_current_request, amr_fleet_status, build_order_status,
                            build_state, packer_workers, stage_estimates)
from .validate import validate_all, write_silver


@dataclass
class PipelineResult:
    as_of: pd.Timestamp
    feeds: dict[str, pd.DataFrame]       # sau ingest
    clean: dict[str, pd.DataFrame]       # sau validate
    quarantine: pd.DataFrame             # ingest + validate + mapper
    link_stats: dict
    journey: pd.DataFrame
    order_status: pd.DataFrame
    state_doc: dict                      # {"contract_version", "current_state"}
    quality: dict
    gold_dir: Path | None = None


def build_quality_report(as_of: pd.Timestamp, ing: IngestResult, clean: dict, quarantine: pd.DataFrame,
                         link_stats: dict, order_status: pd.DataFrame, state: dict) -> dict:
    """Bước 6: số liệu chất lượng dữ liệu để Dashboard hiển thị (Data Layer không vẽ)."""
    feed_rows = {}
    for name in FEEDS:
        q = quarantine[quarantine["feed"] == name]
        feed_rows[name] = {**ing.report[name],
                           "quarantined_rows": int(q["row_ref"].nunique()),
                           "clean_rows_after_validate": len(clean[name])}
    fresh = state["source_freshness"]
    active = state["active_jobs"]
    n_open = len(order_status)
    n_est = int(order_status["is_estimated"].sum())
    return {
        "as_of": as_of.isoformat(),
        "feed_rows": feed_rows,
        "quarantine_total": len(quarantine),
        "quarantine_by_reason": {k: int(v) for k, v in quarantine["reason"].value_counts().items()},
        "quarantine_by_feed_reason": {f"{f}:{r}": int(v) for (f, r), v in
                                      quarantine.groupby(["feed", "reason"]).size().items()},
        "link_rate": link_stats,
        "estimated": {
            "estimated_fields": ["current_state.active_jobs[].remaining_min", "order_status.est_remaining_min",
                                 "order_status.stage_entered_at (khi stage_is_estimated = true)"],
            "open_orders": n_open,
            "open_orders_with_estimate": n_est,
            "open_orders_estimate_ratio": round(n_est / n_open, 4) if n_open else None,
            "stage_inferred_orders": int(order_status["stage_is_estimated"].sum()),
            "active_jobs": len(active),
            "active_jobs_estimated": sum(j["is_estimated"] for j in active),
        },
        "overdue_orders": int(order_status["overdue"].sum()),
        "late_orders": int(order_status["late"].sum()),
        "stale_sources": [n for n, f in fresh.items() if f["stale"]],
        "stale_source_count": sum(f["stale"] for f in fresh.values()),
        "stale_orders": int(order_status["stale"].sum()),
    }


def run(raw_dir: str | Path, as_of, cfg: dict | None = None, out_name: str | None = None,
        write: bool = True, verbose: bool = True) -> PipelineResult:
    cfg = cfg or load_config()
    raw_dir = Path(raw_dir)
    as_of = to_as_of(as_of, cfg["timezone"])
    out_name = out_name or raw_dir.name

    # 1) ingest: chỉ dữ liệu đã biết tại as_of
    ing = ingest(raw_dir, as_of, cfg)

    # 2) validate: gắn cờ, dòng lỗi vào quarantine
    clean, q_validate = validate_all(ing.feeds, cfg)
    if write:
        write_silver(clean, pd.concat([ing.quarantine, q_validate], ignore_index=True),
                     Path(cfg["silver_root"]) / out_name)

    # 3) mapper: nối ID, dựng journey
    linked = link_feeds(clean, ing.master, cfg)
    if verbose:
        print_link_stats(linked.stats)
    journey = build_journey(linked.linked, as_of, cfg)

    # 4) state tại as_of
    workers = packer_workers(clean["labor"], as_of)
    estimates = stage_estimates(journey, cfg)
    order_status = build_order_status(journey, as_of, estimates, cfg, len(workers["slots"]))
    state = build_state(order_status, amr_fleet_status(linked.linked["amr"], cfg),
                        agv_fleet_status(linked.linked["agv"], cfg), workers, as_of, cfg,
                        amr_requests=amr_current_request(linked.linked["amr"]))

    # 5) freshness theo nguồn (gắn vào state) và theo đơn (thêm cột vào order_status)
    state = attach_source_freshness(state, source_freshness(ing.feeds, as_of, cfg), as_of, cfg)
    order_status = add_order_freshness(order_status, journey, as_of, cfg)

    # 6) báo cáo chất lượng
    quarantine = pd.concat([ing.quarantine, q_validate, linked.quarantine], ignore_index=True)
    quality = build_quality_report(as_of, ing, clean, quarantine, linked.stats, order_status, state)

    # 7) kiểm contract rồi mới ghi gold
    state_doc = {"contract_version": CONTRACT_VERSION, "current_state": state}
    gold_dir = None
    if write:
        gold_root = Path(cfg["gold_root"])
        gold_dir = write_gold(gold_root / out_name, as_of.strftime("%H%M"), state_doc,
                              order_status, journey, quarantine, quality, latest_dir=gold_root / "latest")
    else:
        check_outputs(state_doc, order_status)

    return PipelineResult(as_of=as_of, feeds=ing.feeds, clean=clean, quarantine=quarantine,
                          link_stats=linked.stats, journey=journey, order_status=order_status,
                          state_doc=state_doc, quality=quality, gold_dir=gold_dir)
