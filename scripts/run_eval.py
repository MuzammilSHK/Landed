"""Reproduce the reported evaluation numbers.

    python scripts/run_eval.py --pack packs/official --out results/

The brief requires a single command that regenerates the reported score. This is
that command — keep it working, and keep its output committed under results/.
"""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> None:
    parser = argparse.ArgumentParser(description="Landed — evaluation run")
    parser.add_argument("--pack", type=Path, default=Path("packs/official"))
    parser.add_argument("--out", type=Path, default=Path("results"))
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--mutations", type=int, default=50)
    args = parser.parse_args()

    # TODO: from landed.evaluate import run; metrics = run(args.pack, args.out, args.seed)
    # TODO: write metrics.json + a human-readable summary into args.out
    # TODO: record pack version + SHA-256 alongside the metrics so any result
    #       can be traced to the exact data that produced it
    raise NotImplementedError


if __name__ == "__main__":
    main()
