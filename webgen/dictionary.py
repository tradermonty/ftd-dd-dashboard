"""Data dictionary: every column of every published CSV, with type and unit.

The dictionary is generated from the column constants of the code that writes the CSVs, so
it cannot drift from them; the publish gate re-parses the rendered markdown and compares it
with the real CSV headers.
"""

from __future__ import annotations

import re

from engine import events, outcomes, replay
from research import outputs, overlay

# (type, unit, description)
Col = tuple[str, str, str]

PCT_FRACTION = "fraction (0.01 = 1%)"
PCT_POINTS = "percent (1.5 = 1.5%)"

DAILY_STATUS: dict[str, Col] = {
    "date": ("date", "ISO 8601", "XNYS session date."),
    "index": ("str", "", "Index symbol: ^GSPC (S&P 500) or ^IXIC (Nasdaq Composite)."),
    "close": ("float", "index points", "Closing level (price index, no dividends)."),
    "pct_change": (
        "float",
        PCT_FRACTION,
        "Close-to-close change versus the previous session. Empty on the first fetched bar.",
    ),
    "volume_change": ("float", PCT_FRACTION, "Volume change versus the previous session."),
    "is_dd": ("bool", "", "True when this session is a Distribution Day (rule of the skill)."),
    "d5": (
        "int",
        "count",
        "Active Distribution Days among today plus the previous 5 sessions (6 sessions).",
    ),
    "d15": (
        "int",
        "count",
        "Active Distribution Days among today plus the previous 15 sessions (16 sessions).",
    ),
    "d25": (
        "int",
        "count",
        "Active Distribution Days among today plus the previous 25 sessions (26 sessions).",
    ),
    "ftd_state": (
        "enum",
        "",
        "FTD state machine of this index: NO_SIGNAL, CORRECTION, RALLY_ATTEMPT, FTD_WINDOW, "
        "FTD_CONFIRMED, RALLY_FAILED or FTD_INVALIDATED.",
    ),
    "ftd_date": (
        "date",
        "ISO 8601",
        "Date of the Follow-Through Day currently held by the state machine. Empty if none. "
        "It can differ from the date the FTD was first seen (see ftd_events.csv detected_on).",
    ),
    "rally_day1_date": ("date", "ISO 8601", "Day 1 of the current rally attempt. Empty if none."),
    "swing_low_date": ("date", "ISO 8601", "Swing-low date the rally attempt counts from."),
    "combined_state": (
        "enum",
        "",
        "Combined state of both indices (same values as ftd_state). Identical on both rows "
        "of a date.",
    ),
    "risk_level": (
        "enum",
        "",
        "Distribution Day risk level of this index: NORMAL, CAUTION, HIGH or SEVERE.",
    ),
    "market_below_ma": (
        "bool",
        "",
        "True when the close is below both the 21-day EMA and the 50-day SMA. Empty if either "
        "average cannot be computed.",
    ),
}

FTD_EVENTS: dict[str, Col] = {
    "index": ("str", "", "Index symbol."),
    "rally_day1_date": ("date", "ISO 8601", "Day 1 of the rally attempt that produced the FTD."),
    "swing_low_date": ("date", "ISO 8601", "Swing low the rally attempt counts from."),
    "ftd_date": ("date", "ISO 8601", "Date designated as the Follow-Through Day."),
    "detected_on": (
        "date",
        "ISO 8601",
        "First session of the point-in-time replay on which this FTD was visible. Forward "
        "returns are measured from this date's close.",
    ),
    "lag_sessions": ("int", "sessions", "Sessions between ftd_date and detected_on."),
    "ftd_day_number": ("int", "rally day", "Rally day on which the FTD occurred (4 to 10)."),
    "gain_pct": ("float", PCT_POINTS, "Index gain on the FTD day."),
    "volume_vs_prev_pct": (
        "float",
        PCT_POINTS,
        "FTD-day volume change versus the previous session.",
    ),
    "volume_above_avg": ("bool", "", "FTD-day volume above its 50-day average."),
    "invalidated_at_detection": (
        "bool",
        "",
        "True when the FTD was already invalidated on the day it was first detected.",
    ),
    "invalidation_date": (
        "date",
        "ISO 8601",
        "First close below the FTD-day low within 60 sessions. Empty if none.",
    ),
    "invalidated_within_60": (
        "bool",
        "",
        "True/False, or empty when fewer than 60 sessions have elapsed and no invalidation "
        "has happened yet.",
    ),
}

RALLY_ATTEMPTS: dict[str, Col] = {
    "index": ("str", "", "Index symbol."),
    "rally_day1_date": ("date", "ISO 8601", "Day 1 of the rally attempt."),
    "swing_low_date": ("date", "ISO 8601", "Swing low the attempt counts from."),
    "detected_on": ("date", "ISO 8601", "First replay session on which the attempt was visible."),
    "lag_sessions": ("int", "sessions", "Sessions between rally_day1_date and detected_on."),
}

DISTRIBUTION_DAYS: dict[str, Col] = {
    "index": ("str", "", "Index symbol."),
    "date": ("date", "ISO 8601", "Distribution Day date."),
    "pct_change": ("float", PCT_POINTS, "Close-to-close change of that day (negative)."),
    "volume_change_pct": ("float", PCT_POINTS, "Volume change versus the previous session."),
    "expires_on": (
        "date",
        "ISO 8601",
        "Session on which it leaves the count (26th session after the DD day). Empty for an "
        "active DD whose expiry session is later than the last session of the data; the site "
        "then shows the scheduled date computed from the XNYS calendar.",
    ),
    "invalidated_on": (
        "date",
        "ISO 8601",
        "Date the Distribution Day was invalidated by a 5% rise from its close. Empty if not.",
    ),
    "removal_reason": (
        "enum",
        "",
        "expired_25_sessions or invalidated_5pct_gain. Empty while still active.",
    ),
}

FTD_OUTCOMES: dict[str, Col] = {
    "index": ("str", "", "Index symbol."),
    "ftd_date": ("date", "ISO 8601", "Date designated as the FTD."),
    "detected_on": ("date", "ISO 8601", "First session the FTD was visible."),
    "fwd_5": ("float", PCT_FRACTION, "Return 5 sessions after detected_on (close to close)."),
    "fwd_10": ("float", PCT_FRACTION, "Return 10 sessions after detected_on."),
    "fwd_20": ("float", PCT_FRACTION, "Return 20 sessions after detected_on."),
    "fwd_60": ("float", PCT_FRACTION, "Return 60 sessions after detected_on."),
    "max_dd_60": (
        "float",
        PCT_FRACTION,
        "Largest fall below the detected_on close within the next 60 sessions (negative).",
    ),
    "ref_fwd_20_from_ftd": (
        "float",
        PCT_FRACTION,
        "Reference only: 20-session return measured from the ftd_date close.",
    ),
    "ref_fwd_60_from_ftd": (
        "float",
        PCT_FRACTION,
        "Reference only: 60-session return measured from the ftd_date close.",
    ),
    "invalidated_within_60": ("bool", "", "Invalidated within 60 sessions of the FTD."),
}

_STAT: dict[str, Col] = {
    "horizon": ("int", "sessions", "Forward horizon: 20 or 60 sessions."),
    "basis": (
        "enum",
        "",
        "all_days (every session, overlapping windows), non_overlapping (thinned so windows "
        "do not overlap) or calendar_year_blocks (median/quartiles of per-year medians).",
    ),
    "n": ("int", "count", "Sample size (sessions, or non-overlapping samples)."),
    "n_years": ("int", "years", "Number of calendar-year blocks. Empty for other bases."),
    "median": ("float", PCT_FRACTION, "Median forward return."),
    "p25": ("float", PCT_FRACTION, "25th percentile of forward return."),
    "p75": ("float", PCT_FRACTION, "75th percentile of forward return."),
}

DD_BUCKET_STATS: dict[str, Col] = {
    "index": ("str", "", "Index symbol."),
    "bucket": (
        "enum",
        "",
        "Active Distribution Days in the d25 window (today plus the previous 25 sessions, "
        "26 sessions): 0-3, 4, 5 or 6+.",
    ),
    **_STAT,
}
RISK_LEVEL_STATS: dict[str, Col] = {
    "index": ("str", "", "Index symbol."),
    "risk_level": ("enum", "", "NORMAL, CAUTION, HIGH or SEVERE."),
    **_STAT,
}
DATA_ISSUES: dict[str, Col] = {
    "symbol": ("str", "", "Index symbol."),
    "date": ("date", "ISO 8601", "Session date."),
    "issue": (
        "enum",
        "",
        "missing_volume, zero_volume or volume_equals_previous_day (rows are kept, only flagged).",
    ),
}

_METRIC: dict[str, Col] = {
    "n_events": ("int", "count", "A: FTD events in the period."),
    "n_trades": ("int", "count", "A: trades actually evaluated."),
    "n_dropped_incomplete_window": (
        "int",
        "count",
        "Events dropped because the 60-session forward window leaves the period.",
    ),
    "n_skipped_invalidated_at_entry": (
        "int",
        "count",
        "A: events skipped because the FTD was already invalidated at entry.",
    ),
    "win_rate": ("float", PCT_FRACTION, "A: share of trades with a positive return."),
    "median": ("float", PCT_FRACTION, "A: median return per trade."),
    "mean": ("float", PCT_FRACTION, "A: mean return per trade."),
    "p10": ("float", PCT_FRACTION, "A: 10th percentile of trade returns."),
    "p90": ("float", PCT_FRACTION, "A: 90th percentile of trade returns."),
    "profit_factor": ("float", "ratio", "A: gross profit divided by gross loss."),
    "n_stopped": ("int", "count", "A: trades exited by a close below the FTD-day low."),
    "n_days": ("int", "sessions", "B: sessions in the period."),
    "years": ("float", "years", "B: period length."),
    "cagr": ("float", PCT_FRACTION, "B: compound annual return of the exposure rule."),
    "max_dd": ("float", PCT_FRACTION, "B: maximum drawdown (negative)."),
    "vol": ("float", PCT_FRACTION, "B: annualised volatility of daily returns."),
    "avg_exposure": (
        "float",
        PCT_FRACTION,
        "B: mean daily exposure over the period (1 = fully invested every day). This is an "
        "average exposure, not the fraction of days with a position.",
    ),
    "bh_cagr": ("float", PCT_FRACTION, "B: buy-and-hold compound annual return."),
    "bh_max_dd": ("float", PCT_FRACTION, "B: buy-and-hold maximum drawdown."),
    "bh_vol": ("float", PCT_FRACTION, "B: buy-and-hold volatility."),
    "em_cagr": (
        "float",
        PCT_FRACTION,
        "B: exposure-matched benchmark CAGR (constant exposure equal to avg_exposure, "
        "rest in cash at 0%).",
    ),
    "em_max_dd": ("float", PCT_FRACTION, "B: exposure-matched benchmark maximum drawdown."),
    "n_elevated": ("int", "days", "C: days at or above the reference risk level."),
    "p_elevated": (
        "float",
        PCT_FRACTION,
        "C: share of elevated days followed by a 10% fall within 60 sessions.",
    ),
    "n_normal": ("int", "days", "C: days below the reference risk level."),
    "p_normal": ("float", PCT_FRACTION, "C: same share for the non-elevated days."),
    "spread": ("float", PCT_FRACTION, "C: p_elevated minus p_normal."),
}
for _lv in ("NORMAL", "CAUTION", "HIGH", "SEVERE"):
    _METRIC[f"n_{_lv}"] = ("int", "days", f"C: days with risk level {_lv}.")
    _METRIC[f"p_{_lv}"] = (
        "float",
        PCT_FRACTION,
        f"C: share of {_lv} days followed by a 10% fall within 60 sessions.",
    )

_VARIANT_DIMS: dict[str, Col] = {
    "family": ("enum", "", "ftd or dd."),
    "variant_id": ("str", "", "Variant identifier encoding its parameters."),
    "baseline": ("bool", "", "True for the skill's own definition."),
    "index": ("str", "", "Index symbol."),
    "period": ("enum", "", "design (2000-01-03..2014-12-31) or test (2015-01-02..latest)."),
    "correction_pct": ("float", "percent", "FTD: minimum prior decline (skill default 3)."),
    "min_gain": ("float", "percent", "FTD: minimum FTD-day gain (skill default 1.25)."),
    "require_vol_above_avg": ("bool", "", "FTD: require volume above the 50-day average."),
    "signal_id": ("str", "", "DD: signal definition (variant without the exposure part)."),
    "min_decline_frac": (
        "float",
        PCT_FRACTION,
        "DD: minimum decline as a fraction (skill default -0.002 = -0.2%). Renamed from "
        "min_decline_pct, which was misleading: this is a fraction, unlike correction_pct "
        "and min_gain.",
    ),
    "volume_rule": ("enum", "", "DD: prev_day, avg50 or both."),
    "risk_rule": ("enum", "", "DD: skill_high, skill_severe, pctl20, pctl10 or pctl5."),
    "exposure": ("float", PCT_FRACTION, "DD: exposure while the risk trigger is on."),
    "evaluation": ("enum", "", "A (FTD trades), B (exposure) or C (10% fall frequency)."),
    "role": ("enum", "", "baseline, selected or baseline=selected."),
}


def _research_col(name: str) -> Col:
    if name in _VARIANT_DIMS:
        return _VARIANT_DIMS[name]
    if name[:2] in ("A_", "B_", "C_") and name[2:] in _METRIC:
        t, u, d = _METRIC[name[2:]]
        return (t, u, f"{d} (design period)")
    if name in _METRIC:
        t, u, d = _METRIC[name]
        return (t, u, f"{d} (test period)")
    raise KeyError(name)


_OVERLAY: dict[str, Col] = {
    "variant_id": ("str", "", "R2 variant id (buy_hold = baseline) e.g. overlay_k5_inv1_L1.25."),
    "index": ("str", "", "Index symbol: ^GSPC or ^IXIC."),
    "role": ("enum", "", "baseline (buy and hold) or selected (picked on the design period)."),
    "period": ("enum", "", "design (2000-01-03..2014-12-31) or test (2015-01-02..last session)."),
    "cost_model": (
        "enum",
        "",
        "Borrow-cost case: dff_plus_1.5 (FRED DFF + 1.5% a year, main), fixed_0, fixed_3, fixed_6.",
    ),
    "k": ("int", "count", "Go back to 100% after k Distribution Days counted after the FTD day."),
    "use_invalidation": ("bool", "", "Also go back to 100% on a close below the FTD-day low."),
    "leverage": ("float", "multiple of equity", "Exposure while levered (1.25 or 1.5)."),
    "n_days": ("int", "count", "Return days in the period."),
    "years": ("float", "years", "Return days / 252."),
    "cagr": ("float", PCT_FRACTION, "Annualised return of the variant after borrow cost."),
    "max_dd": ("float", PCT_FRACTION, "Maximum drawdown of the variant (negative)."),
    "vol": ("float", PCT_FRACTION, "Annualised volatility of daily returns."),
    "avg_exposure": ("float", "multiple of equity", "Average exposure over the period."),
    "levered_share": ("float", PCT_FRACTION, "Share of return days with exposure above 100%."),
    "n_episodes": ("int", "count", "Number of separate levered episodes in the period."),
    "worst_levered_dd": (
        "float",
        PCT_FRACTION,
        "Worst fall of equity from the equity at entry/running peak inside a levered episode.",
    ),
    "margin_stress": ("bool", "", "True if any day fell below the placeholder 25% equity ratio."),
    "margin_stress_days": ("int", "count", "Days with equity / position value below 25%."),
    "min_equity_ratio": ("float", PCT_FRACTION, "Lowest equity / position value on levered days."),
    "bh_cagr": ("float", PCT_FRACTION, "Buy and hold 100% annualised return, same days."),
    "bh_max_dd": ("float", PCT_FRACTION, "Buy and hold maximum drawdown, same days."),
    "bh_vol": ("float", PCT_FRACTION, "Buy and hold annualised volatility, same days."),
    "em_cagr": (
        "float",
        PCT_FRACTION,
        "Exposure-matched benchmark: constant average exposure, same borrow cost; CAGR.",
    ),
    "em_max_dd": ("float", PCT_FRACTION, "Exposure-matched benchmark maximum drawdown."),
    "em_vol": ("float", PCT_FRACTION, "Exposure-matched benchmark annualised volatility."),
}


def _overlay_spec(columns: list[str]) -> list[tuple[str, Col]]:
    return [(c, _OVERLAY[c]) for c in columns]


def _spec(columns: list[str], table: dict[str, Col]) -> list[tuple[str, Col]]:
    return [(c, table[c]) for c in columns]


def files() -> dict[str, list[tuple[str, Col]]]:
    """run-relative CSV path -> ordered [(column, (type, unit, description))]."""
    return {
        "data/daily_status.csv": _spec(replay.COLUMNS, DAILY_STATUS),
        "data/ftd_events.csv": _spec(events.FTD_EVENT_COLUMNS, FTD_EVENTS),
        "data/rally_attempts.csv": _spec(events.RALLY_COLUMNS, RALLY_ATTEMPTS),
        "data/distribution_days.csv": _spec(events.DD_COLUMNS, DISTRIBUTION_DAYS),
        "data/ftd_outcomes.csv": _spec(outcomes.FTD_OUTCOME_COLUMNS, FTD_OUTCOMES),
        "data/dd_bucket_stats.csv": _spec(outcomes.DD_STAT_COLUMNS, DD_BUCKET_STATS),
        "data/risk_level_stats.csv": _spec(outcomes.RISK_STAT_COLUMNS, RISK_LEVEL_STATS),
        "data/data_issues.csv": _spec(["symbol", "date", "issue"], DATA_ISSUES),
        "research/variants.csv": [(c, _research_col(c)) for c in outputs.VARIANT_COLUMNS],
        "research/test_results.csv": [(c, _research_col(c)) for c in outputs.TEST_COLUMNS],
        "research/overlay_variants.csv": _overlay_spec(overlay.VARIANT_COLUMNS),
        "research/overlay_test_results.csv": _overlay_spec(overlay.TEST_COLUMNS),
    }


def columns(path: str) -> list[str]:
    return [c for c, _ in files()[path]]


INTRO = """# Data dictionary

Personal local use only. The price-derived data comes from Financial Modeling Prep (FMP)
and must not be redistributed (docs/plan.md section 0).

Conventions

- Files are UTF-8 CSV with a header row. Empty cell = not applicable / not available.
- Dates are ISO 8601 (`YYYY-MM-DD`), XNYS session dates. Booleans are `True` / `False`.
- Percent units differ between files. Columns ending in `_pct` in ftd_events.csv and
  distribution_days.csv are in percent points (1.5 = 1.5%). Returns, drawdowns, rates and
  daily_status.csv changes are fractions (0.015 = 1.5%). The `unit` column below states which.
- Indices: `^GSPC` = S&P 500, `^IXIC` = Nasdaq Composite, price indices without dividends.
- Rules are those of the vendored claude-trading-skills code (see vendor/VENDOR.lock), not
  official IBD signals.
- summary.json holds counts per file (`counts`, `per_index`), the data range, the vendor
  reference and the generation time. selected.json (research) holds the fixed split, the
  variant counts, selection criteria and the baseline/selected variants with design and test
  metrics.
"""


def render_markdown() -> str:
    out = [INTRO]
    for path, cols in files().items():
        out.append(f"\n## `{path}`\n")
        out.append("| column | type | unit | description |")
        out.append("|---|---|---|---|")
        for name, (t, u, d) in cols:
            out.append(f"| `{name}` | {t} | {u} | {d} |")
    return "\n".join(out) + "\n"


_H = re.compile(r"^## `([^`]+)`\s*$")
_R = re.compile(r"^\| `([^`]+)` \|")


def parse_markdown(text: str) -> dict[str, list[str]]:
    """path -> column names, in document order."""
    out: dict[str, list[str]] = {}
    cur = None
    for line in text.splitlines():
        m = _H.match(line)
        if m:
            cur = m.group(1)
            out[cur] = []
            continue
        m = _R.match(line)
        if m and cur is not None:
            out[cur].append(m.group(1))
    return out
