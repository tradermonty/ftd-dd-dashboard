#!/usr/bin/env python3
"""Replay the full history and write the P3 event / outcome tables.

Writes (default build/): ftd_events.csv, rally_attempts.csv, distribution_days.csv,
ftd_outcomes.csv, dd_bucket_stats.csv, summary.json (and daily_status.csv).

Usage:
    python scripts/build_events.py [--use-cache] [--end YYYY-MM-DD] [--out-dir DIR]
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import events, fetch, outcomes, replay  # noqa: E402


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})


def build_summary(histories, rows, ftd_ev, rallies, dd, out_rows, vendor_ref) -> dict:
    per_index = {}
    stats = outcomes.ftd_outcome_stats(out_rows)
    for sym in histories:
        ev = [e for e in ftd_ev if e["index"] == sym]
        per_index[sym] = {
            "ftd_events": len(ev),
            "ftd_with_lag": sum(1 for e in ev if e["lag_sessions"] > 0),
            "max_lag_sessions": max((e["lag_sessions"] for e in ev), default=None),
            "invalidated_at_detection": sum(1 for e in ev if e["invalidated_at_detection"]),
            "invalidated_within_60": sum(1 for e in ev if e["invalidated_within_60"] is True),
            "invalidated_within_60_undetermined": sum(
                1 for e in ev if e["invalidated_within_60"] is None
            ),
            "rally_attempts": sum(1 for r in rallies if r["index"] == sym),
            "distribution_days": sum(1 for d in dd if d["index"] == sym),
            "outcome_stats": stats.get(sym, {}),
        }
    return {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "vendor_ref": vendor_ref,
        "data_range": {
            "fetched_first": next(iter(histories.values()))[0]["date"],
            "replay_first": rows[0]["date"],
            "last": rows[-1]["date"],
        },
        "counts": {
            "ftd_events": len(ftd_ev),
            "rally_attempts": len(rallies),
            "distribution_days": len(dd),
            "replay_rows": len(rows),
        },
        "per_index": per_index,
    }


def build_all(histories: dict, od: Path, issues: list[dict] | None = None) -> dict:
    """Replay + events + outcomes for fetched histories; write all CSVs and summary.json."""
    rows = replay.replay(histories, keep_raw=True)
    sessions = events.sessions_from(histories)

    ftd_ev = events.ftd_events(rows, histories, sessions)
    rallies = events.rally_attempts(rows, sessions)
    dd = events.distribution_days(rows, sessions)
    out_rows = outcomes.ftd_outcomes(ftd_ev, histories)
    days = outcomes.dd_day_forward(rows, histories)
    bucket_stats = outcomes.dd_bucket_stats(days)

    replay.write_daily_status(rows, od / "daily_status.csv")
    write_csv(od / "ftd_events.csv", events.FTD_EVENT_COLUMNS, ftd_ev)
    write_csv(od / "rally_attempts.csv", events.RALLY_COLUMNS, rallies)
    write_csv(od / "distribution_days.csv", events.DD_COLUMNS, dd)
    write_csv(od / "ftd_outcomes.csv", outcomes.FTD_OUTCOME_COLUMNS, out_rows)
    write_csv(od / "dd_bucket_stats.csv", outcomes.DD_STAT_COLUMNS, bucket_stats)
    write_csv(
        od / "risk_level_stats.csv", outcomes.RISK_STAT_COLUMNS, outcomes.risk_level_stats(days)
    )
    if issues is not None:
        write_csv(od / "data_issues.csv", ["symbol", "date", "issue"], issues)
    vendor_ref = json.loads((ROOT / "vendor" / "VENDOR.lock").read_text())["ref"]
    summary = build_summary(histories, rows, ftd_ev, rallies, dd, out_rows, vendor_ref)
    risk_counts = outcomes.risk_level_counts(days)
    for sym, counts in risk_counts.items():
        summary["per_index"][sym]["risk_level_days"] = counts
    (od / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False) + "\n")
    return summary


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--use-cache", action="store_true", help="reuse data/raw/ chunks")
    ap.add_argument("--end", default=None)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "build")
    args = ap.parse_args(argv)

    end = date.fromisoformat(args.end) if args.end else None
    t0 = time.time()
    histories = {}
    for sym in fetch.DEFAULT_SYMBOLS:
        histories[sym] = fetch.fetch_index(sym, end=end, use_cache=args.use_cache).rows
    summary = build_all(histories, args.out_dir)
    print(f"done in {time.time() - t0:.1f}s -> {args.out_dir}")
    print(json.dumps(summary["counts"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
