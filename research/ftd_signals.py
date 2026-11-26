"""FTD signal variants (spec §3.6).

Correction depth and minimum gain are applied by loading an independent `rally_tracker`
module instance (engine.loader.load_rally_tracker) and running the production 80-bar replay
with it. The events themselves come from engine.events.ftd_events (extend, don't duplicate).

"Require volume above the 50d average" is a POST-FILTER on the vendored output
(`volume_above_avg is True`). Caveat: filtering after detection is not the same as changing
the detector. The vendored detector takes the FIRST qualifying day of a rally attempt, so
the filter drops the whole attempt, while a detector with the volume condition built in would
keep scanning later days (days 4-10) of the same attempt and might emit a later FTD. The
post-filter therefore yields a subset of such a detector's events.
"""

from __future__ import annotations

from engine import events, loader, replay
from research import variants


def tracker_for(correction_pct: float, min_gain: float):
    """Shared default instance for the baseline values, otherwise an independent instance."""
    base = variants.BASELINE_FTD
    c = None if correction_pct == base["correction_pct"] else correction_pct
    g = None if min_gain == base["min_gain"] else min_gain
    return loader.load_rally_tracker(min_correction_pct=c, ftd_gain_minimum=g)


def replay_ftd_rows(
    histories: dict[str, list[dict]],
    rally_tracker,
    start: str,
    end: str | None = None,
) -> list[dict]:
    """Minimal replay rows (FTD fields only) with the schema events.ftd_events expects."""
    sp, nq = histories[replay.SP500], histories[replay.NASDAQ]
    dates = [r["date"] for r in sp]
    if dates != [r["date"] for r in nq]:
        raise ValueError("S&P 500 and NASDAQ date axes differ")
    out: list[dict] = []
    for i, d in enumerate(dates):
        if d < start or (end is not None and d > end):
            continue
        state = replay.ftd_market_state(sp, nq, i, rally_tracker)
        for sym in (replay.SP500, replay.NASDAQ):
            s = state[replay.STATE_KEY[sym]]
            ftd = s.get("ftd") or {}
            out.append(
                {
                    "date": d,
                    "index": sym,
                    "ftd_state": s["state"],
                    "ftd_date": ftd.get("ftd_date") if ftd.get("ftd_detected") else None,
                    "rally_day1_date": (s.get("rally_attempt") or {}).get("day1_date"),
                    "swing_low_date": (s.get("swing_low") or {}).get("swing_low_date"),
                    "_ftd": dict(s["ftd"]) if s.get("ftd") else None,
                }
            )
    return out


def ftd_event_table(
    histories: dict[str, list[dict]],
    correction_pct: float,
    min_gain: float,
    start: str,
    end: str | None = None,
) -> list[dict]:
    """All FTD events (both indices) of one detector configuration, before volume filtering."""
    rows = replay_ftd_rows(histories, tracker_for(correction_pct, min_gain), start, end)
    return events.ftd_events(rows, histories)


def apply_volume_filter(evs: list[dict], require_vol_above_avg: bool) -> list[dict]:
    if not require_vol_above_avg:
        return evs
    return [e for e in evs if e.get("volume_above_avg") is True]
