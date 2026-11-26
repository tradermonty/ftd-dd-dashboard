"""Day-by-day replay of the FTD and Distribution Day rules (spec §3.2).

For each XNYS session t, only bars <= t are used:
  FTD: last 80 bars ending at t (newest first) -> rally_tracker.get_market_state(sp500, nasdaq)
       then post_ftd_monitor.assess_post_ftd_health(state, sp500_chrono_80, nasdaq_chrono_80)
       (same order as skills/ftd-detector/scripts/ftd_detector.py).
  DD:  last 85 bars ending at t (newest first) per index -> dd_glue.analyze.
"""

from __future__ import annotations

import csv
from pathlib import Path
from types import ModuleType

from engine import dd_glue, loader

FTD_WINDOW = 80
DD_WINDOW = 85
SP500 = "^GSPC"
NASDAQ = "^IXIC"
STATE_KEY = {SP500: "sp500", NASDAQ: "nasdaq"}
DEFAULT_START = "2000-01-03"

COLUMNS = [
    "date",
    "index",
    "close",
    "pct_change",
    "volume_change",
    "is_dd",
    "d5",
    "d15",
    "d25",
    "ftd_state",
    "ftd_date",
    "rally_day1_date",
    "swing_low_date",
    "combined_state",
    "risk_level",
    "market_below_ma",
]


def ftd_market_state(
    sp500_chrono: list[dict],
    nasdaq_chrono: list[dict],
    i: int,
    rally_tracker: ModuleType | None = None,
) -> dict:
    """Full production-style FTD state as of position i (inclusive) in the chrono arrays."""
    mods = loader.load_ftd_modules()
    rally = rally_tracker if rally_tracker is not None else mods.rally_tracker
    lo = max(0, i - FTD_WINDOW + 1)
    sp_chrono = sp500_chrono[lo : i + 1]
    nq_chrono = nasdaq_chrono[lo : i + 1]
    state = rally.get_market_state(sp_chrono[::-1], nq_chrono[::-1])
    return mods.post_ftd_monitor.assess_post_ftd_health(state, sp_chrono, nq_chrono)


def dd_state(chrono: list[dict], i: int, rule=None, thresholds=None) -> dd_glue.DDDay:
    lo = max(0, i - DD_WINDOW + 1)
    return dd_glue.analyze(chrono[lo : i + 1][::-1], rule, thresholds)


def _ratio(cur, prev):
    if cur is None or not prev:
        return None
    return cur / prev - 1


def replay(
    histories: dict[str, list[dict]],
    start: str = DEFAULT_START,
    end: str | None = None,
    rally_tracker: ModuleType | None = None,
    keep_raw: bool = False,
) -> list[dict]:
    """Replay both indices. histories: symbol -> chronological bars (identical date axes).

    With keep_raw=True every row also carries (not written to CSV):
      "_ftd": the vendored ftd analysis dict of that index on that day (or None),
      "_dd":  one compact dict per DD record in that day's 85-bar window
              (date, age_sessions, dd_index, status, removal_reason, pct_change,
              volume_change_pct).
    """
    sp = histories[SP500]
    nq = histories[NASDAQ]
    dates = [r["date"] for r in sp]
    if dates != [r["date"] for r in nq]:
        raise ValueError("S&P 500 and NASDAQ date axes differ")
    rule = dd_glue.build_rule()
    thresholds = dd_glue.build_thresholds()
    out: list[dict] = []
    for i, d in enumerate(dates):
        if d < start or (end is not None and d > end):
            continue
        state = ftd_market_state(sp, nq, i, rally_tracker)
        for sym, chrono in ((SP500, sp), (NASDAQ, nq)):
            idx_state = state[STATE_KEY[sym]]
            ftd = idx_state.get("ftd") or {}
            rally = idx_state.get("rally_attempt") or {}
            swing = idx_state.get("swing_low") or {}
            dd = dd_state(chrono, i, rule, thresholds)
            bar = chrono[i]
            prev = chrono[i - 1] if i > 0 else None
            row = {
                "date": d,
                "index": sym,
                "close": bar["close"],
                "pct_change": _ratio(bar["close"], prev["close"] if prev else None),
                "volume_change": _ratio(bar["volume"], prev["volume"] if prev else None),
                "is_dd": dd.today_is_dd,
                "d5": dd.d5,
                "d15": dd.d15,
                "d25": dd.d25,
                "ftd_state": idx_state["state"],
                "ftd_date": ftd.get("ftd_date") if ftd.get("ftd_detected") else None,
                "rally_day1_date": rally.get("day1_date"),
                "swing_low_date": swing.get("swing_low_date"),
                "combined_state": state["combined_state"],
                "risk_level": dd.risk_level,
                "market_below_ma": dd.market_below_ma,
            }
            if keep_raw:
                row["_ftd"] = dict(idx_state["ftd"]) if idx_state.get("ftd") else None
                row["_dd"] = [
                    {
                        "date": r.date,
                        "age_sessions": r.age_sessions,
                        "dd_index": r.dd_index,
                        "status": r.status,
                        "removal_reason": r.removal_reason,
                        "pct_change": r.pct_change,
                        "volume_change_pct": r.volume_change_pct,
                    }
                    for r in dd.records
                ]
            out.append(row)
    return out


def write_daily_status(records: list[dict], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=COLUMNS)
        w.writeheader()
        for r in records:
            w.writerow({k: ("" if r[k] is None else r[k]) for k in COLUMNS})
