"""Research R2: FTD leverage overlay on top of buy & hold (docs/plan.md section 3.7).

Idea under test: hold the index at 100%; when a Follow-Through Day is detected, raise the
exposure (125% or 150%, financed by borrowing); return to 100% when the signal says so.

FIXED BEFORE R2's OWN COMPUTATIONS (constants below, never tuned afterwards) - BUT THE TEST
PERIOD WAS NOT UNTOUCHED: before R2 was designed, the project owner ran a preview on 2000-2026
(see TEST_PERIOD_CONTAMINATION; docs/plan.md section 3.7, "下見の結果"). That preview motivated
the exit rule used here (DDs counted after the FTD), so R2's test-period result is NOT a clean
out-of-sample test. Sessions after FORWARD_AFTER form an untouched forward holdout.
  Variants (12 = 3 x 2 x 2, all recorded):
    k in {3, 4, 5}: go back to 100% when k Distribution Days have been counted strictly after
        the FTD day (only DDs known by the close of day t count at day t),
    use_invalidation in {yes, no}: also go back to 100% at the first close below the low of
        the FTD day (same test as vendored post_ftd_monitor.check_ftd_invalidation),
    leverage in {1.25, 1.5}.
  Baseline: buy & hold at 100% (id "buy_hold").
  Selection criterion (per index, design period 2000-01-03..2014-12-31 only, main cost model
    FRED DFF + 1.5% p.a.): among the variants whose design max drawdown is NOT worse than
    buy & hold's design max drawdown minus 5 percentage points, pick the highest design CAGR;
    exact ties -> lower leverage, then smaller k, then invalidation exit on, then variant id
    (this tie rule was defined post hoc on 2026-11-07, see research.selection.TIE_RULE); if
    none qualifies the baseline is kept (fallback recorded). The rule does not require the
    variant to beat buy & hold's CAGR, so a variant with a lower design CAGR than buy & hold
    can be picked.
  The test period (2015-01-02..TEST_END, the frozen end in research.split) is computed once,
    for the baseline and the selected variant only, after the selection was made.

Timing and cost conventions
  * A decision is made at the close of day t from data up to and including day t. The exposure
    applies to the return of day t+1 (close t -> close t+1). Entry: the close of the FTD's
    `detected_on` day (the FTD list is the point-in-time replay output). Exit: the close of
    the day the exit condition first holds. After an exit no new entry the same day; FTDs
    detected while levered are ignored.
  * Daily-rebalanced constant exposure during an episode: strategy return of day k is
        e * r_k  -  (e - 1) * annual_rate * calendar_days(k-1 -> k) / 365.
    Calendar-day accrual (weekends and holidays are charged on the preceding trading day's
    return, as a broker accrues interest every night). The rate used for return k is the one
    known at the close of day k-1: the latest FRED DFF observation dated on or before that
    day, forward-filled (the FRED value of a date is published a day later, a negligible
    lag for a policy-rate series). Borrow cost applies to (exposure - 1) only; cash earns 0.
    AMENDED 2026-11-07 (post hoc): FRED publishes each day's DFF on the next business day, so
    the rate used for the return of day k is the latest observation dated on or before the
    session BEFORE the previous session (dates[k-2]), a value that was published by the close
    of day k-1. RATE_LAG_SESSIONS = 1 is this rule, 0 is the earlier one; the effect of the
    change is computed and recorded (rate_timing_amendment).
  * Metrics use the same 252-trading-day annualisation as research.evaluate.
  * Exposure-matched benchmark: constant exposure equal to the variant's average exposure over
    the same period, same borrow-cost model.
  * Margin stress is a separate diagnostic on an UN-rebalanced position within an episode:
    at entry the position is e (equity 1, loan e-1); the position follows the index, the loan
    grows with the borrow rate; equity ratio = (position - loan) / position. A day is flagged
    when it falls below MAINTENANCE_EQUITY_RATIO, a PLACEHOLDER assumption (25%), not a
    broker's actual requirement.
  * Price indices without dividends: levered dividends are also missing, so the benefit of
    leverage is understated somewhat.
This is research, not a trading recommendation.
"""

from __future__ import annotations

import csv
import json
import urllib.request
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from research import evaluate as ev_mod
from research import freeze, selection
from research.outputs import write_csv
from research.split import TEST_END

K_VALUES = (3, 4, 5)
INVALIDATION_OPTIONS = (True, False)
LEVERAGES = (1.25, 1.5)
N_VARIANTS = len(K_VALUES) * len(INVALIDATION_OPTIONS) * len(LEVERAGES)  # 12
BASELINE_ID = "buy_hold"
MAX_DD_TOLERANCE = 0.05  # design max DD may be at most 5 percentage points worse than B&H
MAINTENANCE_EQUITY_RATIO = 0.25  # PLACEHOLDER assumption, not a broker requirement
SPREAD_OVER_DFF = 0.015
MAIN_COST_ID = "dff_plus_1.5"
COST_MODELS = [
    {"id": MAIN_COST_ID, "kind": "dff", "spread": SPREAD_OVER_DFF, "label": "FRED DFF + 1.5%"},
    {"id": "fixed_0", "kind": "fixed", "rate": 0.0, "label": "fixed 0%"},
    {"id": "fixed_3", "kind": "fixed", "rate": 0.03, "label": "fixed 3%"},
    {"id": "fixed_6", "kind": "fixed", "rate": 0.06, "label": "fixed 6%"},
]
CRITERION = (
    "design period only, main cost model FRED DFF + 1.5% p.a.; variants whose design max "
    f"drawdown is not worse than buy & hold's design max drawdown minus {MAX_DD_TOLERANCE * 100:g} "
    "percentage points qualify; pick the highest design CAGR, which does not have to beat buy & "
    "hold's CAGR (exact ties: lower leverage, then smaller k, then invalidation exit on, then "
    "variant id; this tie rule was added post hoc on 2026-11-07); if none qualifies keep buy & "
    "hold. The rule grid was fixed before R2's own computations, but the test period had been "
    "seen in a preview (test_period_contamination), so R2's test result is not a clean "
    "out-of-sample test"
)
RATE_LAG_SESSIONS = 1  # see the module docstring: DFF dated on or before dates[k-2]
RATE_TIMING_AMENDMENT = {
    "date": "2026-11-07",
    "what": (
        "borrow-rate timing changed: the rate for the return of day k is the latest FRED DFF "
        "observation dated on or before the session before the previous session (dates[k-2]); "
        "before, it was dated on or before the previous session (dates[k-1])"
    ),
    "reason": (
        "FRED publishes each day's DFF on the next business day, so the earlier rule used a "
        "value that was not yet published at the previous close"
    ),
}
POST_HOC_AMENDMENTS = [*selection.POST_HOC_AMENDMENTS, RATE_TIMING_AMENDMENT]
# Sessions after this date form the untouched forward holdout: it is the frozen end of the test
# period (research.split.TEST_END), the last session of the data that existed when the preview
# contamination was found and R2 was reviewed.
FORWARD_AFTER = TEST_END
FORWARD_FIRST_SESSION_NOTE = "2026-11-09"  # the first session after FORWARD_AFTER (a Monday)
# The preview the project owner ran BEFORE R2 was designed (docs/plan.md section 3.7,
# "下見の結果"). It saw the 2015-2026 test period, so R2's test result is not out of sample.
# Figures are recorded from the plan; nothing here is recomputed.
TEST_PERIOD_CONTAMINATION = {
    "date": "2026-11-07",
    "description": (
        "Before R2 was designed, the project owner ran a preview over 2000-2026: enter only "
        "when the risk level was not HIGH or SEVERE, exposure 150%, borrow rate 5% a year, "
        "go back to 100% when the risk level was SEVERE. It was computed once with the rule "
        "fixed first. That preview showed that the entry condition left few chances to enter, "
        "and it is why R2 returns to 100% on the number of DDs counted after the FTD instead "
        "of on the risk level."
    ),
    "preview": {
        "period": ["2000-01-03", "2026-11-06"],
        "entry": "risk level not HIGH or SEVERE",
        "leverage": 1.5,
        "borrow_rate": 0.05,
        "exit": "risk level SEVERE",
        "levered_share_range": [0.10, 0.20],
        "results": [
            {
                "index": "^GSPC",
                "period": [2000, 2014],
                "preview_cagr": 0.021,
                "buy_hold_cagr": 0.023,
            },
            {
                "index": "^GSPC",
                "period": [2015, 2026],
                "preview_cagr": 0.114,
                "buy_hold_cagr": 0.114,
            },
            {
                "index": "^IXIC",
                "period": [2000, 2014],
                "preview_cagr": -0.008,
                "buy_hold_cagr": 0.009,
            },
            {
                "index": "^IXIC",
                "period": [2015, 2026],
                "preview_cagr": 0.172,
                "buy_hold_cagr": 0.149,
            },
        ],
    },
    "consequence": "R2's exit rule was changed after the preview; the test period had been seen",
    "clean_out_of_sample": False,
    "forward_holdout": {
        "data_after": FORWARD_AFTER,
        "first_session_note": FORWARD_FIRST_SESSION_NOTE,
    },
}
DISCLAIMER = "This is research, not a trading recommendation."
DIVIDEND_CAVEAT = (
    "Price indices without dividends: dividends are missing for buy & hold and for the levered "
    "part, so the benefit of leverage is understated somewhat."
)
FRED_URL = "https://fred.stlouisfed.org/graph/fredgraph.csv?id=DFF"
DFF_COPY_NAME = "fred_DFF.csv"  # the copy kept inside the run's research/ folder
TRADING_DAYS = ev_mod.TRADING_DAYS

METRIC_COLS = [
    "n_days",
    "years",
    "cagr",
    "max_dd",
    "vol",
    "avg_exposure",
    "levered_share",
    "n_episodes",
    "worst_levered_dd",
    "margin_stress",
    "margin_stress_days",
    "min_equity_ratio",
    "bh_cagr",
    "bh_max_dd",
    "bh_vol",
    "em_cagr",
    "em_max_dd",
    "em_vol",
]
ID_COLS = ["variant_id", "index", "period", "cost_model", "k", "use_invalidation", "leverage"]
VARIANT_COLUMNS = ID_COLS + METRIC_COLS
TEST_COLUMNS = [
    "index",
    "role",
    "variant_id",
    "period",
    "cost_model",
    "k",
    "use_invalidation",
    "leverage",
] + METRIC_COLS


# ------------------------------------------------------------------------------- variants


def variant_id(k: int, use_invalidation: bool, leverage: float) -> str:
    return f"overlay_k{k}_inv{int(use_invalidation)}_L{leverage:g}"


def grid_definition() -> dict:
    """The pre-registered R2 grid (hashed by research.freeze)."""
    return {
        "k": list(K_VALUES),
        "use_invalidation": list(INVALIDATION_OPTIONS),
        "leverage": list(LEVERAGES),
        "baseline_id": BASELINE_ID,
        "variant_ids": [v["id"] for v in variants()],
    }


def variants() -> list[dict]:
    return [
        {
            "id": variant_id(k, inv, lev),
            "k": k,
            "use_invalidation": inv,
            "leverage": lev,
        }
        for k in K_VALUES
        for inv in INVALIDATION_OPTIONS
        for lev in LEVERAGES
    ]


# ----------------------------------------------------------------------------- decisions


def decide_exposure(
    dates: Sequence[str],
    closes: Sequence[float],
    lows: Sequence[float],
    is_dd: Sequence[bool],
    events: Sequence[dict],
    k: int,
    use_invalidation: bool,
    leverage: float,
) -> tuple[list[float], list[dict]]:
    """exposure_after[t]: exposure held from the close of day t (applies to the return of
    day t+1). Uses only data at or before t when deciding day t.

    `events`: FTDs of ONE index with `ftd_date` and `detected_on` (point-in-time output).
    Returns (exposure_after, episodes) with episodes [{entry, exit|None, reason, ftd_date}].
    """
    n = len(dates)
    pos = {d: i for i, d in enumerate(dates)}
    entries: dict[int, int] = {}
    for e in sorted(events, key=lambda e: (e["detected_on"], e["ftd_date"])):
        if e["detected_on"] in pos and e["ftd_date"] in pos:
            entries.setdefault(pos[e["detected_on"]], pos[e["ftd_date"]])

    exp = [1.0] * n
    episodes: list[dict] = []
    cum = [0] * n  # cumulative DD count up to and including each day
    levered, f, cur = False, -1, None
    for t in range(n):
        cum[t] = (cum[t - 1] if t else 0) + (1 if is_dd[t] else 0)
        if levered:
            reason = None
            if cum[t] - cum[f] >= k:
                reason = "dd_count"
            elif use_invalidation and closes[t] < lows[f]:
                reason = "invalidation"
            if reason:
                levered = False
                cur["exit"], cur["reason"] = dates[t], reason
                episodes.append(cur)
                cur = None
            else:
                exp[t] = leverage
        elif t in entries:
            f = entries[t]
            if cum[t] - cum[f] >= k:
                continue
            if use_invalidation and any(closes[j] < lows[f] for j in range(f + 1, t + 1)):
                continue
            levered = True
            exp[t] = leverage
            cur = {"entry": dates[t], "exit": None, "reason": None, "ftd_date": dates[f]}
    if cur is not None:
        episodes.append(cur)
    return exp, episodes


# ----------------------------------------------------------------------------- rates (DFF)


def load_dff(path: Path) -> list[tuple[str, float]]:
    """[(date, annual rate as a fraction)] sorted; FRED percent values, '.' rows skipped."""
    out = []
    with Path(path).open(newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            v = (row.get("DFF") or "").strip()
            if v in ("", "."):
                continue
            out.append((row["observation_date"], float(v) / 100))
    return sorted(out)


def dff_source(path: Path, shown_as: str = "research/fred_DFF.csv") -> dict:
    """Provenance of the FRED file used: its sha256 and last observation (stored in
    overlay_selected.json; the run keeps a copy so the numbers can be recomputed from it)."""
    import hashlib

    rows = load_dff(path)
    return {
        "file": shown_as,
        "series": "DFF",
        "url": FRED_URL,
        "sha256": hashlib.sha256(Path(path).read_bytes()).hexdigest(),
        "n_observations": len(rows),
        "last_observation": rows[-1][0] if rows else None,
    }


def _http_text(url: str) -> str:
    # Keep urllib's default User-Agent: since 2026-11 FRED drops connections from custom
    # agents such as "ftd-dd-dashboard research" (HTTP/2 stream reset), while
    # "Python-urllib/3.x" is served normally.
    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310 - fixed https URL
        return resp.read().decode("utf-8")


class StaleRateData(RuntimeError):
    """The FRED cache is too old for the price data and may not be refreshed."""


DFF_MAX_AGE_DAYS = 3  # a cache file older than this is refreshed when the network is allowed


def rate_needed_through(dates: Sequence[str]) -> str:
    """Date of the FRED observation the last return of `dates` needs (see dff_staleness)."""
    return dates[-3] if len(dates) >= 3 else dates[0]


def dff_staleness(path: Path, dates: Sequence[str], now: datetime | None = None) -> dict:
    """Is the cached FRED file good enough for the price data in `dates`?

    "hard": the last observation is earlier than the observation the last return needs, or the
    file is missing. The last return (dates[-2] -> dates[-1]) uses rates[k-1] with the lag-1
    rule of `rate_known`, i.e. the newest observation dated on or before dates[-3] -- the same
    date the rate_data gate checks (webgen/validate.py).
    "soft": the file itself is more than DFF_MAX_AGE_DAYS days old (refreshed when allowed, but
    not an error when it must not be)."""
    path = Path(path)
    out: dict = {"hard": None, "soft": None, "last_observation": None}
    if not path.exists():
        out["hard"] = f"{path.name} does not exist"
        return out
    rows = load_dff(path)
    last_obs = rows[-1][0] if rows else None
    out["last_observation"] = last_obs
    need = rate_needed_through(dates)
    if last_obs is None or last_obs < need:
        out["hard"] = (
            f"the last FRED observation ({last_obs}) is earlier than {need}, the date of the "
            "rate the last return of the price data needs"
        )
    age = ((now or datetime.now(UTC)) - datetime.fromtimestamp(path.stat().st_mtime, UTC)).days
    if age > DFF_MAX_AGE_DAYS:
        out["soft"] = f"{path.name} was last written {age} days ago"
    return out


def _write_dff(path: Path, text: str, dates: Sequence[str] | None = None,
               keep_if_short: bool = False) -> bool:
    """Write a FRED download over `path`; return False when it was not used.

    With `dates`, the download is checked before it replaces anything: when it does not cover
    the rate the last return needs and `keep_if_short` is set (the current file is sufficient),
    the current file is kept and the download discarded."""
    if "DFF" not in (text.splitlines() or [""])[0]:
        raise ValueError("unexpected FRED response (no DFF header)")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    if dates and keep_if_short and dff_staleness(tmp, dates)["hard"]:
        tmp.unlink()
        return False
    tmp.replace(path)
    return True


def load_or_fetch_dff(
    path: Path,
    fetch: Callable[[str], str] | None = None,
    *,
    dates: Sequence[str] | None = None,
    allow_network: bool = True,
    now: datetime | None = None,
) -> list[tuple[str, float]]:
    """Read the cached FRED DFF file, downloading it when it is missing or stale.

    With `dates` (the sessions of the price data) the cache is refreshed when its last
    observation is earlier than the one the last return needs (`rate_needed_through`), or when
    the file is older than DFF_MAX_AGE_DAYS. `fetch` is the downloader (tests inject one; None
    uses the real URL). With allow_network False nothing is downloaded: a cache that is stale
    in the first sense raises StaleRateData with a clear message (a merely old file is
    accepted). A failed refresh of an old-but-sufficient file keeps the file; a failed refresh
    of an insufficient one raises, and so does a refresh that downloaded a file which is still
    insufficient -- the network path accepts exactly what the offline recompute accepts."""
    path = Path(path)
    st = dff_staleness(path, dates, now) if dates else {"hard": None, "soft": None}
    if not path.exists():
        st["hard"] = st.get("hard") or f"{path.name} does not exist"
    if st["hard"] or st["soft"]:
        if not allow_network:
            if st["hard"]:
                raise StaleRateData(
                    f"FRED cache is stale: {st['hard']}. This step may not download it; refresh "
                    "it with a run that has network access (with --use-cache, add "
                    "--refresh-fred)."
                )
        else:
            try:
                # only an insufficient cache may be replaced by a download that is still short
                _write_dff(path, (fetch or _http_text)(FRED_URL), dates,
                           keep_if_short=not st["hard"])
            except (OSError, ValueError) as exc:
                if st["hard"]:
                    raise StaleRateData(
                        f"FRED cache is stale ({st['hard']}) and the refresh failed: {exc}"
                    ) from exc
            else:
                if dates:
                    after = dff_staleness(path, dates, now)["hard"]
                    if after:
                        raise StaleRateData(
                            f"FRED was downloaded but is still insufficient: {after}"
                        )
    return load_dff(path)


def rate_known(
    dates: Sequence[str], dff: Sequence[tuple[str, float]], lag: int = RATE_LAG_SESSIONS
) -> list[float]:
    """DFF actually published by the close of each date.

    FRED publishes the value of a day on the next business day, so at the close of date[j] the
    newest published observation is the one dated on or before dates[j-1]. `lag` is the number
    of sessions to step back (1 = this rule; 0 = the earlier rule that used dates[j] itself).
    For the first dates the cutoff is the calendar day before the first session."""
    out, i, cur = [], 0, None
    for j, d in enumerate(dates):
        if j - lag >= 0:
            cutoff = dates[j - lag] if lag else d
        else:
            cutoff = (date.fromisoformat(dates[0]) - timedelta(days=1)).isoformat()
        while i < len(dff) and dff[i][0] <= cutoff:
            cur = dff[i][1]
            i += 1
        if cur is None:
            raise ValueError(f"no DFF observation on or before {cutoff}")
        out.append(cur)
    return out


def annual_rates(
    dates: Sequence[str],
    dff: Sequence[tuple[str, float]],
    cost: dict,
    lag: int = RATE_LAG_SESSIONS,
) -> list[float]:
    if cost["kind"] == "fixed":
        return [cost["rate"]] * len(dates)
    return [r + cost["spread"] for r in rate_known(dates, dff, lag)]


# ------------------------------------------------------------------------------- returns


def _gaps(dates: Sequence[str]) -> list[int]:
    ds = [date.fromisoformat(d) for d in dates]
    return [0] + [(ds[i] - ds[i - 1]).days for i in range(1, len(ds))]


def strategy_returns(
    dates: Sequence[str],
    closes: Sequence[float],
    exp_after: Sequence[float],
    rates: Sequence[float],
    start: str,
    end: str,
) -> dict:
    """Per return day k (start <= dates[k-1], dates[k] <= end): exposure and rate of day k-1."""
    gaps = _gaps(dates)
    out: dict[str, list] = {
        "k": [],
        "e": [],
        "bh": [],
        "ret": [],
        "ratio": [],
        "rate": [],
        "gap": [],
    }
    for k in range(1, len(dates)):
        if dates[k - 1] < start or dates[k] > end:
            continue
        ratio = closes[k] / closes[k - 1]
        r = ratio - 1
        e, rate, gap = exp_after[k - 1], rates[k - 1], gaps[k]
        out["k"].append(k)
        out["e"].append(e)
        out["bh"].append(r)
        out["ret"].append(e * r - (e - 1) * rate * gap / 365)
        out["ratio"].append(ratio)
        out["rate"].append(rate)
        out["gap"].append(gap)
    return out


def _perf(returns: Sequence[float]) -> dict:
    return ev_mod._perf(returns)


def equity_ratio(position: float, loan: float) -> float:
    return (position - loan) / position if position > 0 else -1.0


def evaluate(
    dates: Sequence[str],
    closes: Sequence[float],
    exp_after: Sequence[float],
    rates: Sequence[float],
    start: str,
    end: str,
) -> dict:
    r = strategy_returns(dates, closes, exp_after, rates, start, end)
    n = len(r["ret"])
    if n == 0:
        return {c: None for c in METRIC_COLS} | {"n_days": 0}
    s, b = _perf(r["ret"]), _perf(r["bh"])
    avg = sum(r["e"]) / n
    em_ret = [
        avg * x - (avg - 1) * rate * gap / 365
        for x, rate, gap in zip(r["bh"], r["rate"], r["gap"], strict=True)
    ]
    em = _perf(em_ret)

    eq = 1.0
    worst: float | None = None
    stress_days, min_ratio = 0, None
    n_ep = 0
    in_ep = False
    peak = pos = loan = 0.0
    for e, ret, ratio, rate, gap in zip(
        r["e"], r["ret"], r["ratio"], r["rate"], r["gap"], strict=True
    ):
        if e > 1:
            if not in_ep:
                in_ep, n_ep = True, n_ep + 1
                peak, pos, loan = eq, e, e - 1
            eq *= 1 + ret
            peak = max(peak, eq)
            dd = eq / peak - 1
            worst = dd if worst is None else min(worst, dd)
            pos *= ratio
            loan *= 1 + rate * gap / 365
            ratio_now = equity_ratio(pos, loan)
            min_ratio = ratio_now if min_ratio is None else min(min_ratio, ratio_now)
            if ratio_now < MAINTENANCE_EQUITY_RATIO:
                stress_days += 1
        else:
            in_ep = False
            eq *= 1 + ret
    return {
        "n_days": n,
        "years": s["years"],
        "cagr": s["cagr"],
        "max_dd": s["max_dd"],
        "vol": s["vol"],
        "avg_exposure": avg,
        "levered_share": sum(1 for e in r["e"] if e > 1) / n,
        "n_episodes": n_ep,
        "worst_levered_dd": worst,
        "margin_stress": stress_days > 0,
        "margin_stress_days": stress_days,
        "min_equity_ratio": min_ratio,
        "bh_cagr": b["cagr"],
        "bh_max_dd": b["max_dd"],
        "bh_vol": b["vol"],
        "em_cagr": em["cagr"],
        "em_max_dd": em["max_dd"],
        "em_vol": em["vol"],
    }


# ---------------------------------------------------------------------------- selection


def _simplicity(vid: str) -> tuple:
    """Tie-break key (POST-HOC AMENDMENT 2026-11-07, see research.selection.TIE_RULE): closer
    to buy & hold first = lower leverage, smaller k, invalidation exit on. Observations are
    identical for every variant (same days), so the "more observations" step is a no-op."""
    for v in variants():
        if v["id"] == vid:
            return (v["leverage"], v["k"], 0 if v["use_invalidation"] else 1)
    return ()


def select_overlay(
    rows: Sequence[dict], baseline_id: str = BASELINE_ID, tie: str = selection.TIE_NEW
) -> dict[str, dict]:
    """Per index, from DESIGN rows of the main cost model only (the pre-registered rule).

    `tie` selection.TIE_OLD breaks exact ties by the variant id as a string alone (the way the
    earlier selections did); it exists to show whether the tie rule changed the pick.
    "n_exact_ties_at_top" counts the other qualifying variants with exactly the top CAGR."""
    bad = [r for r in rows if r.get("period") != "design"]
    if bad:
        raise ValueError(
            f"selection accepts design-period rows only; got period={bad[0].get('period')!r}"
        )
    if tie not in (selection.TIE_NEW, selection.TIE_OLD):
        raise ValueError(f"unknown tie rule {tie!r}")

    def order(r):
        if tie == selection.TIE_OLD:
            return (-r["cagr"], r["variant_id"])
        return (-r["cagr"], *_simplicity(r["variant_id"]), r["variant_id"])

    out: dict[str, dict] = {}
    for idx in sorted({r["index"] for r in rows}):
        mine = [r for r in rows if r["index"] == idx]
        cands = sorted(
            (
                r
                for r in mine
                if r["cagr"] is not None and r["max_dd"] >= r["bh_max_dd"] - MAX_DD_TOLERANCE
            ),
            key=order,
        )
        out[idx] = {
            "selected": cands[0]["variant_id"] if cands else baseline_id,
            "fallback_to_baseline": not cands,
            "n_considered": len(mine),
            "n_qualifying": len(cands),
            "n_exact_ties_at_top": (
                sum(1 for r in cands[1:] if r["cagr"] == cands[0]["cagr"]) if cands else 0
            ),
            "criterion": CRITERION,
        }
    return out


def tie_rule_effect(rows: Sequence[dict]) -> dict[str, dict]:
    """Did the R2 tie rule change a pick? Compares the rule with the string-order fallback."""
    new = select_overlay(rows, tie=selection.TIE_NEW)
    old = select_overlay(rows, tie=selection.TIE_OLD)
    return {
        idx: {
            "pick": new[idx]["selected"],
            "pick_string_order": old[idx]["selected"],
            "changed": new[idx]["selected"] != old[idx]["selected"],
            "n_exact_ties_at_top": new[idx]["n_exact_ties_at_top"],
        }
        for idx in new
    }


def forward_record(
    dates: Sequence[str],
    closes: Sequence[float],
    exp_after: Sequence[float],
    rates: Sequence[float],
    after: str,
    last: str,
) -> dict:
    """Performance over the return days whose previous close is on or after `after` (the
    sessions following the last session seen when R2 was reviewed). Empty until data exists:
    n_days 0 and every figure None. Same return code as the other periods."""
    r = strategy_returns(dates, closes, exp_after, rates, after, last)
    n = len(r["ret"])
    empty = {
        "n_days": 0,
        "first_date": None,
        "last_date": None,
        "cum_return": None,
        "bh_cum_return": None,
        "max_dd": None,
        "bh_max_dd": None,
        "levered_days": 0,
    }
    if n == 0:
        return empty

    def path(rets):
        eq = peak = 1.0
        worst = 0.0
        for x in rets:
            eq *= 1 + x
            peak = max(peak, eq)
            worst = min(worst, eq / peak - 1)
        return eq - 1, worst

    cum, mdd = path(r["ret"])
    bcum, bmdd = path(r["bh"])
    return {
        "n_days": n,
        "first_date": dates[r["k"][0]],
        "last_date": dates[r["k"][-1]],
        "cum_return": cum,
        "bh_cum_return": bcum,
        "max_dd": mdd,
        "bh_max_dd": bmdd,
        "levered_days": sum(1 for e in r["e"] if e > 1),
    }


# ------------------------------------------------------------------------------ pipeline


def _row(vid, idx, period, cost_id, params, metrics) -> dict:
    return {
        "variant_id": vid,
        "index": idx,
        "period": period,
        "cost_model": cost_id,
        "k": params.get("k"),
        "use_invalidation": params.get("use_invalidation"),
        "leverage": params.get("leverage", 1.0),
    } | {c: metrics.get(c) for c in METRIC_COLS}


def _bands(dates: Sequence[str], episodes: Sequence[dict]) -> list[dict]:
    return [
        {
            "x0": e["entry"],
            "x1": e["exit"] or dates[-1],
            "ftd_date": e["ftd_date"],
            "reason": e["reason"] or "open",
        }
        for e in episodes
    ]


def run_overlay(
    histories: dict[str, list[dict]],
    ftd_events: Sequence[dict],
    is_dd_by_index: dict[str, Sequence[bool]],
    dff: Sequence[tuple[str, float]],
    design_start: str,
    design_end: str,
    test_start: str,
    log: Callable[[str], None] | None = None,
    test_end: str | None = None,
) -> dict:
    """`test_end`: frozen end of the test period (research.split.TEST_END); sessions after it
    are used only by the forward record."""
    log = log or (lambda m: None)
    vs = variants()
    by_id = {v["id"]: v for v in vs}
    design_rows: list[dict] = []
    test_rows: list[dict] = []
    compared_design: list[dict] = []
    legacy_rows: list[dict] = []  # design rows under the earlier rate timing (amendment effect)
    bands: dict[str, list[dict]] = {}
    series: dict[str, dict] = {}
    last = next(iter(histories.values()))[-1]["date"]
    test_end = min(test_end or TEST_END, last)
    main_cost = next(c for c in COST_MODELS if c["id"] == MAIN_COST_ID)

    for idx, bars in histories.items():
        dates = [b["date"] for b in bars]
        closes = [b["close"] for b in bars]
        lows = [b["low"] for b in bars]
        is_dd = list(is_dd_by_index[idx])
        if len(is_dd) != len(dates):
            raise ValueError(f"{idx}: DD flags ({len(is_dd)}) and bars ({len(dates)}) differ")
        mine = [e for e in ftd_events if e["index"] == idx]
        rates = {c["id"]: annual_rates(dates, dff, c) for c in COST_MODELS}
        exp_by: dict[str, list[float]] = {BASELINE_ID: [1.0] * len(dates)}
        eps_by: dict[str, list[dict]] = {BASELINE_ID: []}
        for v in vs:
            exp_by[v["id"]], eps_by[v["id"]] = decide_exposure(
                dates, closes, lows, is_dd, mine, v["k"], v["use_invalidation"], v["leverage"]
            )
        legacy_rate = annual_rates(dates, dff, main_cost, lag=0)
        series[idx] = {
            "dates": dates,
            "closes": closes,
            "exp": exp_by,
            "rates": rates,
            "legacy_rate": legacy_rate,
            "eps": eps_by,
        }

        def evaluate_at(vid, cost_id, start, end, _s=series[idx]):
            return evaluate(
                _s["dates"], _s["closes"], _s["exp"][vid], _s["rates"][cost_id], start, end
            )

        for v in vs:
            for c in COST_MODELS:
                m = evaluate_at(v["id"], c["id"], design_start, design_end)
                design_rows.append(_row(v["id"], idx, "design", c["id"], v, m))
            m_old = evaluate(dates, closes, exp_by[v["id"]], legacy_rate, design_start, design_end)
            legacy_rows.append(_row(v["id"], idx, "design", MAIN_COST_ID, v, m_old))
        log(f"  overlay design {idx}: {len(vs)} variants x {len(COST_MODELS)} cost models")

    main_rows = [r for r in design_rows if r["cost_model"] == MAIN_COST_ID]
    chosen = select_overlay(main_rows, BASELINE_ID)
    tie_effect = tie_rule_effect(main_rows)
    legacy_chosen = select_overlay(legacy_rows, BASELINE_ID)
    forward: dict[str, dict] = {}
    rate_effect: dict[str, dict] = {}

    # ---- test period: once, for the baseline and the selected variant of each index
    for idx, info in chosen.items():
        s = series[idx]
        sel = info["selected"]
        roles = [("baseline", BASELINE_ID)]
        if sel != BASELINE_ID:
            roles.append(("selected", sel))
        for role, vid in roles:
            params = by_id.get(vid, {})
            for c in COST_MODELS:
                m_test = evaluate(
                    s["dates"],
                    s["closes"],
                    s["exp"][vid],
                    s["rates"][c["id"]],
                    test_start,
                    test_end,
                )
                row = _row(vid, idx, "test", c["id"], params, m_test)
                row["role"] = role
                test_rows.append(row)
                m_des = evaluate(
                    s["dates"],
                    s["closes"],
                    s["exp"][vid],
                    s["rates"][c["id"]],
                    design_start,
                    design_end,
                )
                drow = _row(vid, idx, "design", c["id"], params, m_des)
                drow["role"] = role
                compared_design.append(drow)
        # chart band: levered periods of the selected variant over the whole history
        if sel == BASELINE_ID:
            bands[idx] = []
        else:
            eps = s["eps"][sel]
            bands[idx] = _bands(s["dates"], eps)
        # forward record: sessions after FORWARD_AFTER, same return code, main cost model
        forward[idx] = {"after": FORWARD_AFTER, "last": last}
        for role, vid in roles:
            forward[idx][role] = forward_record(
                s["dates"],
                s["closes"],
                s["exp"][vid],
                s["rates"][MAIN_COST_ID],
                FORWARD_AFTER,
                last,
            )
        # effect of the post-hoc rate-timing amendment on the picked variant (main cost model)
        before = {}
        for period, (p0, p1) in (
            ("design", (design_start, design_end)),
            ("test", (test_start, test_end)),
        ):
            new_m = evaluate(
                s["dates"], s["closes"], s["exp"][sel], s["rates"][MAIN_COST_ID], p0, p1
            )
            old_m = evaluate(s["dates"], s["closes"], s["exp"][sel], s["legacy_rate"], p0, p1)
            before[period] = {"cagr_before": old_m["cagr"], "cagr_after": new_m["cagr"]}
        rate_effect[idx] = {
            "pick_before": legacy_chosen[idx]["selected"],
            "pick_after": sel,
            "pick_changed": legacy_chosen[idx]["selected"] != sel,
            **before,
        }
    return {
        "design_rows": design_rows,
        "test_rows": test_rows,
        "design_of_compared": compared_design,
        "chosen": chosen,
        "bands": bands,
        "n_variants": N_VARIANTS,
        "variants": vs,
        "last_date": last,
        "split": {"design": [design_start, design_end], "test": [test_start, test_end]},
        "forward": forward,
        "tie_effect": tie_effect,
        "rate_effect": rate_effect,
    }


# ------------------------------------------------------------------------------- outputs


def _by_cost(rows: list[dict], idx: str, role: str) -> dict:
    out = {}
    for r in rows:
        if r["index"] == idx and r["role"] == role:
            out[r["cost_model"]] = {c: r[c] for c in METRIC_COLS}
    return out


def selected_json(
    result: dict, provenance: dict | None = None, dff_source: dict | None = None
) -> dict:
    by_id = {v["id"]: v for v in result["variants"]}
    indices = {}
    for idx, info in result["chosen"].items():
        sel = info["selected"]
        roles = ["baseline"] if sel == BASELINE_ID else ["baseline", "selected"]
        indices[idx] = {
            "selected": sel,
            "selected_params": {k: v for k, v in by_id.get(sel, {}).items() if k != "id"},
            "fallback_to_baseline": info["fallback_to_baseline"],
            "n_considered": info["n_considered"],
            "n_qualifying": info["n_qualifying"],
            "design": {r: _by_cost(result["design_of_compared"], idx, r) for r in roles},
            "test": {r: _by_cost(result["test_rows"], idx, r) for r in roles},
            "bands": result["bands"].get(idx, []),
            "forward": result["forward"][idx],
            "tie_effect": result["tie_effect"][idx],
            "rate_timing_effect": result["rate_effect"][idx],
        }
    return {
        "split": result["split"],
        "n_variants": result["n_variants"],
        "baseline_id": BASELINE_ID,
        "criterion": CRITERION,
        "tie_rule": selection.TIE_RULE,
        "post_hoc_amendments": POST_HOC_AMENDMENTS,
        "freeze": freeze.info("overlay"),
        "freeze_provenance": provenance,
        "test_period_contamination": TEST_PERIOD_CONTAMINATION,
        "dff_source": dff_source,
        "rate_lag_sessions": RATE_LAG_SESSIONS,
        "main_cost_model": MAIN_COST_ID,
        "cost_models": COST_MODELS,
        "maintenance_equity_ratio": MAINTENANCE_EQUITY_RATIO,
        "maintenance_is_placeholder": True,
        "max_dd_tolerance": MAX_DD_TOLERANCE,
        "variant_grid": {
            "k": list(K_VALUES),
            "use_invalidation": list(INVALIDATION_OPTIONS),
            "leverage": list(LEVERAGES),
        },
        "disclaimer": DISCLAIMER,
        "dividend_caveat": DIVIDEND_CAVEAT,
        "indices": indices,
    }


def write_outputs(
    result: dict,
    out_dir: Path,
    provenance: dict | None = None,
    dff_source: dict | None = None,
) -> dict:
    out_dir = Path(out_dir)
    write_csv(out_dir / "overlay_variants.csv", VARIANT_COLUMNS, result["design_rows"])
    write_csv(out_dir / "overlay_test_results.csv", TEST_COLUMNS, result["test_rows"])
    sel = selected_json(result, provenance, dff_source)
    (out_dir / "overlay_selected.json").write_text(
        json.dumps(sel, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return sel
