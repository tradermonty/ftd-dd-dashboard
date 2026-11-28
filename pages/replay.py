"""Replay the vendored ftd-detector (pages/skill) day by day on the local FMP cache.

Each session since 2000-01-03 is judged from the 500 bars that end on it, the way the skill
runs in production. Writes one row per session to <out csv>.

usage: python pages/replay.py <out csv>
"""

import csv
import functools
import importlib.util
import json
import sys
import time
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "pages" / "skill"
WINDOW = 500
START = "2000-01-03"


def load_skill():
    sys.path.insert(0, str(SKILL))
    spec = importlib.util.spec_from_file_location("rally_tracker_v2", SKILL / "rally_tracker.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    # The skill asks the exchange calendar about each bar on every call (about 0.1 s each),
    # which makes a 26-year replay take hours. A session's calendar entry never changes, so
    # cache it; the skill's own logic is untouched.
    cal = sys.modules["_market_calendar"]
    original = cal.is_early_close
    early = _early_closes()
    # spot-check the shortcut against the skill's own answer on recent sessions and every
    # early close we know, so a calendar change cannot silently diverge
    probe = sorted(early)[-12:] + [date(2026, 11, 25), date(2026, 11, 30), date(2025, 7, 7)]
    for d in probe:
        if original("XNYS", d) != (d in early):
            raise SystemExit(f"early-close shortcut disagrees with the skill on {d}")

    def is_early_close(venue, session_date):
        if venue not in ("XNYS", "NYSE"):
            return original(venue, session_date)
        return session_date in early

    cal.is_early_close = is_early_close

    # session_for_date asks the provider one day at a time; read the whole schedule once
    norm, tz, sched = cal._schedule("XNYS", date(1995, 1, 1), date(2035, 12, 31))
    rows = {ts.date(): row for ts, row in sched.iterrows()}
    original_session = cal.session_for_date

    @functools.cache
    def session_for_date(venue, session_date):
        if venue not in ("XNYS", "NYSE") or not isinstance(session_date, date):
            return original_session(venue, session_date)
        row = rows.get(session_date)
        return None if row is None else cal._session_from_row(norm, tz, session_date, row)

    for d in probe + [date(2026, 11, 26), date(2026, 11, 28)]:
        if original_session("XNYS", d) != session_for_date("XNYS", d):
            raise SystemExit(f"session shortcut disagrees with the skill on {d}")
    cal.session_for_date = session_for_date
    return mod


@functools.lru_cache(maxsize=1)
def _early_closes() -> frozenset:
    import pandas_market_calendars as pmc

    nyse = pmc.get_calendar("NYSE")
    sched = nyse.schedule(start_date="1995-01-01", end_date="2035-12-31")
    return frozenset(ts.date() for ts in nyse.early_closes(sched).index)


def bars(prefix: str) -> list[dict]:
    out = {}
    for p in sorted((ROOT / "data/raw").glob(f"{prefix}_*.json")):
        for b in json.loads(p.read_text()):
            out[b["date"]] = b
    return [out[d] for d in sorted(out)]


def main(out: Path) -> None:
    rt = load_skill()
    sp, nq = bars("GSPC"), bars("IXIC")
    dates = [b["date"] for b in sp]
    if dates != [b["date"] for b in nq]:
        raise SystemExit("S&P 500 and Nasdaq bars are not on the same sessions")
    rows, t0 = [], time.time()
    for i, d in enumerate(dates):
        if d < START:
            continue
        lo = max(0, i - WINDOW + 1)
        s = rt.get_market_state(sp[lo : i + 1][::-1], nq[lo : i + 1][::-1])
        row = {
            "date": d,
            "combined_status": s.get("market_status"),
            "combined_state": s["combined_state"],
        }
        for k in ("sp500", "nasdaq"):
            a = s[k]
            ra = a.get("rally_attempt") or {}
            cyc = a.get("cycle") or {}
            f = a.get("ftd") or {}
            row.update(
                {
                    f"{k}_state": a["state"],
                    f"{k}_status": a.get("market_status"),
                    f"{k}_day1": ra.get("day1_date"),
                    f"{k}_day": ra.get("current_day_count"),
                    f"{k}_ref_low_date": ra.get("reference_low_date"),
                    f"{k}_ftd": f.get("ftd_date") if f.get("ftd_detected") else None,
                    f"{k}_pressure": "|".join(cyc.get("pressure_reasons") or []),
                    f"{k}_active_dd": cyc.get("active_distribution_days"),
                }
            )
        rows.append(row)
        if len(rows) % 1000 == 0:
            print(f"  {len(rows)} sessions, {time.time() - t0:.0f}s", flush=True)
    out.parent.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    with tmp.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    tmp.replace(out)
    print(f"wrote {out} ({len(rows)} sessions, last {rows[-1]['date']}, {time.time() - t0:.0f}s)")


if __name__ == "__main__":
    main(Path(sys.argv[1]))
