#!/usr/bin/env python3
"""Write the data file for the top-page mockup (local only, never published).

Reads build/current (daily_status.csv, ftd_events.csv, distribution_days.csv) and the
cached FMP bars in data/raw/, and writes <out>/mock_data.js with the last SESSIONS sessions
per index plus the most recent FTD episode of each index.

Usage: python scripts/mock_data.py build/mockups/top-v1
"""

from __future__ import annotations

import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SESSIONS = 130
INDICES = {"^GSPC": "GSPC", "^IXIC": "IXIC"}


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def bars(sym: str) -> dict[str, dict]:
    out: dict[str, dict] = {}
    for p in sorted((ROOT / "data" / "raw").glob(f"{INDICES[sym]}_*.json")):
        for b in json.loads(p.read_text()):
            out[b["date"]] = b
    return out


def main(out_dir: str) -> int:
    cur = ROOT / "build" / "current" / "data"
    status = read_csv(cur / "daily_status.csv")
    events = read_csv(cur / "ftd_events.csv")
    dds = read_csv(cur / "distribution_days.csv")
    data: dict = {"as_of": None, "indices": {}}
    for sym in INDICES:
        rows = [r for r in status if r["index"] == sym][-SESSIONS:]
        raw = bars(sym)
        series = []
        for r in rows:
            b = raw[r["date"]]
            series.append({
                "d": r["date"], "o": b["open"], "h": b["high"], "l": b["low"], "c": b["close"],
                "v": b["volume"], "pct": round(float(r["pct_change"]) * 100, 2),
                "vchg": round(float(r["volume_change"]) * 100, 1),
                "dd": r["is_dd"] == "True", "d5": int(r["d5"]), "d15": int(r["d15"]),
                "d25": int(r["d25"]), "risk": r["risk_level"], "state": r["ftd_state"],
                "ftd": r["ftd_date"], "day1": r["rally_day1_date"], "low": r["swing_low_date"],
            })
        ev = [e for e in events if e["index"] == sym][-1]
        dd_rows = [d for d in dds if d["index"] == sym and d["date"] >= rows[0]["date"]]
        data["indices"][sym] = {
            "series": series,
            "last_ftd": {k: ev[k] for k in (
                "rally_day1_date", "swing_low_date", "ftd_date", "ftd_day_number", "gain_pct",
                "volume_vs_prev_pct", "invalidation_date")},
            "dd_days": [{k: d[k] for k in ("date", "expires_on", "invalidated_on")}
                        for d in dd_rows],
        }
        data["as_of"] = rows[-1]["date"]
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    js = "window.MOCK=" + json.dumps(data, separators=(",", ":")) + ";\n"
    (out / "mock_data.js").write_text(js, encoding="utf-8")
    print(f"wrote {out / 'mock_data.js'} ({len(js) // 1024} KB), as of {data['as_of']}")
    hist = history(status, events, dds)
    js = "window.HIST=" + json.dumps(hist, separators=(",", ":")) + ";\n"
    (out / "history_data.js").write_text(js, encoding="utf-8")
    print(f"wrote {out / 'history_data.js'} ({len(js) // 1024} KB)")
    return 0


STATES = ["NO_SIGNAL", "CORRECTION", "RALLY_FAILED", "FTD_INVALIDATED", "RALLY_ATTEMPT",
          "FTD_WINDOW", "FTD_CONFIRMED"]
RISKS = ["NORMAL", "CAUTION", "HIGH", "SEVERE"]


def history(status: list[dict], events: list[dict], dds: list[dict]) -> dict:
    """Every session since 2000 as columns, plus the FTD events and DD invalidations."""
    out: dict = {"states": STATES, "risks": RISKS, "indices": {}}
    for sym in INDICES:
        rows = [r for r in status if r["index"] == sym]
        out["indices"][sym] = {
            "d": [r["date"] for r in rows],
            "c": [round(float(r["close"]), 2) for r in rows],
            "dd": "".join("1" if r["is_dd"] == "True" else "0" for r in rows),
            "d25": [int(r["d25"]) for r in rows],
            "d5": [int(r["d5"]) for r in rows],
            "d15": [int(r["d15"]) for r in rows],
            "risk": "".join(str(RISKS.index(r["risk_level"])) for r in rows),
            "st": "".join(str(STATES.index(r["ftd_state"])) for r in rows),
            "day1": [r["rally_day1_date"] for r in rows],
            "ftd": [[e["rally_day1_date"], e["swing_low_date"], e["ftd_date"],
                     e["invalidation_date"], e["gain_pct"]]
                    for e in events if e["index"] == sym],
            "ddinv": {d["date"]: d["invalidated_on"] for d in dds
                      if d["index"] == sym and d["invalidated_on"]},
        }
    return out


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "build/mockups/top-v1"))
