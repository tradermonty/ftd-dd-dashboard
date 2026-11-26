"""Numbers shown on the site, all derived from the run directory or the vendored code.

Nothing here is typed in by hand: statements in the page text are built from these values.
"""

from __future__ import annotations

import csv
import json
import re
from dataclasses import dataclass, field
from pathlib import Path

from engine import dd_glue, events, fetch, loader, replay
from research import evaluate, selection, variants
from research.split import DESIGN_END, DESIGN_START, HORIZON, TEST_END, TEST_START

INDEX_ORDER = ("^GSPC", "^IXIC")
RISK_LEVELS = ("NORMAL", "CAUTION", "HIGH", "SEVERE")
BUCKETS = ("0-3", "4", "5", "6+")


def read_csv(path: Path) -> list[dict]:
    with path.open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def fnum(x) -> float | None:
    if x is None or x == "":
        return None
    return float(x)


def truthy(x) -> bool | None:
    if x in ("True", True):
        return True
    if x in ("False", False):
        return False
    return None


@dataclass
class Facts:
    run: Path
    summary: dict
    selected: dict
    status: list[dict]
    ftd: list[dict]
    dd: list[dict]
    outcomes: list[dict]
    bucket: list[dict]
    risk: list[dict]
    issues: list[dict]
    variants_rows: list[dict] = field(default_factory=list)
    test_rows: list[dict] = field(default_factory=list)
    overlay: dict | None = None  # research R2 (overlay_selected.json); absent in older runs

    @property
    def last_date(self) -> str:
        return self.summary["data_range"]["last"]

    @property
    def first_date(self) -> str:
        return self.summary["data_range"]["replay_first"]

    @property
    def indices(self) -> list[str]:
        have = {r["index"] for r in self.status[-4:]}
        return [i for i in INDEX_ORDER if i in have]


def rename_legacy_keys(obj):
    """Runs made before the rename read the key `time_invested` as `avg_exposure`."""
    if isinstance(obj, dict):
        return {
            ("avg_exposure" if k == "time_invested" else k): rename_legacy_keys(v)
            for k, v in obj.items()
        }
    if isinstance(obj, list):
        return [rename_legacy_keys(v) for v in obj]
    return obj


def load_facts(run: Path) -> Facts:
    run = Path(run)
    d, r = run / "data", run / "research"
    return Facts(
        run=run,
        summary=json.loads((d / "summary.json").read_text()),
        selected=rename_legacy_keys(json.loads((r / "selected.json").read_text())),
        status=read_csv(d / "daily_status.csv"),
        ftd=read_csv(d / "ftd_events.csv"),
        dd=read_csv(d / "distribution_days.csv"),
        outcomes=read_csv(d / "ftd_outcomes.csv"),
        bucket=read_csv(d / "dd_bucket_stats.csv"),
        risk=read_csv(d / "risk_level_stats.csv"),
        issues=read_csv(d / "data_issues.csv"),
        variants_rows=read_csv(r / "variants.csv") if (r / "variants.csv").exists() else [],
        test_rows=read_csv(r / "test_results.csv") if (r / "test_results.csv").exists() else [],
        overlay=(
            json.loads((r / "overlay_selected.json").read_text())
            if (r / "overlay_selected.json").exists()
            else None
        ),
    )


# ----------------------------------------------------------------------------- today's state


def index_states(f: Facts) -> list[dict]:
    """Latest row per index."""
    out = []
    for sym in f.indices:
        rows = [r for r in f.status if r["index"] == sym]
        last = rows[-1]
        out.append(
            {
                "index": sym,
                "date": last["date"],
                "close": float(last["close"]),
                "pct_change": fnum(last["pct_change"]),
                "ftd_state": last["ftd_state"],
                "ftd_date": last["ftd_date"],
                "rally_day1_date": last["rally_day1_date"],
                "d5": int(last["d5"]),
                "d15": int(last["d15"]),
                "d25": int(last["d25"]),
                "risk": last["risk_level"],
                "is_dd": last["is_dd"] == "True",
            }
        )
    return out


# ------------------------------------------------------------------------------- definitions


def _literal(path: Path, pattern: str) -> int:
    m = re.search(pattern, path.read_text(encoding="utf-8"))
    if not m:
        raise RuntimeError(f"pattern {pattern!r} not found in {path.name}")
    return int(m.group(1))


def definition_values() -> dict:
    """Thresholds exactly as implemented by the vendored code (module constants / dataclasses);
    two window lengths are plain literals in the source and are read from the source text."""
    rt = loader.load_ftd_modules().rally_tracker
    rule = dd_glue.build_rule()
    th = dd_glue.build_thresholds()
    src = loader.FTD_DIR / "rally_tracker.py"
    return {
        "min_correction_pct": rt.MIN_CORRECTION_PCT,
        "min_down_days": rt.MIN_DOWN_DAYS,
        "ftd_day_start": rt.FTD_DAY_START,
        "ftd_day_end": rt.FTD_DAY_END,
        "gain_min": rt.FTD_GAIN_MINIMUM,
        "gain_rec": rt.FTD_GAIN_RECOMMENDED,
        "gain_strong": rt.FTD_GAIN_STRONG,
        "analysis_bars": _literal(src, r"lookback = min\((\d+), len\(history\)\)"),
        "swing_lookback": _literal(src, r"search_start = max\(0, i - (\d+)\)"),
        "ftd_fetch_bars": replay.FTD_WINDOW,
        "invalidation_horizon": events.INVALIDATION_HORIZON,
        "dd_decline_pct": rule.min_decline_pct * 100,
        "dd_expiration": rule.expiration_sessions,
        "dd_invalidation_gain_pct": rule.invalidation_gain_pct * 100,
        "dd_invalidation_source": rule.invalidation_price_source,
        "dd_window_bars": replay.DD_WINDOW,
        "caution_d25": th.caution_d25,
        "high_d25": th.high_d25,
        "high_d15": th.high_d15,
        "high_d5": th.high_d5,
        "severe_d25": th.severe_d25,
        "severe_d15": th.severe_d15,
        "severe_ma_d25": th.severe_ma_d25,
        "ema_period": dd_glue.DEFAULT_MA_CONFIG["ema_periods"][0],
        "sma_period": dd_glue.DEFAULT_MA_CONFIG["sma_periods"][0],
    }


D25_WINDOW = 26  # d25 counts age 0..25 = today plus the previous 25 sessions (vendor docstring)


def base_rates(f: Facts) -> list[dict]:
    """Share of sessions that are DDs and what that implies for the count in a d25 window
    (today plus the previous 25 sessions = 26 sessions)."""
    th = dd_glue.build_thresholds()
    out = []
    for sym in f.indices:
        rows = [r for r in f.status if r["index"] == sym]
        n = len(rows)
        dd_days = sum(1 for r in rows if r["is_dd"] == "True")
        share = dd_days / n
        d25 = [int(r["d25"]) for r in rows]
        levels = {lv: sum(1 for r in rows if r["risk_level"] == lv) / n for lv in RISK_LEVELS}
        severe = [r for r in rows if r["risk_level"] == "SEVERE"]
        not_d25 = sum(1 for r in severe if int(r["d25"]) < th.severe_d25)
        out.append(
            {
                "index": sym,
                "sessions": n,
                "dd_days": dd_days,
                "dd_share": share,
                "window": D25_WINDOW,
                "expected_window": D25_WINDOW * share,
                "mean_d25": sum(d25) / n,
                "share_d25_severe": sum(1 for v in d25 if v >= th.severe_d25) / n,
                "severe_d25": th.severe_d25,
                "severe_d15": th.severe_d15,
                "severe_ma_d25": th.severe_ma_d25,
                "levels": levels,
                "severe_days": len(severe),
                "severe_not_d25": not_d25,
                "share_severe_not_d25": (not_d25 / len(severe)) if severe else None,
            }
        )
    return out


def ftd_overlap(
    f: Facts, horizon: int = HORIZON, test_start: str = TEST_START, test_end: str = TEST_END
) -> list[dict]:
    """Per index: FTDs detected in the test period, and how many of them have another FTD of
    the same index detected within `horizon` sessions (before or after). Such trades overlap in
    time and could not all be held with one unit of capital."""
    out = []
    for sym in f.indices:
        dates = [r["date"] for r in f.status if r["index"] == sym]
        pos = {d: i for i, d in enumerate(dates)}
        det = sorted(
            pos[r["detected_on"]] for r in f.ftd if r["index"] == sym and r["detected_on"] in pos
        )
        mine = [p for p in det if test_start <= dates[p] <= test_end]
        # `det` holds every FTD of the index, p itself included; > 1 means a neighbour exists
        k = sum(1 for p in mine if sum(1 for q in det if abs(q - p) <= horizon) > 1)
        out.append({"index": sym, "n": len(mine), "k": k, "h": horizon})
    return out


def same_volume_count(f: Facts) -> int:
    """Data issues of the 'same volume as the previous day' type only."""
    return sum(1 for r in f.issues if r.get("issue") == "volume_equals_previous_day")


def scheduled_expiry(date_str: str) -> str:
    """Session on which a DD of `date_str` leaves the count (26th session after it, XNYS)."""
    cal = fetch.get_xnys()
    return cal.session_offset(date_str, events.DD_EXPIRY_SESSIONS).strftime("%Y-%m-%d")


def b_constraint_violations(f: Facts) -> list[dict]:
    """B: selected variants (not the baseline) whose TEST-period CAGR is more than the selection
    tolerance below buy & hold, i.e. that broke their own selection constraint out of sample."""
    out = []
    tol = selection.CAGR_TOLERANCE_B
    for idx, info in evaluation(f, "B").items():
        if info["selected"] == info["baseline"]:
            continue
        r = info["test"]["selected"]
        if r["cagr"] is not None and r["bh_cagr"] is not None and r["cagr"] < r["bh_cagr"] - tol:
            out.append(
                {
                    "index": idx,
                    "id": info["selected"],
                    "cagr": r["cagr"],
                    "bh_cagr": r["bh_cagr"],
                    "tolerance": tol,
                }
            )
    return out


# ---------------------------------------------------------------------------------- research


def research_constants() -> dict:
    return {
        "design": (DESIGN_START, DESIGN_END),
        "test_start": TEST_START,
        "test_end": TEST_END,
        "horizon": HORIZON,
        "dd_threshold": -evaluate.DD_THRESHOLD,
        "min_trades_a": selection.MIN_TRADES_A,
        "cagr_tol_b": selection.CAGR_TOLERANCE_B,
        "min_days_c": selection.MIN_DAYS_C,
        "counts": variants.count_summary(),
        "corrections": variants.CORRECTION_PCTS,
        "gains": variants.MIN_GAINS,
        "declines": variants.DECLINES,
        "volume_rules": variants.VOLUME_RULES,
        "risk_rules": variants.RISK_RULES,
        "exposures": variants.EXPOSURES,
    }


def evaluation(f: Facts, ev: str) -> dict[str, dict]:
    """selected.json evaluation block for `ev` ordered by index."""
    block = f.selected["evaluations"][ev]
    return {i: block[i] for i in INDEX_ORDER if i in block}


def metric_key(ev: str) -> str:
    """Selection metric per evaluation and whether higher is better."""
    return {"A": "median", "B": "max_dd", "C": "spread"}[ev]


def selected_beats_baseline(f: Facts) -> list[dict]:
    """Per (evaluation, index): did the design-selected variant beat the baseline on the TEST
    period, on the metric it was selected for? Equal ids are reported as `same`."""
    out = []
    for ev in "ABC":
        for idx, info in evaluation(f, ev).items():
            same = info["baseline"] == info["selected"]
            m = metric_key(ev)
            b = info["test"]["baseline"].get(m)
            s = info["test"]["selected"].get(m)
            out.append(
                {
                    "evaluation": ev,
                    "index": idx,
                    "same": same,
                    "metric": m,
                    "baseline": b,
                    "selected": s,
                    "better": (s is not None and b is not None and s > b),
                }
            )
    return out


def tie_tally(f: Facts) -> dict[str, dict]:
    """The picked-beats-baseline tally with the picks now in force ("new") and with the picks the
    old string-order tie rule made ("old"). The old picks differ only where
    selected.json["tie_break_comparison"] lists them; their test results come from there."""
    comp = {(x["evaluation"], x["index"]): x for x in f.selected.get("tie_break_comparison", [])}
    out = {}
    for which in ("new", "old"):
        n = better = 0
        for ev in "ABC":
            for idx, info in evaluation(f, ev).items():
                sel_id, sel_test = info["selected"], info["test"]["selected"]
                if which == "old" and (ev, idx) in comp:
                    sel_id = comp[(ev, idx)]["before"]["id"]
                    sel_test = comp[(ev, idx)]["before"]["test"]
                if sel_id == info["baseline"]:
                    continue
                m = metric_key(ev)
                b, s = info["test"]["baseline"].get(m), sel_test.get(m)
                n += 1
                better += int(s is not None and b is not None and s > b)
        out[which] = {"n": n, "better": better}
    return out


def stats_rows(rows: list[dict], key: str) -> dict[tuple[str, str, str, str], dict]:
    """(index, bucket-or-level, horizon, basis) -> row with floats."""
    out = {}
    for r in rows:
        out[(r["index"], r[key], r["horizon"], r["basis"])] = {
            "n": int(r["n"]),
            "n_years": int(r["n_years"]) if r["n_years"] else None,
            "median": fnum(r["median"]),
            "p25": fnum(r["p25"]),
            "p75": fnum(r["p75"]),
        }
    return out
