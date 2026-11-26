"""Baseline == P3 build outputs on the REAL cached history. Skipped without data/ or build/.

No network: reads data/raw cache only (cache miss -> skip) and the P3 CSVs in build/.
"""

from __future__ import annotations

import csv
from datetime import date
from pathlib import Path

import pytest

from engine import fetch
from research import dd_signals, ftd_signals
from research.split import DESIGN_START

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"


def _no_network(url, params):
    raise fetch.FetchError("cache miss")


@pytest.fixture(scope="module")
def real():
    if not (BUILD / "daily_status.csv").exists() or not (BUILD / "ftd_events.csv").exists():
        pytest.skip("P3 build outputs not present")
    try:
        end = date.fromisoformat(max(r["date"] for r in _read(BUILD / "daily_status.csv")))
        return {
            s: fetch.fetch_index(
                s, end=end, use_cache=True, http_get=_no_network, api_key="unused"
            ).rows
            for s in fetch.DEFAULT_SYMBOLS
        }
    except fetch.FetchError:
        pytest.skip("raw cache not present")


def _read(path):
    with path.open(newline="") as f:
        return list(csv.DictReader(f))


def test_baseline_ftd_events_equal_p3_csv(real):
    got = ftd_signals.ftd_event_table(real, 3.0, 1.25, DESIGN_START)
    exp = _read(BUILD / "ftd_events.csv")
    assert len(got) == len(exp) > 50
    key = lambda r: (r["index"], r["ftd_date"], r["detected_on"])  # noqa: E731
    assert sorted(key(r) for r in got) == sorted(key(r) for r in exp)


@pytest.mark.parametrize("sym", fetch.DEFAULT_SYMBOLS)
def test_baseline_dd_and_risk_equal_p3_csv(real, sym):
    s = dd_signals.dd_series(real[sym], -0.002, "prev_day")
    exp = {r["date"]: r for r in _read(BUILD / "daily_status.csv") if r["index"] == sym}
    n = 0
    for t, d in enumerate(s.dates):
        if d not in exp:
            continue
        r = exp[d]
        assert (s.d5[t], s.d15[t], s.d25[t]) == (int(r["d5"]), int(r["d15"]), int(r["d25"])), d
        assert s.is_dd[t] == (r["is_dd"] == "True"), d
        assert s.level[t] == r["risk_level"], d
        n += 1
    assert n > 6000


@pytest.mark.parametrize("sym", fetch.DEFAULT_SYMBOLS)
def test_overlay_real_data_entries_match_p3_events_and_unlevered_is_buy_and_hold(real, sym):
    from research import overlay

    bars = real[sym]
    dates = [b["date"] for b in bars]
    closes = [b["close"] for b in bars]
    lows = [b["low"] for b in bars]
    s = dd_signals.dd_series(bars, -0.002, "prev_day")
    assert s.dates == dates
    ev = [r for r in _read(BUILD / "ftd_events.csv") if r["index"] == sym]
    exp, eps = overlay.decide_exposure(dates, closes, lows, s.is_dd, ev, 5, True, 1.5)
    detected = {r["detected_on"] for r in ev}
    assert eps and all(e["entry"] in detected for e in eps)
    assert min(exp) == 1.0 and max(exp) == 1.5
    flat = [1.0] * len(dates)
    m = overlay.evaluate(dates, closes, flat, [0.05] * len(dates), DESIGN_START, dates[-1])
    assert m["cagr"] == m["bh_cagr"] and m["levered_share"] == 0
