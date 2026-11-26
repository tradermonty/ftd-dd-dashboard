#!/usr/bin/env python3
"""Fetch (or reuse cached) FMP history for both indices and replay every session.

Writes build/daily_status.csv and build/data_issues.csv. Prints a short sanity summary.

Usage:
    python scripts/run_replay.py [--use-cache] [--end YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import csv
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import fetch, replay  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--use-cache", action="store_true", help="reuse data/raw/ chunks")
    ap.add_argument("--end", default=None, help="last date (default: latest XNYS session)")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "build")
    args = ap.parse_args(argv)

    end = date.fromisoformat(args.end) if args.end else None
    t0 = time.time()
    histories, issues = {}, []
    for sym in fetch.DEFAULT_SYMBOLS:
        res = fetch.fetch_index(sym, end=end, use_cache=args.use_cache)
        histories[sym] = res.rows
        issues.extend(res.issues)
        print(
            f"{sym}: {len(res.rows)} bars {res.rows[0]['date']}..{res.rows[-1]['date']}, "
            f"{len(res.issues)} data issues"
        )
    t1 = time.time()

    records = replay.replay(histories)
    t2 = time.time()
    replay.write_daily_status(records, args.out_dir / "daily_status.csv")

    args.out_dir.mkdir(parents=True, exist_ok=True)
    with (args.out_dir / "data_issues.csv").open("w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=["symbol", "date", "issue"])
        w.writeheader()
        w.writerows(issues)

    print(f"fetch {t1 - t0:.1f}s, replay {t2 - t1:.1f}s, rows {len(records)}")
    for sym in fetch.DEFAULT_SYMBOLS:
        rows = [r for r in records if r["index"] == sym]
        ftd_dates = {r["ftd_date"] for r in rows if r["ftd_date"]}
        dd_days = sum(1 for r in rows if r["is_dd"])
        print(f"{sym}: sessions={len(rows)} distinct_ftd_date={len(ftd_dates)} dd_days={dd_days}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
