"""Event extraction from a replay (spec §3.3).

All functions take the rows produced by `replay.replay(..., keep_raw=True)`.

- An FTD event is keyed by (index, ftd_date) and is created on the first replay day that
  reports that ftd_date. The FTD leaving the 60-bar analysis window (ftd_date disappears or
  the state changes) never creates an event, nor does it re-create one if the same
  ftd_date reappears.
- Percent columns (`*_pct`) are in percent units (1.5 = 1.5%). `gain_pct` is the vendored
  value; the others are converted from fractions for consistency.
- Invalidation of an FTD is NOT read off state changes. It is computed with the vendored
  `check_ftd_invalidation` on the chronological history sliced to ftd_idx + 60 sessions, so
  the answer is bounded to "within 60 sessions".
"""

from __future__ import annotations

from collections.abc import Sequence

from engine import loader

FTD_EVENT_COLUMNS = [
    "index",
    "rally_day1_date",
    "swing_low_date",
    "ftd_date",
    "detected_on",
    "lag_sessions",
    "ftd_day_number",
    "gain_pct",
    "volume_vs_prev_pct",
    "volume_above_avg",
    "invalidated_at_detection",
    "invalidation_date",
    "invalidated_within_60",
]
RALLY_COLUMNS = ["index", "rally_day1_date", "swing_low_date", "detected_on", "lag_sessions"]
DD_COLUMNS = [
    "index",
    "date",
    "pct_change",
    "volume_change_pct",
    "expires_on",
    "invalidated_on",
    "removal_reason",
]
INVALIDATION_HORIZON = 60
DD_EXPIRY_SESSIONS = 26  # age > 25 with the DD day counted as 0 -> the 26th session


def sessions_from(histories: dict[str, list[dict]]) -> list[str]:
    """Session dates from the (validated) history axis."""
    return [b["date"] for b in next(iter(histories.values()))]


def _by_index(rows: Sequence[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for r in rows:
        out.setdefault(r["index"], []).append(r)
    return out


def _lag(pos: dict[str, int], earlier: str, later: str) -> int:
    return pos[later] - pos[earlier]


def ftd_events(
    rows: Sequence[dict],
    histories: dict[str, list[dict]],
    sessions: Sequence[str] | None = None,
) -> list[dict]:
    sessions = list(sessions) if sessions is not None else sessions_from(histories)
    pos = {d: i for i, d in enumerate(sessions)}
    post = loader.load_ftd_modules().post_ftd_monitor
    out: list[dict] = []
    for sym, srows in _by_index(rows).items():
        chrono = histories[sym]
        replay_start = min(r["date"] for r in srows)
        seen: set[str] = set()
        for r in srows:
            fd = r["ftd_date"]
            if not fd or fd in seen:
                continue
            seen.add(fd)
            if fd < replay_start:
                # Dated before the replay began: its first appearance is an artifact
                # of the replay start, not a point-in-time detection. Exclude it.
                continue
            ftd = r["_ftd"]
            fi = pos[fd]
            bounded = chrono[: fi + INVALIDATION_HORIZON + 1]
            chk = post.check_ftd_invalidation(bounded, fi)
            if chk["invalidated"]:
                within, inv_date = True, chk["invalidation_date"]
            elif len(chrono) - 1 - fi >= INVALIDATION_HORIZON:
                within, inv_date = False, None
            else:
                within, inv_date = None, None  # not enough future data to decide
            prev = ftd.get("prev_day_volume")
            vol = ftd.get("volume")
            out.append(
                {
                    "index": sym,
                    "rally_day1_date": r["rally_day1_date"],
                    "swing_low_date": r["swing_low_date"],
                    "ftd_date": fd,
                    "detected_on": r["date"],
                    "lag_sessions": _lag(pos, fd, r["date"]),
                    "ftd_day_number": ftd.get("ftd_day_number"),
                    "gain_pct": ftd.get("gain_pct"),
                    "volume_vs_prev_pct": (vol / prev - 1) * 100 if prev else None,
                    "volume_above_avg": ftd.get("volume_above_avg"),
                    "invalidated_at_detection": r["ftd_state"] == "FTD_INVALIDATED",
                    "invalidation_date": inv_date,
                    "invalidated_within_60": within,
                }
            )
    out.sort(key=lambda e: (e["detected_on"], e["index"]))
    return out


def rally_attempts(rows: Sequence[dict], sessions: Sequence[str]) -> list[dict]:
    """One row per (index, rally_day1_date), keyed on first appearance in the replay.

    Rallies whose `rally_day1_date` is before the replay start are excluded (same rule as
    `ftd_events`).

    `detected_on` means "first day the rally appeared in the replay". It can lag day 1 by more
    than a live user would see, because the vendored step-2 swing-low search can surface an
    older rally later. It is not a point-in-time detection date.
    """
    pos = {d: i for i, d in enumerate(sessions)}
    out: list[dict] = []
    for sym, srows in _by_index(rows).items():
        seen: set[str] = set()
        replay_start = min(r["date"] for r in srows)
        for r in srows:
            d1 = r["rally_day1_date"]
            if not d1 or d1 in seen:
                continue
            seen.add(d1)
            if d1 < replay_start:
                continue
            out.append(
                {
                    "index": sym,
                    "rally_day1_date": d1,
                    "swing_low_date": r["swing_low_date"],
                    "detected_on": r["date"],
                    "lag_sessions": _lag(pos, d1, r["date"]) if d1 in pos else None,
                }
            )
    out.sort(key=lambda e: (e["detected_on"], e["index"]))
    return out


def distribution_days(rows: Sequence[dict], sessions: Sequence[str]) -> list[dict]:
    """One row per (index, date) that was a DD on its own day (dd_index == 0 in the replay).

    `expires_on` / `removal_reason="expired_25_sessions"` are reported only when the expiry
    session lies within the replayed range (<= the last replay row); otherwise both are None.
    For an invalidated DD, `removal_reason` is the invalidation reason only, and `expires_on`
    keeps the nominal expiry date (when known) for reference.
    """
    pos = {d: i for i, d in enumerate(sessions)}
    out: list[dict] = []
    for sym, srows in _by_index(rows).items():
        dds: dict[str, dict] = {}
        for r in srows:
            for rec in r["_dd"]:
                if rec["dd_index"] == 0 and rec["date"] not in dds:
                    k = pos[rec["date"]]
                    exp = (
                        sessions[k + DD_EXPIRY_SESSIONS]
                        if k + DD_EXPIRY_SESSIONS < len(sessions)
                        else None
                    )
                    dds[rec["date"]] = {
                        "index": sym,
                        "date": rec["date"],
                        "pct_change": rec["pct_change"] * 100,
                        "volume_change_pct": rec["volume_change_pct"] * 100,
                        "expires_on": exp,
                        "invalidated_on": None,
                        "removal_reason": None,
                    }
        # later days: first day the record for that DD date is invalidated
        for r in srows:
            for rec in r["_dd"]:
                d = dds.get(rec["date"])
                if d and d["invalidated_on"] is None and rec["status"] == "invalidated":
                    d["invalidated_on"] = r["date"]
                    d["removal_reason"] = rec["removal_reason"]
        last_replay = max(r["date"] for r in srows)
        for d in dds.values():
            if d["invalidated_on"] is not None:
                continue  # removal_reason is the invalidation; expires_on stays as computed
            if d["expires_on"] is not None and d["expires_on"] > last_replay:
                # expiry would fall after the last replayed session: not observed, not claimed
                d["expires_on"] = None
            if d["expires_on"] is not None:
                d["removal_reason"] = "expired_25_sessions"
        out.extend(dds.values())
    out.sort(key=lambda e: (e["date"], e["index"]))
    return out
