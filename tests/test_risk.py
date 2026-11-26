"""§3.4.1 acceptance: risk_level / market_below_ma equal the ORIGINAL ibd_monitor. No network."""

from __future__ import annotations

import copy

import yaml

from engine import dd_glue, loader, replay

SP, NQ = replay.SP500, replay.NASDAQ
MIN_I = 100


def _hist(bars, seg):
    return {SP: bars[seg][SP], NQ: bars[seg][NQ]}


def test_dd_glue_risk_equals_original_analyze_index(bars, original_skills):
    ibd = original_skills.ibd_monitor
    config = ibd.load_config(None)
    rule = ibd._build_rule(config)
    thresholds = ibd._build_thresholds(config)
    levels: set[str] = set()
    ma_path_severe = 0
    below_values: set = set()
    checked = 0
    for seg in ("ftd_rich", "latest"):
        for sym in (SP, NQ):
            chrono = bars[seg][sym]
            # the replay's own 85-bar window, every day, plus short windows at the start
            for i in range(30, len(chrono)):
                window = chrono[max(0, i - replay.DD_WINDOW + 1) : i + 1]
                hist = list(reversed(window))
                res, _ = ibd.analyze_index(sym, sym, hist, config, rule, thresholds, None)
                mine = dd_glue.analyze(hist)
                orig_below = res.trend_filters["market_below_21ema_or_50ma"]
                assert mine.market_below_ma == orig_below, (seg, sym, i)
                assert mine.risk_level == res.risk_level, (seg, sym, i)
                levels.add(mine.risk_level)
                below_values.add(mine.market_below_ma)
                if mine.risk_level == "SEVERE" and mine.d25 < 6 and mine.d15 < 4:
                    ma_path_severe += 1
                checked += 1
    assert checked > 1000
    # the fixtures must exercise every level, the MA escalation path and both MA states
    assert levels == {"NORMAL", "CAUTION", "HIGH", "SEVERE"}
    assert ma_path_severe > 0
    assert {True, False} <= below_values


def test_replay_rows_carry_original_risk(bars, original_skills):
    ibd = original_skills.ibd_monitor
    config = ibd.load_config(None)
    rule = ibd._build_rule(config)
    thresholds = ibd._build_thresholds(config)
    h = _hist(bars, "ftd_rich")
    rows = replay.replay(h, start=h[SP][MIN_I]["date"])
    assert {"risk_level", "market_below_ma"} <= set(rows[0])
    for r in rows[::5]:
        chrono = h[r["index"]]
        i = next(k for k, b in enumerate(chrono) if b["date"] == r["date"])
        hist85 = list(reversed(chrono[i - 84 : i + 1]))
        res, _ = ibd.analyze_index(r["index"], r["index"], hist85, config, rule, thresholds, None)
        assert r["risk_level"] == res.risk_level
        assert r["market_below_ma"] == res.trend_filters["market_below_21ema_or_50ma"]


def test_replay_columns_append_only():
    assert replay.COLUMNS[:14] == [
        "date",
        "index",
        "close",
        "pct_change",
        "volume_change",
        "is_dd",
        "d5",
        "d15",
        "d25",
        "ftd_state",
        "ftd_date",
        "rally_day1_date",
        "swing_low_date",
        "combined_state",
    ]
    assert replay.COLUMNS[14:] == ["risk_level", "market_below_ma"]


def test_thresholds_equal_default_yaml(original_skills):
    cfg = yaml.safe_load((original_skills.dd_dir.parent / "config" / "default.yaml").read_text())
    assert cfg["risk_thresholds"] == dd_glue.DEFAULT_RISK_CONFIG
    assert cfg["moving_average_filters"] == dd_glue.DEFAULT_MA_CONFIG
    mine = dd_glue.build_thresholds()
    orig = original_skills.ibd_monitor._build_thresholds(cfg)
    assert vars(mine) == vars(orig)
    assert vars(mine) == vars(
        loader.load_dd_modules().models.RiskThresholds()
    )  # dataclass defaults


def test_ma_path_synthetic_severe():
    """d25 == 5 (< severe 6) escalates to SEVERE only when the close is below both MAs."""
    dd = loader.load_dd_modules()
    th = dd_glue.build_thresholds()
    assert dd.risk_classifier.classify_risk(0, 3, 5, True, th) == "SEVERE"
    assert dd.risk_classifier.classify_risk(0, 3, 5, False, th) == "HIGH"
    assert dd.risk_classifier.classify_risk(0, 3, 5, None, th) == "HIGH"

    # steadily falling closes (newest first): below EMA21 and SMA50 -> True
    falling = [{"close": 100.0 + i * 0.5} for i in range(85)]  # newest (i=0) is lowest
    assert dd_glue.compute_market_below_ma(falling) is True
    rising = [{"close": 200.0 - i * 0.5} for i in range(85)]
    assert dd_glue.compute_market_below_ma(rising) is False
    # below only one MA -> not "below"
    oldest_first = [100.0] * 50 + [200.0] * 34 + [190.0]
    mixed = [{"close": c} for c in reversed(oldest_first)]
    assert dd_glue.compute_market_below_ma(mixed) is False
    # not enough bars for the 50SMA -> None
    assert dd_glue.compute_market_below_ma(falling[:40]) is None
    assert dd_glue.compute_market_below_ma([]) is None
    assert dd_glue.compute_market_below_ma(falling, {"enabled": False}) is None


def test_ema_differs_80_vs_85_bars_and_glue_equals_original(bars, original_skills):
    """The 21EMA is seeded from the oldest bars, so the window length changes its value.

    The replay uses the same 85-bar window as the original ibd_monitor (lookback 80 + 5); on
    that input the glue's EMA and market_below flag equal the original's, while an 80-bar
    window gives a different EMA.
    """
    ibd = original_skills.ibd_monitor
    mu = loader.load_dd_modules().math_utils
    ma_cfg = ibd.load_config(None)["moving_average_filters"]
    chrono = _hist(bars, "ftd_rich")[SP]
    differing = checked = 0
    for i in range(100, len(chrono), 7):
        h85 = list(reversed(chrono[i - 84 : i + 1]))  # newest first
        c85 = [b["close"] for b in h85]
        c80 = c85[:80]  # same newest day, five fewer old bars
        assert mu.calc_ema(c85, 21) == ibd.calc_ema(c85, 21)  # vendored == original
        differing += mu.calc_ema(c85, 21) != mu.calc_ema(c80, 21)
        orig_below = ibd._compute_ma_filters(h85, ma_cfg)[0]["market_below_21ema_or_50ma"]
        assert dd_glue.compute_market_below_ma(h85) == orig_below
        assert replay.dd_state(chrono, i).market_below_ma == orig_below
        checked += 1
    assert checked > 20
    assert differing == checked  # window length always matters for the EMA value


def test_risk_no_lookahead(bars):
    h = _hist(bars, "ftd_rich")
    k = 260
    t = h[SP][k - 1]["date"]
    start = h[SP][MIN_I]["date"]
    base = replay.replay({s: h[s][:k] for s in (SP, NQ)}, start=start)
    tampered = copy.deepcopy(h)
    for s in (SP, NQ):
        for bar in tampered[s][k:]:
            bar.update(
                close=bar["close"] * 0.3,
                high=bar["high"] * 0.3,
                low=bar["low"] * 0.3,
                volume=bar["volume"] * 7,
            )
    rows_t = replay.replay(tampered, start=start)
    a = [(r["date"], r["index"], r["risk_level"], r["market_below_ma"]) for r in base]
    b = [
        (r["date"], r["index"], r["risk_level"], r["market_below_ma"])
        for r in rows_t
        if r["date"] <= t
    ]
    assert a == b
    assert any(x[2] != "NORMAL" for x in a)  # non-vacuous
