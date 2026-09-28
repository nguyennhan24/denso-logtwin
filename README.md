# shibal-twin — Digital Twin Control Tower (DENSO D2)

```bash
pip install -r requirements.txt
python -m src.generator --seed 42 --days 1        # sinh 6 feed vào data/raw/2026-10-01/
python contracts/validate_contracts.py            # kiểm tra mock đúng contract v0.1
pytest -q                                         # chạy test
```

Data Layer (Phase 4):

```bash
python -m src.data_layer --date 2026-10-01 --as-of 10:00 --as-of 14:30   # ghi data/unified/2026-10-01/{silver,gold}/
```

Xem `docs/feeds.md` để hiểu dữ liệu, `docs/data_layer.md` để dùng và tự kiểm tra Data Layer, `CLAUDE.md` để biết quy tắc repo.
