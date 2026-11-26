"""Post-event outcomes and summary statistics (spec §3.4).

- FTD outcomes are measured from the CLOSE on `detected_on` (what a user could have seen).
  `ref_fwd_*_from_ftd` are reference columns measured from `ftd_date`.
- Forward returns / drawdown are blank (None) when the future bars do not exist yet.
- Statistics are median + quartiles + n (no mean), kept per index (never pooled).
- DD-cluster statistics are reported on all days, on a non-overlapping subsample, and on
  calendar-year blocks (median of per-year medians; spec §3.4 block method).
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Sequence

FTD_HORIZONS = (5, 10, 20, 60)
REF_HORIZONS = (20, 60)
DD_HORIZONS = (20, 60)
DD_BUCKETS = ("0-3", "4", "5", "6+")

FTD_OUTCOME_COLUMNS = [
    "index",
    "ftd_date",
    "detected_on",
    "fwd_5",
    "fwd_10",
    "fwd_20",
    "fwd_60",
    "max_dd_60",
    "ref_fwd_20_from_ftd",
    "ref_fwd_60_from_ftd",
    "invalidated_within_60",
]
DD_DAY_COLUMNS = ["index", "date", "d25", "bucket", "fwd_20", "fwd_60"]
DD_STAT_COLUMNS = ["index", "bucket", "horizon", "basis", "n", "n_years", "median", "p25", "p75"]
RISK_LEVELS = ("NORMAL", "CAUTION", "HIGH", "SEVERE")
RISK_STAT_COLUMNS = [
    "index",
    "risk_level",
    "horizon",
    "basis",
    "n",
    "n_years",
    "median",
    "p25",
    "p75",
]


# ------------------------------------------------------------------ statistics


def _percentile(sorted_vals: Sequence[float], q: float) -> float:
    """Linear-interpolation percentile (numpy default), q in [0, 1]."""
    pos = (len(sorted_vals) - 1) * q
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return sorted_vals[lo]
    return sorted_vals[lo] + (sorted_vals[hi] - sorted_vals[lo]) * (pos - lo)


def summarize(values: Iterable[float | None]) -> dict:
    """n, median, p25, p75. None / NaN are ignored. No mean by design."""
    vals = sorted(v for v in values if v is not None and not math.isnan(v))
    if not vals:
        return {"n": 0, "median": None, "p25": None, "p75": None}
    return {
        "n": len(vals),
        "median": _percentile(vals, 0.5),
        "p25": _percentile(vals, 0.25),
        "p75": _percentile(vals, 0.75),
    }


def subsample_non_overlapping(seq: Sequence, horizon: int) -> list:
    """Every `horizon`-th element: consecutive picks are >= horizon sessions apart."""
    if horizon < 1:
        raise ValueError("horizon must be >= 1")
    return list(seq[::horizon])


# ------------------------------------------------------------------ FTD outcomes


def _fwd(closes: Sequence[float], i: int, k: int) -> float | None:
    if i + k >= len(closes):
        return None
    return closes[i + k] / closes[i] - 1


def ftd_outcomes(events: Sequence[dict], histories: dict[str, list[dict]]) -> list[dict]:
    """One outcome row per FTD event.

    `max_dd_60` is the close-to-close drawdown from the entry close (the close on
    `detected_on`): the lowest of the next 60 closes relative to the entry close, capped at 0
    (a path that never closes below the entry close has a drawdown of 0, not a gain).
    Intraday lows are not used.
    """
    pos_cache: dict[str, dict[str, int]] = {}
    close_cache: dict[str, list[float]] = {}
    out: list[dict] = []
    for e in events:
        sym = e["index"]
        if sym not in pos_cache:
            pos_cache[sym] = {b["date"]: i for i, b in enumerate(histories[sym])}
            close_cache[sym] = [b["close"] for b in histories[sym]]
        pos, closes = pos_cache[sym], close_cache[sym]
        di = pos[e["detected_on"]]
        fi = pos[e["ftd_date"]]
        row = {"index": sym, "ftd_date": e["ftd_date"], "detected_on": e["detected_on"]}
        for k in FTD_HORIZONS:
            row[f"fwd_{k}"] = _fwd(closes, di, k)
        if di + 60 < len(closes):
            row["max_dd_60"] = min(0.0, min(closes[di + 1 : di + 61]) / closes[di] - 1)
        else:
            row["max_dd_60"] = None
        for k in REF_HORIZONS:
            row[f"ref_fwd_{k}_from_ftd"] = _fwd(closes, fi, k)
        row["invalidated_within_60"] = e.get("invalidated_within_60")
        out.append(row)
    return out


def ftd_outcome_stats(outcome_rows: Sequence[dict]) -> dict[str, dict]:
    """{index: {metric: summary}} for fwd_*, max_dd_60 and ref columns."""
    metrics = [f"fwd_{k}" for k in FTD_HORIZONS] + ["max_dd_60"]
    metrics += [f"ref_fwd_{k}_from_ftd" for k in REF_HORIZONS]
    out: dict[str, dict] = {}
    for sym in sorted({r["index"] for r in outcome_rows}):
        mine = [r for r in outcome_rows if r["index"] == sym]
        out[sym] = {m: summarize(r.get(m) for r in mine) for m in metrics}
    return out


# ------------------------------------------------------------------ DD clusters


def dd_bucket(d25: int) -> str:
    if d25 <= 3:
        return "0-3"
    if d25 == 4:
        return "4"
    if d25 == 5:
        return "5"
    return "6+"


def dd_day_forward(status_rows: Sequence[dict], histories: dict[str, list[dict]]) -> list[dict]:
    """Per replay day: bucket by active d25 and 20/60-session forward returns from its close."""
    pos_cache: dict[str, dict[str, int]] = {}
    close_cache: dict[str, list[float]] = {}
    out: list[dict] = []
    for r in status_rows:
        sym = r["index"]
        if sym not in pos_cache:
            pos_cache[sym] = {b["date"]: i for i, b in enumerate(histories[sym])}
            close_cache[sym] = [b["close"] for b in histories[sym]]
        i = pos_cache[sym][r["date"]]
        closes = close_cache[sym]
        out.append(
            {
                "index": sym,
                "date": r["date"],
                "d25": r["d25"],
                "bucket": dd_bucket(r["d25"]),
                "risk_level": r.get("risk_level"),
                "fwd_20": _fwd(closes, i, 20),
                "fwd_60": _fwd(closes, i, 60),
            }
        )
    return out


def _grouped_stats(day_rows: Sequence[dict], field: str, categories: Sequence[str]) -> list[dict]:
    """Per index x category x horizon: all-days and non-overlapping summaries.

    Non-overlapping: within a (index, category) chronological sequence of days that have a
    defined forward return, take every h-th day (h = horizon).
    """
    out: list[dict] = []
    for sym in sorted({r["index"] for r in day_rows}):
        mine = sorted((r for r in day_rows if r["index"] == sym), key=lambda r: r["date"])
        for cat in categories:
            seq = [r for r in mine if r[field] == cat]
            for h in DD_HORIZONS:
                vals = [r[f"fwd_{h}"] for r in seq if r[f"fwd_{h}"] is not None]
                for basis, v in (
                    ("all_days", vals),
                    ("non_overlapping", subsample_non_overlapping(vals, h)),
                ):
                    out.append(
                        {"index": sym, field: cat, "horizon": h, "basis": basis, "n_years": None}
                        | summarize(v)
                    )
                # calendar-year blocks: median within each year, then quartiles across years
                by_year: dict[str, list[float]] = {}
                for r in seq:
                    if r[f"fwd_{h}"] is not None:
                        by_year.setdefault(str(r["date"])[:4], []).append(r[f"fwd_{h}"])
                year_meds = [summarize(v)["median"] for _, v in sorted(by_year.items())]
                s = summarize(year_meds)
                out.append(
                    {"index": sym, field: cat, "horizon": h, "basis": "calendar_year_blocks"}
                    | {"n": len(vals), "n_years": s["n"]}
                    | {k: s[k] for k in ("median", "p25", "p75")}
                )
    return out


def dd_bucket_stats(day_rows: Sequence[dict]) -> list[dict]:
    """Per index x d25 bucket x horizon (see `_grouped_stats`)."""
    return _grouped_stats(day_rows, "bucket", DD_BUCKETS)


def risk_level_stats(day_rows: Sequence[dict]) -> list[dict]:
    """Per index x risk_level x horizon (§3.4.1); same shape as `dd_bucket_stats`."""
    return _grouped_stats(day_rows, "risk_level", RISK_LEVELS)


def risk_level_counts(day_rows: Sequence[dict]) -> dict[str, dict[str, int]]:
    """{index: {level: number of replay days}} for all four levels (zeros included)."""
    out: dict[str, dict[str, int]] = {}
    for r in day_rows:
        c = out.setdefault(r["index"], dict.fromkeys(RISK_LEVELS, 0))
        c[r["risk_level"]] += 1
    return out
