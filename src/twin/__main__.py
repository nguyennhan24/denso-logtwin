"""Chạy từ root repo: python -m src.twin --state data/gold/latest/state.json."""
import argparse
from dataclasses import asdict
import json
from pathlib import Path

from .gold import run_gold_baseline


def main():
    parser = argparse.ArgumentParser(description="Chạy baseline Twin từ snapshot gold v0.1")
    parser.add_argument("--state", default="data/gold/latest/state.json")
    parser.add_argument("--out", default="data/results/sync2_baseline.json")
    parser.add_argument("--horizon", type=float, default=60)
    parser.add_argument("--sample-every", type=float, default=5)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    try:
        result = run_gold_baseline(args.state, horizon_min=args.horizon,
                                   sample_every_min=args.sample_every, seed=args.seed)
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"Không chạy được gold: {error}\n")

    output = Path(args.out)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(asdict(result), ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"BASELINE | initial_state_used={result.initial_state_used} | "
          f"throughput={result.metrics['throughput_count']} | "
          f"unfinished={result.metrics['unfinished_count']}")
    print(f"Result: {output}")


if __name__ == "__main__":
    main()
