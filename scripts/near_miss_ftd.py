#!/usr/bin/env python3
"""List days that narrowly missed being a Follow-Through Day (local analysis only).

A day counts when the skill's state that day is RALLY_ATTEMPT or FTD_WINDOW, it is day 4-10
of the rally (counted from rally_day1_date in daily_status.csv), and either
  A. the gain is >= 1.00% and < 1.25% with volume above the previous day, or
  B. the gain is >= 1.25% but volume is not above the previous day.
For each near miss it also records whether the same rally later produced an FTD (from
ftd_events.csv) and the index return 20 and 60 sessions later.

Usage: python scripts/near_miss_ftd.py [build/current] [tasks/output/near_miss_ftd.csv]
"""

from __future__ import annotations

import csv
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import median

# Gains are compared in percent rounded to 2 decimals, as the vendored ftd-detector does
# (rally_tracker: change_pct = round(..., 2); FTD when change_pct >= FTD_GAIN_MINIMUM).
GAIN_MIN, FTD_GAIN = 1.00, 1.25
FIELDS = ["index", "date", "rally_day1_date", "rally_day", "kind", "gain_pct",
          "volume_change_pct", "same_rally_ftd_date", "ret_20d_pct", "ret_60d_pct"]
DAY_START, DAY_END = 4, 10


def read(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def near_misses(status: list[dict], events: list[dict]) -> list[dict]:
    ftd_by_rally = {(e["index"], e["rally_day1_date"]): e["ftd_date"] for e in events}
    out = []
    by_index = defaultdict(list)
    for r in status:
        by_index[r["index"]].append(r)
    for sym, rows in by_index.items():
        pos = {r["date"]: i for i, r in enumerate(rows)}
        for i, r in enumerate(rows):
            if r["ftd_state"] not in ("RALLY_ATTEMPT", "FTD_WINDOW") or not r["rally_day1_date"]:
                continue
            if r["rally_day1_date"] not in pos or not r["pct_change"] or not r["volume_change"]:
                continue
            day = i - pos[r["rally_day1_date"]] + 1
            if not DAY_START <= day <= DAY_END:
                continue
            gain, vol = round(float(r["pct_change"]) * 100, 2), float(r["volume_change"])
            if GAIN_MIN <= gain < FTD_GAIN and vol > 0:
                kind = "A: gain 1.00-1.25%, volume up"
            elif gain >= FTD_GAIN and vol <= 0:
                kind = "B: gain >= 1.25%, volume not up"
            else:
                continue
            close = float(r["close"])
            fwd = {}
            for n in (20, 60):
                fwd[n] = (float(rows[i + n]["close"]) / close - 1) if i + n < len(rows) else None
            later = ftd_by_rally.get((sym, r["rally_day1_date"]))
            out.append({
                "index": sym, "date": r["date"], "rally_day1_date": r["rally_day1_date"],
                "rally_day": day, "kind": kind[0], "gain_pct": gain,
                "volume_change_pct": round(vol * 100, 1),
                "same_rally_ftd_date": later or "",
                "ret_20d_pct": None if fwd[20] is None else round(fwd[20] * 100, 2),
                "ret_60d_pct": None if fwd[60] is None else round(fwd[60] * 100, 2),
            })
    return sorted(out, key=lambda x: (x["date"], x["index"]))


def main(argv: list[str]) -> int:
    run = Path(argv[1]) if len(argv) > 1 else Path("build/current")
    dest = Path(argv[2]) if len(argv) > 2 else Path("tasks/output/near_miss_ftd.csv")
    rows = near_misses(read(run / "data/daily_status.csv"), read(run / "data/ftd_events.csv"))
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=FIELDS)  # header even when there is no near miss
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {dest}: {len(rows)} near misses")
    for sym in ("^GSPC", "^IXIC"):
        r = [x for x in rows if x["index"] == sym]
        kinds = Counter(x["kind"] for x in r)
        no_ftd = [x for x in r if not x["same_rally_ftd_date"]]
        print(f"{sym}: A {kinds['A']}, B {kinds['B']}; rally never got an FTD: {len(no_ftd)}")
        # the rallies with a near miss but no FTD: one line per rally (first near miss)
        seen = set()
        uniq = []
        for x in no_ftd:
            if x["rally_day1_date"] in seen:
                continue
            seen.add(x["rally_day1_date"])
            uniq.append(x)
        r20 = [x["ret_20d_pct"] for x in uniq if x["ret_20d_pct"] is not None]
        r60 = [x["ret_60d_pct"] for x in uniq if x["ret_60d_pct"] is not None]
        if uniq:
            m20 = f"{median(r20):+.2f}%" if r20 else "n/a"
            m60 = f"{median(r60):+.2f}%" if r60 else "n/a"
            print(f"   rallies without FTD but with a near miss: {len(uniq)}; median 20d "
                  f"{m20}, 60d {m60}; 60d > +5%: {sum(1 for v in r60 if v > 5)} of {len(r60)}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
