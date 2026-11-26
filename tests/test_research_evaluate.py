"""Hand-computed arithmetic for evaluations A, B, C on tiny synthetic series. No network."""

from __future__ import annotations

import math

import pytest

from research import evaluate


def _bars(closes, lows=None):
    out = []
    for i, c in enumerate(closes):
        out.append(
            {
                "date": f"2020-01-{i + 1:02d}" if i < 28 else f"2020-02-{i - 27:02d}",
                "open": c,
                "high": c,
                "low": c if lows is None else lows[i],
                "close": c,
                "volume": 1,
            }
        )
    return out


def _bars_many(closes, lows):
    """Dates as increasing ISO strings for long series."""
    from datetime import date, timedelta

    d0 = date(2020, 1, 1)
    return [
        {
            "date": (d0 + timedelta(days=i)).isoformat(),
            "open": c,
            "high": c,
            "low": lw,
            "close": c,
            "volume": 1,
        }
        for i, (c, lw) in enumerate(zip(closes, lows, strict=True))
    ]


# ------------------------------------------------------------------ A


def test_trade_stop_exit_and_horizon_exit():
    n = 40
    closes = [100.0] * n
    lows = [99.0] * n
    # FTD day idx 3 (low 98). Close below low at idx 9 -> stop at 97.
    lows[3] = 98.0
    closes[9] = 97.0
    bars = _bars_many(closes, lows)
    ev = {"ftd_date": bars[3]["date"], "detected_on": bars[3]["date"]}
    trades, meta = evaluate.trade_returns([ev], bars, bars[0]["date"], bars[-1]["date"], horizon=10)
    assert len(trades) == 1
    t = trades[0]
    assert t["exit_reason"] == "stop" and t["exit_date"] == bars[9]["date"]
    assert t["ret"] == pytest.approx(97 / 100 - 1)
    assert meta["n_dropped_incomplete_window"] == 0

    # no stop: exit after `horizon` sessions at that close
    closes2 = [100.0] * n
    closes2[13] = 110.0
    bars2 = _bars_many(closes2, lows)
    ev2 = {"ftd_date": bars2[3]["date"], "detected_on": bars2[3]["date"]}
    tr2, _ = evaluate.trade_returns([ev2], bars2, bars2[0]["date"], bars2[-1]["date"], horizon=10)
    assert tr2[0]["exit_reason"] == "horizon" and tr2[0]["exit_date"] == bars2[13]["date"]
    assert tr2[0]["ret"] == pytest.approx(0.10)


def test_trade_close_equal_to_low_is_not_a_stop():
    closes = [100.0] * 20
    lows = [99.0] * 20
    lows[2] = 98.0
    closes[5] = 98.0  # equals the low -> not "below" -> no stop (check_ftd_invalidation: <)
    bars = _bars_many(closes, lows)
    ev = {"ftd_date": bars[2]["date"], "detected_on": bars[2]["date"]}
    trades, _ = evaluate.trade_returns([ev], bars, bars[0]["date"], bars[-1]["date"], horizon=8)
    assert trades[0]["exit_reason"] == "horizon"


def test_trade_dropped_skipped_and_window_limits():
    closes = [100.0] * 30
    lows = [99.0] * 30
    lows[2] = 98.0
    closes[3] = 97.0  # already below the FTD low before detection (lag 2)
    bars = _bars_many(closes, lows)
    skipped = {"ftd_date": bars[2]["date"], "detected_on": bars[4]["date"]}
    late = {"ftd_date": bars[24]["date"], "detected_on": bars[24]["date"]}  # 24+10 > 29
    ok = {"ftd_date": bars[10]["date"], "detected_on": bars[10]["date"]}
    trades, meta = evaluate.trade_returns(
        [skipped, late, ok], bars, bars[0]["date"], bars[-1]["date"], horizon=10
    )
    assert [t["entry_date"] for t in trades] == [bars[10]["date"]]
    assert meta["n_skipped_invalidated_at_entry"] == 1
    assert meta["n_dropped_incomplete_window"] == 1
    assert meta["n_events"] == 3
    # `end` hides later bars: ok's window (10+10=20) no longer fits when end is idx 15
    t2, m2 = evaluate.trade_returns([ok], bars, bars[0]["date"], bars[15]["date"], horizon=10)
    assert t2 == [] and m2["n_dropped_incomplete_window"] == 1
    # events outside [start, end] are ignored
    t3, m3 = evaluate.trade_returns([ok], bars, bars[11]["date"], bars[-1]["date"], horizon=10)
    assert t3 == [] and m3["n_events"] == 0


def test_trade_stats():
    trades = [
        {"ret": r, "exit_reason": "stop" if r < 0 else "horizon"}
        for r in (0.10, -0.05, 0.02, -0.01, 0.03)
    ]
    s = evaluate.trade_stats(trades)
    assert s["n_trades"] == 5
    assert s["win_rate"] == pytest.approx(3 / 5)
    assert s["median"] == pytest.approx(0.02)
    assert s["mean"] == pytest.approx(0.09 / 5)
    assert s["profit_factor"] == pytest.approx(0.15 / 0.06)
    assert s["n_stopped"] == 2
    # numpy-style linear interpolation: sorted [-.05,-.01,.02,.03,.10]; p10 at pos 0.4
    assert s["p10"] == pytest.approx(-0.05 + 0.04 * 0.4)
    assert s["p90"] == pytest.approx(0.03 + 0.07 * 0.6)
    assert evaluate.trade_stats([])["n_trades"] == 0
    assert evaluate.trade_stats([{"ret": 0.1, "exit_reason": "horizon"}])["profit_factor"] is None


# ------------------------------------------------------------------ B


def test_exposure_backtest_arithmetic():
    dates = ["d0", "d1", "d2", "d3"]
    closes = [100.0, 110.0, 99.0, 108.9]
    flags = [False, True, False, False]
    m = evaluate.exposure_backtest(dates, closes, flags, 0.5, "d0", "d3")
    # returns .1 (flag d0 off), -.1 * 0.5 (flag d1 on), .1 (flag d2 off)
    eq = 1.1 * 0.95 * 1.1
    years = 3 / 252
    assert m["n_days"] == 3
    assert m["cagr"] == pytest.approx(eq ** (1 / years) - 1)
    assert m["max_dd"] == pytest.approx(1.045 / 1.1 - 1)
    std = math.sqrt(((0.05) ** 2 + (0.10) ** 2 + (0.05) ** 2) / 2)
    assert m["vol"] == pytest.approx(std * math.sqrt(252))
    assert m["avg_exposure"] == pytest.approx((1 + 0.5 + 1) / 3)
    assert m["bh_cagr"] == pytest.approx((1.1 * 0.9 * 1.1) ** (1 / years) - 1)
    assert m["bh_max_dd"] == pytest.approx(0.99 / 1.1 - 1)


def test_exposure_zero_is_cash_and_window():
    dates = ["d0", "d1", "d2", "d3"]
    closes = [100.0, 90.0, 99.0, 99.0]
    flags = [True, True, True, True]
    m = evaluate.exposure_backtest(dates, closes, flags, 0.0, "d0", "d3")
    assert m["cagr"] == pytest.approx(0.0) and m["max_dd"] == 0.0 and m["avg_exposure"] == 0.0
    # window: signal day >= start and return day <= end -> only k=2 when start=d1, end=d2
    w = evaluate.exposure_backtest(dates, closes, [False] * 4, 1.0, "d1", "d2")
    assert w["n_days"] == 1
    assert w["bh_cagr"] == pytest.approx((99 / 90) ** 252 - 1)
    # a later bar cannot change an earlier window (bars after end are ignored)
    closes2 = [*closes[:3], 1e9]
    w2 = evaluate.exposure_backtest(dates, closes2, [False] * 4, 1.0, "d1", "d2")
    assert w2 == w


def test_exposure_empty_window():
    m = evaluate.exposure_backtest(["a", "b"], [1.0, 2.0], [False, False], 0.5, "x", "y")
    assert m["n_days"] == 0 and m["cagr"] is None and m["avg_exposure"] is None


# ------------------------------------------------------------------ C


def test_drawdown_prob_arithmetic_and_dropping():
    dates = [f"d{i}" for i in range(6)]
    closes = [100.0, 85.0, 100.0, 100.0, 100.0, 100.0]
    labels = ["a", "b", "a", "b", "a", "b"]
    r = evaluate.drawdown_prob(dates, closes, labels, "d0", "d5", horizon=2)
    # t=0 hit (85 <= 90); t=1: min(100,100) no; t=2 no; t=3 no; t=4,5 dropped
    assert r["by_label"]["a"] == {"n": 2, "hits": 1, "p": 0.5}
    assert r["by_label"]["b"] == {"n": 2, "hits": 0, "p": 0.0}
    assert r["n_dropped_incomplete_window"] == 2
    # shorter period end drops more, and later bars never matter
    closes2 = [*closes[:4], 1.0, 1.0]
    r2 = evaluate.drawdown_prob(dates, closes2, labels, "d0", "d3", horizon=2)
    assert r2["by_label"]["a"]["n"] + r2["by_label"]["b"]["n"] == 2
    assert r2["n_dropped_incomplete_window"] == 2
    # exactly -10% counts as a hit
    r3 = evaluate.drawdown_prob(["d0", "d1", "d2"], [100.0, 90.0, 95.0], ["x"] * 3, "d0", "d2", 2)
    assert r3["by_label"]["x"]["hits"] == 1


def test_exposure_matched_benchmark_arithmetic():
    """Constant daily-rebalanced exposure = the strategy's average exposure, cash 0."""
    dates = ["d0", "d1", "d2", "d3"]
    closes = [100.0, 110.0, 99.0, 108.9]
    flags = [False, True, False, False]
    m = evaluate.exposure_backtest(dates, closes, flags, 0.5, "d0", "d3")
    x = (1 + 0.5 + 1) / 3
    assert m["avg_exposure"] == pytest.approx(x)
    eq = (1 + x * 0.1) * (1 - x * 0.1) * (1 + x * 0.1)
    years = 3 / 252
    assert m["em_cagr"] == pytest.approx(eq ** (1 / years) - 1)
    peak = 1 + x * 0.1
    assert m["em_max_dd"] == pytest.approx((1 + x * 0.1) * (1 - x * 0.1) / peak - 1)
    # at exposure 1.0 everywhere the benchmark is buy & hold
    full = evaluate.exposure_backtest(dates, closes, [False] * 4, 1.0, "d0", "d3")
    assert full["em_cagr"] == pytest.approx(full["bh_cagr"])
    assert full["em_max_dd"] == pytest.approx(full["bh_max_dd"])


def test_exposure_matched_benchmark_empty_window():
    m = evaluate.exposure_backtest(["a", "b"], [1.0, 2.0], [False, False], 0.5, "x", "y")
    assert m["em_cagr"] is None and m["em_max_dd"] is None
