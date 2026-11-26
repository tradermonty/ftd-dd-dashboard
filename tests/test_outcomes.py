"""P3 acceptance: outcome arithmetic, statistics, DD buckets, subsampling, look-ahead."""

from __future__ import annotations

import copy

import pytest

from engine import events, outcomes, replay

SP, NQ = replay.SP500, replay.NASDAQ


def _bars(closes, lows=None):
    return [
        {
            "date": f"d{k:03d}",
            "open": c,
            "high": c,
            "low": (lows[k] if lows else c),
            "close": c,
            "volume": 1,
        }
        for k, c in enumerate(closes)
    ]


def _event(ftd_date, detected_on, sym=SP, **kw):
    e = {"index": sym, "ftd_date": ftd_date, "detected_on": detected_on}
    e.update(kw)
    return e


# ------------------------------------------------------------------ statistics


def test_summarize_basic_quartiles():
    s = outcomes.summarize([1, 2, 3, 4, 5])
    assert s == {"n": 5, "median": 3, "p25": 2, "p75": 4}


def test_summarize_interpolates_even_n():
    s = outcomes.summarize([1, 2, 3, 4])
    assert s["n"] == 4
    assert s["median"] == pytest.approx(2.5)
    assert s["p25"] == pytest.approx(1.75)
    assert s["p75"] == pytest.approx(3.25)


def test_summarize_unsorted_and_skips_none():
    s = outcomes.summarize([5, None, 1, 3, float("nan"), 2, 4])
    assert s == {"n": 5, "median": 3, "p25": 2, "p75": 4}


def test_summarize_empty_and_single():
    assert outcomes.summarize([]) == {"n": 0, "median": None, "p25": None, "p75": None}
    assert outcomes.summarize([7]) == {"n": 1, "median": 7, "p25": 7, "p75": 7}


def test_summarize_has_no_mean():
    assert "mean" not in outcomes.summarize([1, 2, 3])


def test_non_overlapping_takes_every_hth():
    assert outcomes.subsample_non_overlapping(list(range(10)), 3) == [0, 3, 6, 9]
    assert outcomes.subsample_non_overlapping([1, 2], 5) == [1]
    assert outcomes.subsample_non_overlapping([], 5) == []
    with pytest.raises(ValueError):
        outcomes.subsample_non_overlapping([1], 0)


@pytest.mark.parametrize(
    ("d25", "bucket"),
    [(0, "0-3"), (3, "0-3"), (4, "4"), (5, "5"), (6, "6+"), (12, "6+")],
)
def test_bucket_edges(d25, bucket):
    assert outcomes.dd_bucket(d25) == bucket


# --------------------------------------------------------------- FTD outcomes


def test_forward_returns_from_detected_on_close():
    closes = [100.0 + k for k in range(100)]
    h = {SP: _bars(closes), NQ: _bars(closes)}
    ev = [_event("d010", "d012")]
    (o,) = outcomes.ftd_outcomes(ev, h)
    entry = closes[12]
    for k in (5, 10, 20, 60):
        assert o[f"fwd_{k}"] == pytest.approx(closes[12 + k] / entry - 1)
    assert o["ref_fwd_20_from_ftd"] == pytest.approx(closes[30] / closes[10] - 1)
    assert o["ref_fwd_60_from_ftd"] == pytest.approx(closes[70] / closes[10] - 1)
    assert o["max_dd_60"] == 0.0  # monotonic up: close-to-close drawdown is capped at 0


def test_max_drawdown_over_next_60_sessions():
    closes = [100.0] * 100
    closes[20] = 90.0  # 10 sessions after entry at 10
    closes[75] = 50.0  # 65 sessions after entry: outside the 60-session window
    h = {SP: _bars(closes), NQ: _bars(closes)}
    (o,) = outcomes.ftd_outcomes([_event("d008", "d010")], h)
    assert o["max_dd_60"] == pytest.approx(-0.10)


def test_blank_when_not_enough_future():
    closes = [100.0] * 35
    h = {SP: _bars(closes), NQ: _bars(closes)}
    (o,) = outcomes.ftd_outcomes([_event("d008", "d010")], h)
    assert o["fwd_5"] is not None and o["fwd_10"] is not None and o["fwd_20"] is not None
    assert o["fwd_60"] is None and o["max_dd_60"] is None
    assert o["ref_fwd_60_from_ftd"] is None
    assert o["ref_fwd_20_from_ftd"] is not None
    (o2,) = outcomes.ftd_outcomes([_event("d008", "d031")], h)
    assert o2["fwd_5"] is None  # only 4 future bars


def test_uses_same_index_closes():
    a = [100.0 + k for k in range(80)]
    b = [100.0] * 80
    h = {SP: _bars(a), NQ: _bars(b)}
    (o,) = outcomes.ftd_outcomes([_event("d010", "d010", sym=NQ)], h)
    assert o["fwd_5"] == 0.0


def test_fwd_independent_of_bars_after_horizon():
    closes = [100.0 + (k % 7) for k in range(120)]
    h = {SP: _bars(closes), NQ: _bars(closes)}
    ev = [_event("d010", "d012")]
    base = outcomes.ftd_outcomes(ev, h)[0]
    h2 = copy.deepcopy(h)
    for b in h2[SP][12 + 20 + 1 :]:
        b["close"] *= 3
    alt = outcomes.ftd_outcomes(ev, h2)[0]
    for k in (5, 10, 20):
        assert alt[f"fwd_{k}"] == base[f"fwd_{k}"]
    assert alt["fwd_60"] != base["fwd_60"]


def test_fwd_unchanged_by_future_bars_on_real_data(bars):
    h = {SP: bars["ftd_rich"][SP], NQ: bars["ftd_rich"][NQ]}
    rows = replay.replay(h, start=h[SP][100]["date"], keep_raw=True)
    evs = events.ftd_events(rows, h)
    base = outcomes.ftd_outcomes(evs, h)
    cut = 450
    h2 = copy.deepcopy(h)
    for s in (SP, NQ):
        for b in h2[s][cut:]:
            b["close"] *= 0.5
    dates = [b["date"] for b in h[SP]]
    alt = outcomes.ftd_outcomes(evs, h2)
    n = 0
    for a, b in zip(base, alt, strict=True):
        i = dates.index(a["detected_on"])
        for k in (5, 10, 20, 60):
            if i + k < cut:
                assert a[f"fwd_{k}"] == b[f"fwd_{k}"]
                n += 1
    assert n > 0


# ------------------------------------------------------------------ DD buckets


def _status_rows(sym, d25s, closes):
    return [
        {"date": f"d{k:03d}", "index": sym, "close": closes[k], "d25": d}
        for k, d in enumerate(d25s)
    ]


def test_dd_day_forward_returns_and_bucket():
    closes = [100.0 + k for k in range(100)]
    h = {SP: _bars(closes), NQ: _bars(closes)}
    rows = _status_rows(SP, [5] * 100, closes) + _status_rows(NQ, [0] * 100, closes)
    days = outcomes.dd_day_forward(rows, h)
    d = next(x for x in days if x["index"] == SP and x["date"] == "d010")
    assert d["bucket"] == "5"
    assert d["fwd_20"] == pytest.approx(closes[30] / closes[10] - 1)
    assert d["fwd_60"] == pytest.approx(closes[70] / closes[10] - 1)
    last = next(x for x in days if x["index"] == SP and x["date"] == "d099")
    assert last["fwd_20"] is None and last["fwd_60"] is None


def test_bucket_stats_all_and_non_overlapping_per_index():
    n = 200
    closes = [100.0 * (1.001**k) for k in range(n)]
    h = {SP: _bars(closes), NQ: _bars(closes)}
    rows = _status_rows(SP, [0] * n, closes) + _status_rows(NQ, [6] * n, closes)
    days = outcomes.dd_day_forward(rows, h)
    stats = outcomes.dd_bucket_stats(days)
    key = {(s["index"], s["bucket"], s["horizon"], s["basis"]): s for s in stats}
    a = key[(SP, "0-3", 20, "all_days")]
    b = key[(SP, "0-3", 20, "non_overlapping")]
    assert a["n"] == n - 20
    assert b["n"] == -(-(n - 20) // 20)  # every 20th of the valid days
    assert key[(NQ, "6+", 60, "all_days")]["n"] == n - 60
    assert (SP, "6+", 20, "all_days") not in key or key[(SP, "6+", 20, "all_days")]["n"] == 0
    # never pooled: SP and NQ appear in separate rows
    assert {s["index"] for s in stats} == {SP, NQ}
    assert a["median"] == pytest.approx(1.001**20 - 1)


def test_non_overlapping_is_chronological_within_bucket():
    closes = [100.0 + k for k in range(60)]
    h = {SP: _bars(closes), NQ: _bars(closes)}
    # alternating buckets: bucket "4" days are the odd indices
    d25 = [4 if k % 2 else 0 for k in range(60)]
    rows = _status_rows(SP, d25, closes) + _status_rows(NQ, d25, closes)
    days = outcomes.dd_day_forward(rows, h)
    stats = outcomes.dd_bucket_stats(days)
    key = {(s["index"], s["bucket"], s["horizon"], s["basis"]): s for s in stats}
    s4 = key[(SP, "4", 20, "non_overlapping")]
    valid = [k for k in range(60) if k % 2 and k + 20 < 60]
    assert s4["n"] == len(valid[::20])


def _risk_rows(sym, levels, closes):
    return [
        {"date": f"d{k:03d}", "index": sym, "close": closes[k], "d25": 0, "risk_level": lv}
        for k, lv in enumerate(levels)
    ]


def test_risk_level_stats_shape_counts_and_non_overlapping():
    n = 200
    closes = [100.0 * (1.001**k) for k in range(n)]
    h = {SP: _bars(closes), NQ: _bars(closes)}
    # SP alternates NORMAL / HIGH, NQ is always SEVERE
    sp_levels = ["HIGH" if k % 2 else "NORMAL" for k in range(n)]
    rows = _risk_rows(SP, sp_levels, closes) + _risk_rows(NQ, ["SEVERE"] * n, closes)
    days = outcomes.dd_day_forward(rows, h)
    assert all("risk_level" in d for d in days)
    stats = outcomes.risk_level_stats(days)
    assert set(stats[0]) == set(outcomes.RISK_STAT_COLUMNS)
    key = {(s["index"], s["risk_level"], s["horizon"], s["basis"]): s for s in stats}
    # every index x level x horizon x basis row is present (empty ones have n == 0)
    assert len(stats) == 2 * 4 * 2 * 3
    assert key[(SP, "CAUTION", 20, "all_days")]["n"] == 0
    valid = [k for k in range(n) if k % 2 and k + 20 < n]
    assert key[(SP, "HIGH", 20, "all_days")]["n"] == len(valid)
    assert key[(SP, "HIGH", 20, "non_overlapping")]["n"] == len(valid[::20])
    assert key[(NQ, "SEVERE", 60, "all_days")]["n"] == n - 60
    assert key[(NQ, "SEVERE", 20, "all_days")]["median"] == pytest.approx(1.001**20 - 1)
    # never pooled
    assert key[(NQ, "NORMAL", 20, "all_days")]["n"] == 0


def test_risk_level_counts_per_index():
    days = [
        {"index": SP, "risk_level": "NORMAL"},
        {"index": SP, "risk_level": "SEVERE"},
        {"index": SP, "risk_level": "SEVERE"},
        {"index": NQ, "risk_level": "HIGH"},
    ]
    c = outcomes.risk_level_counts(days)
    assert c[SP] == {"NORMAL": 1, "CAUTION": 0, "HIGH": 0, "SEVERE": 2}
    assert c[NQ] == {"NORMAL": 0, "CAUTION": 0, "HIGH": 1, "SEVERE": 0}


def test_risk_forward_unchanged_by_future_bars():
    closes = [100.0 + k for k in range(120)]
    h1 = {SP: _bars(closes), NQ: _bars(closes)}
    rows = _risk_rows(SP, ["HIGH"] * 120, closes) + _risk_rows(NQ, ["HIGH"] * 120, closes)
    base = {(d["index"], d["date"]): d for d in outcomes.dd_day_forward(rows, h1)}
    c2 = list(closes)
    for k in range(80, 120):
        c2[k] *= 0.5
    h2 = {SP: _bars(c2), NQ: _bars(c2)}
    rows2 = _risk_rows(SP, ["HIGH"] * 120, c2) + _risk_rows(NQ, ["HIGH"] * 120, c2)
    alt = {(d["index"], d["date"]): d for d in outcomes.dd_day_forward(rows2, h2)}
    # day 10: both horizons (20 -> bar 30, 60 -> bar 70) look only at bars < 80
    assert base[(SP, "d010")] == alt[(SP, "d010")]


def test_ftd_outcome_stats_per_index_and_horizon():
    rows = [
        {"index": SP, "fwd_20": 0.1, "fwd_60": 0.2, "max_dd_60": -0.05, "invalidated_within_60": 1},
        {"index": SP, "fwd_20": 0.3, "fwd_60": None, "max_dd_60": None, "invalidated_within_60": 0},
        {"index": NQ, "fwd_20": -0.1, "fwd_60": 0.0, "max_dd_60": -0.2, "invalidated_within_60": 0},
    ]
    s = outcomes.ftd_outcome_stats(rows)
    assert s[SP]["fwd_20"] == {
        "n": 2,
        "median": pytest.approx(0.2),
        "p25": pytest.approx(0.15),
        "p75": pytest.approx(0.25),
    }
    assert s[SP]["fwd_60"]["n"] == 1
    assert s[NQ]["fwd_20"]["n"] == 1


def test_max_dd_60_never_positive():
    closes = [100.0 + k for k in range(100)]
    h = {SP: _bars(closes), NQ: _bars(closes)}
    (o,) = outcomes.ftd_outcomes([_event("d008", "d010")], h)
    assert o["max_dd_60"] == 0.0
    assert "close-to-close" in outcomes.ftd_outcomes.__doc__


def _dated_rows(sym, dates, field, level, vals):
    return [
        {"index": sym, "date": d, field: level, "fwd_20": f, "fwd_60": f}
        for d, f in zip(dates, vals, strict=True)
    ]


def _stat(stats, field, sym, cat, h, basis):
    (row,) = [
        s for s in stats if (s["index"], s[field], s["horizon"], s["basis"]) == (sym, cat, h, basis)
    ]
    return row


def test_calendar_year_blocks_median_of_yearly_medians():
    # 2020: 1,2,3 -> 2 ; 2021: 10 -> 10 ; 2022: 4,6 -> 5 ; 2023: only None -> excluded
    dates = [
        "2020-01-02",
        "2020-02-03",
        "2020-03-02",
        "2021-05-03",
        "2022-01-03",
        "2022-02-01",
        "2023-01-03",
    ]
    vals = [1.0, 2.0, 3.0, 10.0, 4.0, 6.0, None]
    stats = outcomes.dd_bucket_stats(_dated_rows(SP, dates, "bucket", "5", vals))
    b = _stat(stats, "bucket", SP, "5", 20, "calendar_year_blocks")
    assert b["n_years"] == 3
    assert b["median"] == pytest.approx(5.0)  # median of [2, 5, 10]
    assert b["p25"] == pytest.approx(3.5)
    assert b["p75"] == pytest.approx(7.5)
    assert _stat(stats, "bucket", SP, "5", 20, "all_days")["n_years"] is None


def test_calendar_year_blocks_in_risk_level_stats_and_empty():
    days = _dated_rows(NQ, ["2020-01-02", "2021-01-04"], "risk_level", "HIGH", [0.1, 0.3])
    stats = outcomes.risk_level_stats(days)
    h = _stat(stats, "risk_level", NQ, "HIGH", 60, "calendar_year_blocks")
    assert h["n_years"] == 2 and h["median"] == pytest.approx(0.2)
    e = _stat(stats, "risk_level", NQ, "SEVERE", 60, "calendar_year_blocks")
    assert e["n_years"] == 0 and e["median"] is None
    assert set(stats[0]) == set(outcomes.RISK_STAT_COLUMNS)
