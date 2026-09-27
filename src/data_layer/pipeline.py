"""pipeline.py — Chạy cả chuỗi Data Layer cho một ngày và ghi SILVER / GOLD.

  data/raw/<date>/*.csv                      BRONZE (không sửa)
  data/unified/<date>/silver/*.parquet       feed sạch + quarantine, tại as_of muộn nhất
  data/unified/<date>/gold/
      journey.parquet                        hành trình tại as_of muộn nhất
      state_<HHMM>.json                      CurrentLogisticsState (contract v0.1)
      dq_<HHMM>.json                         báo cáo chất lượng dữ liệu
      diag_<HHMM>.json                       chẩn đoán ngoài contract

Silver/journey lấy tại as_of MUỘN NHẤT (không phải cả ngày) để file ra cũng không chứa
thông tin tương lai so với các state đã xuất.
Mọi state phải qua contracts.validate_contracts trước khi ghi; có lỗi thì dừng, không ghi gì.
"""
from __future__ import annotations

import json
from pathlib import Path

from contracts.validate_contracts import check_bundle

from .ingest import FEEDS, load_config, load_day, min_to_iso, parse_as_of, snapshot
from .mapper import build_journey
from .quality import dq_report
from .schemas import validate_feeds
from .state_builder import build_diagnostics, build_state


class ContractError(ValueError):
    """State dựng ra không qua validator của contract — không được giao cho team."""


def run_as_of(raw, as_of_min: float, cfg: dict) -> dict:
    """Chuỗi chuẩn cho một as_of: snapshot -> validate -> journey -> state/dq/diag.

    Trả dict: snap, clean, quarantine, journey, state, dq, diag (chưa ghi file).
    """
    snap = snapshot(raw, as_of_min)
    clean, quarantine = validate_feeds(snap)
    journey = build_journey(clean, as_of_min)
    return {
        "snap": snap, "clean": clean, "quarantine": quarantine, "journey": journey,
        "state": build_state(clean, journey, as_of_min, cfg),
        "dq": dq_report(snap, clean, quarantine, journey, as_of_min, cfg),
        "diag": build_diagnostics(clean, journey, as_of_min, cfg),
    }


def _hhmm(iso: str) -> str:
    return iso[11:13] + iso[14:16]


def _write_json(path: Path, obj: dict) -> None:
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def run(date: str, as_of_list: list[str], raw_dir: str | Path = "data/raw",
        out_dir: str | Path = "data/unified", config_path: str | Path | None = None) -> dict:
    """Chạy Data Layer cho ngày `date` tại các mốc as_of ("10:00" hoặc ISO).

    Trả dict tóm tắt: đường dẫn silver/gold và, cho mỗi as_of, số job + số dòng cách ly.
    """
    cfg = load_config(config_path)
    raw = load_day(Path(raw_dir) / date, cfg)
    times = sorted({parse_as_of(a, date, cfg) for a in as_of_list})
    if not times:
        raise ValueError("Cần ít nhất một --as-of")

    results = {t: run_as_of(raw, t, cfg) for t in times}

    # Kiểm tra contract cho TẤT CẢ state trước khi ghi bất cứ file nào
    problems = []
    for t, r in results.items():
        errs = check_bundle({"current_state": r["state"], "demand": []})
        problems += [f"{min_to_iso(t, raw.day_start)}: {e}" for e in errs]
    if problems:
        raise ContractError("State không hợp lệ theo contract v0.1:\n" + "\n".join(problems))

    base = Path(out_dir) / date
    silver, gold = base / "silver", base / "gold"
    silver.mkdir(parents=True, exist_ok=True)
    gold.mkdir(parents=True, exist_ok=True)

    last = results[times[-1]]
    for f in FEEDS:
        getattr(last["clean"], f).to_parquet(silver / f"{f}.parquet", index=True)
    last["clean"].master.to_parquet(silver / "master.parquet", index=False)
    last["quarantine"].to_parquet(silver / "quarantine.parquet", index=False)
    last["journey"].to_parquet(gold / "journey.parquet", index=False)

    summary = {"date": date, "silver": str(silver), "gold": str(gold), "as_of": []}
    for t, r in results.items():
        tag = _hhmm(r["state"]["as_of"])
        _write_json(gold / f"state_{tag}.json", r["state"])
        _write_json(gold / f"dq_{tag}.json", r["dq"])
        _write_json(gold / f"diag_{tag}.json", r["diag"])
        summary["as_of"].append({
            "as_of": r["state"]["as_of"],
            "queued": len(r["state"]["queued_jobs"]),
            "active": len(r["state"]["active_jobs"]),
            "quarantined_rows": int(len(r["quarantine"])),
            "estimated_jobs": r["diag"]["estimated_jobs"]["count"],
        })
    return summary
