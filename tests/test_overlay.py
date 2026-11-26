"""Research R2 (docs/plan.md section 3.7): FTD leverage overlay. Synthetic data, no network."""

from __future__ import annotations

import csv
import math
import random
from datetime import date, timedelta

import pytest

from engine import loader
from research import overlay


def make_bars(n: int, seed: int = 1, start: str = "2000-01-03") -> list[dict]:
    """Business-day bars with a random walk; low <= close."""
    rng = random.Random(seed)
    d = date.fromisoformat(start)
    px, out = 100.0, []
    while len(out) < n:
        if d.weekday() < 5:
            px *= 1 + rng.gauss(0.0003, 0.01)
            out.append(
                {
                    "date": d.isoformat(),
                    "close": px,
                    "low": px * (1 - abs(rng.gauss(0, 0.004))),
                    "volume": 1,
                }
            )
        d += timedelta(days=1)
    return out


def split(bars):
    return (
        [b["date"] for b in bars],
        [b["close"] for b in bars],
        [b["low"] for b in bars],
    )


def ev(dates, ftd_i, det_i=None):
    return {"ftd_date": dates[ftd_i], "detected_on": dates[ftd_i if det_i is None else det_i]}


def flat(n=20):
    """Flat prices (close 100, low 99): nothing invalidates unless the test pushes a close."""
    bars = make_bars(n)
    for b in bars:
        b["close"], b["low"] = 100.0, 99.0
    return bars


# ----------------------------------------------------------------------------- decisions


def test_variants_are_twelve_and_ids_unique():
    vs = overlay.variants()
    assert len(vs) == overlay.N_VARIANTS == 12
    assert len({v["id"] for v in vs}) == 12
    assert {v["k"] for v in vs} == {3, 4, 5}
    assert {v["leverage"] for v in vs} == {1.25, 1.5}
    assert {v["use_invalidation"] for v in vs} == {True, False}


def test_entry_at_detected_on_and_exit_on_kth_dd_after_ftd():
    bars = flat(20)
    dates, closes, lows = split(bars)
    is_dd = [False] * 20
    for i in (4, 5, 6):  # three DDs strictly after FTD day 2
        is_dd[i] = True
    exp, episodes = overlay.decide_exposure(
        dates, closes, lows, is_dd, [ev(dates, 2)], k=3, use_invalidation=False, leverage=1.5
    )
    # decided at the close of the day: levered from day 2's close, flat again at day 6's close
    assert exp[:2] == [1.0, 1.0]
    assert exp[2:6] == [1.5, 1.5, 1.5, 1.5]
    assert exp[6:] == [1.0] * 14
    assert episodes == [
        {"entry": dates[2], "exit": dates[6], "reason": "dd_count", "ftd_date": dates[2]}
    ]


def test_dd_on_the_ftd_day_is_not_counted():
    bars = flat(20)
    dates, closes, lows = split(bars)
    is_dd = [False] * 20
    is_dd[2] = is_dd[4] = is_dd[5] = True  # FTD day 2 itself is a DD: only 2 count after it
    exp, _ = overlay.decide_exposure(
        dates, closes, lows, is_dd, [ev(dates, 2)], k=3, use_invalidation=False, leverage=1.5
    )
    assert all(e == 1.5 for e in exp[2:])  # never reaches 3


def test_dds_between_ftd_day_and_detection_count_point_in_time():
    bars = flat(20)
    dates, closes, lows = split(bars)
    is_dd = [False] * 20
    is_dd[3] = is_dd[4] = True  # FTD day 2, detected on day 5: both known at detection
    is_dd[7] = True
    exp, _ = overlay.decide_exposure(
        dates, closes, lows, is_dd, [ev(dates, 2, 5)], k=3, use_invalidation=False, leverage=1.5
    )
    assert exp[4] == 1.0 and exp[5] == 1.5  # entry on detected_on, not on the FTD day
    assert exp[6] == 1.5 and exp[7] == 1.0  # third DD on day 7 ends it


def test_entry_skipped_when_exit_condition_already_met_at_detection():
    bars = flat(20)
    dates, closes, lows = split(bars)
    is_dd = [False] * 20
    is_dd[3] = is_dd[4] = is_dd[5] = True
    exp, episodes = overlay.decide_exposure(
        dates, closes, lows, is_dd, [ev(dates, 2, 5)], k=3, use_invalidation=False, leverage=1.5
    )
    assert exp == [1.0] * 20 and episodes == []


def test_invalidation_exit_is_close_below_ftd_day_low():
    bars = flat(20)
    dates, closes, lows = split(bars)
    lows[2] = 99.0
    closes[5] = 98.9  # close below the FTD day's low
    is_dd = [False] * 20
    with_inv, ep = overlay.decide_exposure(
        dates, closes, lows, is_dd, [ev(dates, 2)], k=5, use_invalidation=True, leverage=1.25
    )
    assert with_inv[4] == 1.25 and with_inv[5] == 1.0
    assert ep[0]["exit"] == dates[5] and ep[0]["reason"] == "invalidation"
    without, _ = overlay.decide_exposure(
        dates, closes, lows, is_dd, [ev(dates, 2)], k=5, use_invalidation=False, leverage=1.25
    )
    assert all(e == 1.25 for e in without[2:])


def test_invalidation_rule_equals_vendored_check():
    mod = loader.load_ftd_modules().post_ftd_monitor
    bars = make_bars(80, seed=7)
    dates, closes, lows = split(bars)
    for ftd_i in (10, 25, 40):
        vend = mod.check_ftd_invalidation(bars, ftd_i)
        exp, ep = overlay.decide_exposure(
            dates,
            closes,
            lows,
            [False] * 80,
            [ev(dates, ftd_i)],
            k=99,
            use_invalidation=True,
            leverage=1.5,
        )
        if vend["invalidated"]:
            assert ep and ep[0]["exit"] == vend["invalidation_date"]
            assert ep[0]["reason"] == "invalidation"
        else:
            assert not ep or ep[0]["exit"] is None


def test_new_ftd_ignored_while_levered_then_allowed_after_exit():
    bars = flat(30)
    dates, closes, lows = split(bars)
    is_dd = [False] * 30
    is_dd[5] = is_dd[6] = is_dd[7] = True  # k=3 -> exit at 7
    events = [ev(dates, 2), ev(dates, 4), ev(dates, 12)]
    exp, episodes = overlay.decide_exposure(
        dates, closes, lows, is_dd, events, k=3, use_invalidation=False, leverage=1.5
    )
    assert [e["entry"] for e in episodes] == [dates[2], dates[12]]
    assert episodes[0]["exit"] == dates[7]
    # the FTD of day 4 was ignored: DDs are counted from FTD day 2 only
    assert exp[7] == 1.0
    assert exp[12] == 1.5  # new episode: needs 3 more DDs after FTD day 12 (there are none)
    assert episodes[1]["exit"] is None


def test_never_below_100_percent_and_no_leverage_is_all_ones():
    bars = make_bars(300, seed=3)
    dates, closes, lows = split(bars)
    rng = random.Random(5)
    is_dd = [rng.random() < 0.2 for _ in dates]
    events = [ev(dates, i) for i in (20, 80, 150, 220)]
    for v in overlay.variants():
        exp, _ = overlay.decide_exposure(
            dates, closes, lows, is_dd, events, v["k"], v["use_invalidation"], v["leverage"]
        )
        assert min(exp) >= 1.0
    exp, _ = overlay.decide_exposure(dates, closes, lows, is_dd, events, 3, True, 1.0)
    assert set(exp) == {1.0}


# --------------------------------------------------------------------------- no look-ahead


def test_decisions_at_or_before_t_do_not_depend_on_later_data():
    rng = random.Random(11)
    bars = make_bars(400, seed=2)
    dates, closes, lows = split(bars)
    is_dd = [rng.random() < 0.2 for _ in dates]
    events = [ev(dates, i, i + (1 if i % 3 == 0 else 0)) for i in (15, 70, 90, 180, 260, 330)]
    for v in overlay.variants():
        args = (v["k"], v["use_invalidation"], v["leverage"])
        full, _ = overlay.decide_exposure(dates, closes, lows, is_dd, events, *args)
        for t in (30, 71, 100, 181, 262, 340):
            # (a) truncated inputs: nothing after t exists at all
            cut_events = [e for e in events if e["detected_on"] <= dates[t]]
            trunc, _ = overlay.decide_exposure(
                dates[: t + 1], closes[: t + 1], lows[: t + 1], is_dd[: t + 1], cut_events, *args
            )
            assert trunc == full[: t + 1]
            # (b) full length but everything after t altered
            c2, l2, d2 = list(closes), list(lows), list(is_dd)
            for j in range(t + 1, len(dates)):
                c2[j], l2[j], d2[j] = 1e-3, 1e-3, not is_dd[j]
            altered, _ = overlay.decide_exposure(dates, c2, l2, d2, events, *args)
            assert altered[: t + 1] == full[: t + 1]


DATES4 = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]  # Thu Fri Mon Tue
DFF3 = [("2020-01-01", 0.0150), ("2020-01-03", 0.0160), ("2020-01-06", 0.0200)]


def test_rate_known_uses_the_value_published_by_the_previous_close():
    # FRED publishes a day's DFF on the next business day: at the close of session j the newest
    # published value is dated on or before session j-1.
    known = overlay.rate_known(DATES4, DFF3)
    assert known == [0.0150, 0.0150, 0.0160, 0.0200]
    # the observation dated the same day (or the previous session for the first dates) is not
    # used: Fri 01-03 itself gets 0.015, not its own 0.016
    assert known[1] == 0.0150 and known[2] == 0.0160


def test_rate_known_legacy_lag_zero_is_the_earlier_rule():
    assert overlay.rate_known(DATES4, DFF3, lag=0) == [0.0150, 0.0160, 0.0200, 0.0200]
    assert overlay.RATE_LAG_SESSIONS == 1  # the amended rule is the default


def test_rate_known_on_day_t_uses_only_observations_before_t():
    known = overlay.rate_known(DATES4, DFF3)
    altered = [*DFF3, ("2020-01-07", 0.9)]
    assert overlay.rate_known(DATES4[:3], altered) == known[:3]
    # an observation dated on the session itself never reaches that session's rate
    bumped = [(d, 0.99 if d == "2020-01-06" else r) for d, r in DFF3]
    assert overlay.rate_known(DATES4, bumped)[2] == known[2]  # Mon 01-06 ignores Mon's value
    assert overlay.rate_known(DATES4, bumped)[3] == 0.99  # Tue sees it


def test_rate_known_first_session_uses_only_values_before_it():
    dff = [("2019-12-31", 0.01), ("2020-01-02", 0.5)]
    # the first session sees only values dated before it; the next one sees the first's value
    assert overlay.rate_known(["2020-01-02", "2020-01-03"], dff) == [0.01, 0.5]
    with pytest.raises(ValueError):
        overlay.rate_known(["2020-01-02"], [("2020-01-02", 0.5)])


# ------------------------------------------------------------------------ cost arithmetic


def test_borrow_cost_arithmetic_calendar_days_on_flat_prices():
    # Fri 2020-01-03 -> Mon 2020-01-06 is 3 calendar days; rate known on the Friday is used.
    dates = ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"]
    closes = [100.0] * 4
    exp = [1.5, 1.5, 1.5, 1.5]
    rates = [0.04, 0.05, 0.09, 0.09]  # annual rates known at each close
    r = overlay.strategy_returns(dates, closes, exp, rates, "2020-01-02", "2020-01-07")
    assert r["k"] == [1, 2, 3]
    expected = [
        -(0.5) * 0.04 * 1 / 365,  # Thu -> Fri uses Thursday's rate
        -(0.5) * 0.05 * 3 / 365,  # Fri -> Mon uses Friday's rate, 3 calendar days
        -(0.5) * 0.09 * 1 / 365,
    ]
    assert r["ret"] == pytest.approx(expected, abs=1e-15)
    assert r["bh"] == [0.0, 0.0, 0.0]


def test_cost_applies_to_exposure_minus_one_only_and_scales_returns():
    dates = ["2020-01-02", "2020-01-03"]
    closes = [100.0, 102.0]
    r = overlay.strategy_returns(dates, closes, [1.25, 1.25], [0.06, 0.06], dates[0], dates[1])
    assert r["ret"][0] == pytest.approx(1.25 * 0.02 - 0.25 * 0.06 / 365, abs=1e-15)
    r1 = overlay.strategy_returns(dates, closes, [1.0, 1.0], [0.06, 0.06], dates[0], dates[1])
    assert r1["ret"][0] == pytest.approx(0.02, abs=1e-15)  # exposure 1.0: no cost at all


def test_cost_models_definition():
    ids = [c["id"] for c in overlay.COST_MODELS]
    assert ids == ["dff_plus_1.5", "fixed_0", "fixed_3", "fixed_6"]
    dates = ["2020-01-02", "2020-01-03"]
    dff = [("2020-01-01", 0.02)]
    main = overlay.annual_rates(dates, dff, overlay.COST_MODELS[0])
    assert main == pytest.approx([0.035, 0.035])
    assert overlay.annual_rates(dates, dff, overlay.COST_MODELS[3]) == [0.06, 0.06]


# ----------------------------------------------------------------- buy & hold equivalence


def _metrics(bars, exp, rates, start, end):
    dates, closes, _ = split(bars)
    return overlay.evaluate(dates, closes, exp, rates, start, end)


def test_no_leverage_reproduces_buy_and_hold_exactly():
    bars = make_bars(600, seed=9)
    dates, closes, lows = split(bars)
    rng = random.Random(1)
    is_dd = [rng.random() < 0.2 for _ in dates]
    events = [ev(dates, i) for i in (30, 120, 300)]
    exp, _ = overlay.decide_exposure(dates, closes, lows, is_dd, events, 3, True, 1.0)
    rates = [0.05] * len(dates)
    m = _metrics(bars, exp, rates, dates[0], dates[-1])
    assert m["cagr"] == m["bh_cagr"] and m["max_dd"] == m["bh_max_dd"] and m["vol"] == m["bh_vol"]
    assert m["levered_share"] == 0 and m["n_episodes"] == 0
    # and the buy&hold CAGR matches a direct computation on the same return days
    n = len(dates) - 1
    direct = (closes[-1] / closes[0]) ** (252 / n) - 1
    assert m["bh_cagr"] == pytest.approx(direct, rel=1e-9)
    assert m["em_cagr"] == m["bh_cagr"]


def test_metrics_levered_share_episodes_and_exposure_matched_benchmark():
    bars = make_bars(300, seed=4)
    dates, closes, lows = split(bars)
    exp = [1.0] * 300
    for i in list(range(50, 80)) + list(range(100, 110)):
        exp[i] = 1.5
    rates = [0.03] * 300
    m = _metrics(bars, exp, rates, dates[0], dates[-1])
    assert m["n_episodes"] == 2
    n_days = len(dates) - 1
    assert m["levered_share"] == pytest.approx(40 / n_days)
    assert m["avg_exposure"] == pytest.approx(1 + 0.5 * 40 / n_days)
    # exposure-matched benchmark = constant avg exposure with the same cost model
    r = overlay.strategy_returns(
        dates, closes, [m["avg_exposure"]] * 300, rates, dates[0], dates[-1]
    )
    em = overlay._perf(r["ret"])
    assert m["em_cagr"] == pytest.approx(em["cagr"]) and m["em_max_dd"] == pytest.approx(
        em["max_dd"]
    )


def test_worst_equity_drawdown_while_levered_and_margin_stress():
    dates = [(date(2020, 1, 1) + timedelta(days=i)).isoformat() for i in range(8)]
    closes = [100, 100, 80, 60, 40, 40, 40, 40]  # -60% from entry while 1.5x levered
    exp = [1.0, 1.5, 1.5, 1.5, 1.5, 1.0, 1.0, 1.0]
    rates = [0.0] * 8
    m = overlay.evaluate(dates, [float(c) for c in closes], exp, rates, dates[0], dates[-1])
    # daily-rebalanced 1.5x: (1-.3)*(1-.375)*(1-.5) -> below 0.25 of entry equity; worst < -50%
    assert m["worst_levered_dd"] < -0.5
    assert m["margin_stress"] is True and m["margin_stress_days"] >= 1
    assert m["min_equity_ratio"] < overlay.MAINTENANCE_EQUITY_RATIO
    calm = overlay.evaluate(
        dates,
        [100.0 + i * 0.1 for i in range(8)],
        exp,
        rates,
        dates[0],
        dates[-1],
    )
    assert calm["margin_stress"] is False and calm["margin_stress_days"] == 0
    assert calm["worst_levered_dd"] >= -0.01


def test_margin_ratio_formula():
    # entry: position 1.5, loan 0.5 -> equity ratio 2/3; position -50% -> (0.75-0.5)/0.75 = 1/3
    ratio = overlay.equity_ratio(position=0.75, loan=0.5)
    assert ratio == pytest.approx(1 / 3)
    assert overlay.MAINTENANCE_EQUITY_RATIO == 0.25


# ------------------------------------------------------------------------ split and selection


def test_selection_rejects_non_design_rows_and_is_deterministic():
    good = {
        "variant_id": "a",
        "index": "^GSPC",
        "period": "design",
        "cagr": 0.05,
        "max_dd": -0.4,
        "bh_max_dd": -0.5,
        "bh_cagr": 0.03,
    }
    with pytest.raises(ValueError):
        overlay.select_overlay([{**good, "period": "test"}], "buy_hold")
    rows = [
        good,
        {**good, "variant_id": "b", "cagr": 0.07, "max_dd": -0.52},  # qualifies (> -0.55)
        {**good, "variant_id": "c", "cagr": 0.09, "max_dd": -0.56},  # too deep
        {**good, "variant_id": "d", "cagr": 0.07, "max_dd": -0.30},  # tie with b -> id order
    ]
    out = overlay.select_overlay(rows, "buy_hold")
    assert out["^GSPC"]["selected"] == "b"
    assert out["^GSPC"]["n_qualifying"] == 3 and not out["^GSPC"]["fallback_to_baseline"]
    none = overlay.select_overlay([{**good, "max_dd": -0.9}], "buy_hold")
    assert none["^GSPC"]["selected"] == "buy_hold" and none["^GSPC"]["fallback_to_baseline"]


def test_criterion_is_preregistered_constants():
    assert overlay.MAX_DD_TOLERANCE == 0.05
    assert overlay.MAIN_COST_ID == "dff_plus_1.5"
    assert "highest design CAGR" in overlay.CRITERION and "5" in overlay.CRITERION


def _small_world(n=900, seed=21):
    bars = make_bars(n, seed=seed)
    dates, closes, lows = split(bars)
    rng = random.Random(seed)
    is_dd = [rng.random() < 0.2 for _ in dates]
    events = [{"index": "^GSPC", **ev(dates, i)} for i in range(40, n - 40, 70)]
    events += [{"index": "^IXIC", **ev(dates, i + 5)} for i in range(40, n - 40, 90)]
    h = {"^GSPC": bars, "^IXIC": bars}
    dff = [("1999-01-01", 0.03)]
    return h, events, {"^GSPC": is_dd, "^IXIC": is_dd}, dff, dates


def test_run_overlay_split_enforcement():
    h, events, dd, dff, dates = _small_world()
    design_start, design_end, test_start = dates[5], dates[500], dates[501]
    res = overlay.run_overlay(h, events, dd, dff, design_start, design_end, test_start)
    assert len(res["design_rows"]) == 12 * 2 * len(overlay.COST_MODELS)
    assert {r["period"] for r in res["design_rows"]} == {"design"}
    assert {r["period"] for r in res["test_rows"]} == {"test"}
    # test rows only for baseline + the selected variant
    for idx in ("^GSPC", "^IXIC"):
        ids = {r["variant_id"] for r in res["test_rows"] if r["index"] == idx}
        assert ids == {overlay.BASELINE_ID, res["chosen"][idx]["selected"]} or (
            ids == {overlay.BASELINE_ID}
        )
    assert res["n_variants"] == 12

    # design results cannot see test bars: wreck everything after design_end
    h2 = {k: [dict(b) for b in v] for k, v in h.items()}
    for v in h2.values():
        for b in v[501:]:
            b["close"] = b["close"] * 0.1
            b["low"] = b["low"] * 0.1
    res2 = overlay.run_overlay(h2, events, dd, dff, design_start, design_end, test_start)
    assert res2["design_rows"] == res["design_rows"]
    assert res2["chosen"] == res["chosen"]


def test_selection_uses_main_cost_model_rows_only():
    h, events, dd, dff, dates = _small_world()
    res = overlay.run_overlay(h, events, dd, dff, dates[5], dates[500], dates[501])
    main = [r for r in res["design_rows"] if r["cost_model"] == overlay.MAIN_COST_ID]
    again = overlay.select_overlay(main, overlay.BASELINE_ID)
    assert {i: c["selected"] for i, c in again.items()} == {
        i: c["selected"] for i, c in res["chosen"].items()
    }


def test_levered_bands_of_selected_variant():
    h, events, dd, dff, dates = _small_world()
    res = overlay.run_overlay(h, events, dd, dff, dates[5], dates[500], dates[501])
    for bands in res["bands"].values():
        for b in bands:
            assert b["x0"] <= b["x1"] and b["x0"] in dates and b["x1"] in dates


# ------------------------------------------------------------------------------- DFF file


def test_load_dff_parses_percent_and_skips_missing(tmp_path):
    p = tmp_path / "fred_DFF.csv"
    p.write_text("observation_date,DFF\n2020-01-01,1.50\n2020-01-02,.\n2020-01-03,1.75\n")
    assert overlay.load_dff(p) == [("2020-01-01", 0.015), ("2020-01-03", 0.0175)]


def test_load_or_fetch_uses_cache_without_network(tmp_path):
    p = tmp_path / "fred_DFF.csv"
    p.write_text("observation_date,DFF\n2020-01-01,1.50\n")

    def boom(url):
        raise AssertionError("network used although the cache exists")

    assert overlay.load_or_fetch_dff(p, fetch=boom) == [("2020-01-01", 0.015)]


def test_load_or_fetch_downloads_once_when_missing(tmp_path):
    p = tmp_path / "raw" / "fred_DFF.csv"
    calls = []

    def fake(url):
        calls.append(url)
        return "observation_date,DFF\n2020-01-01,2.00\n"

    assert overlay.load_or_fetch_dff(p, fetch=fake) == [("2020-01-01", 0.02)]
    assert overlay.load_or_fetch_dff(p, fetch=fake) == [("2020-01-01", 0.02)]
    assert len(calls) == 1 and "id=DFF" in calls[0]


# ---------------------------------------------------------------------------------- outputs


def test_output_columns_and_files(tmp_path):
    h, events, dd, dff, dates = _small_world()
    res = overlay.run_overlay(h, events, dd, dff, dates[5], dates[500], dates[501])
    overlay.write_outputs(res, tmp_path)
    rows = list(csv.DictReader((tmp_path / "overlay_variants.csv").open()))
    assert rows and list(rows[0]) == overlay.VARIANT_COLUMNS
    trows = list(csv.DictReader((tmp_path / "overlay_test_results.csv").open()))
    assert trows and list(trows[0]) == overlay.TEST_COLUMNS
    sel = __import__("json").loads((tmp_path / "overlay_selected.json").read_text())
    assert sel["n_variants"] == 12 and "criterion" in sel and "not a trading" in sel["disclaimer"]
    assert set(sel["indices"]) == {"^GSPC", "^IXIC"}
    info = sel["indices"]["^GSPC"]
    assert set(info["test"]) >= {"baseline", "selected"} and not math.isnan(
        info["test"]["baseline"][overlay.MAIN_COST_ID]["cagr"]
    )
