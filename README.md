# shibal-twin — Digital Twin Control Tower (DENSO D2)

```bash
pip install -r requirements.txt
python -m src.generator --seed 42 --days 1        # sinh 6 feed vào data/raw/2026-10-01/
python contracts/validate_contracts.py            # kiểm tra mock đúng contract v0.1
pytest -q                                         # chạy test
```

Xem `docs/feeds.md` để hiểu dữ liệu, `CLAUDE.md` để biết quy tắc repo.
