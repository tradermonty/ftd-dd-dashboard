"""P2 acceptance: replay equivalence, dd_glue equivalence, no look-ahead. No network."""

from __future__ import annotations

import copy
import csv
from unittest import mock

import pytest
import yaml

from engine import dd_glue, loader, replay

SP, NQ = replay.SP500, replay.NASDAQ
MIN_I = 100  # keep >= 85 bars of history before the first compared day


def _hist(bars, seg):
    return {SP: bars[seg][SP], NQ: bars[seg][NQ]}


def _direct_ftd(mods, sp_chrono, nq_chrono, i):
    """Independent re-implementation of the 80-bar production call sequence."""
    sp80 = sp_chrono[i - 79 : i + 1]
    nq80 = nq_chrono[i - 79 : i + 1]
    state = mods.rally_tracker.get_market_state(list(reversed(sp80)), list(reversed(nq80)))
    return mods.post_ftd_monitor.assess_post_ftd_health(state, sp80, nq80)


def _fields_from_state(state, key):
    s = state[key]
    ftd = s.get("ftd") or {}
    return {
        "ftd_state": s["state"],
        "ftd_date": ftd.get("ftd_date") if ftd.get("ftd_detected") else None,
        "rally_day1_date": (s.get("rally_attempt") or {}).get("day1_date"),
        "swing_low_date": (s.get("swing_low") or {}).get("swing_low_date"),
        "combined_state": state["combined_state"],
    }


@pytest.mark.parametrize("seg", ["ftd_rich", "latest"])
def test_replay_equals_direct_vendored_calls(bars, seg):
    h = _hist(bars, seg)
    start = h[SP][MIN_I]["date"]
    rows = replay.replay(h, start=start)
    ftd_mods = loader.load_ftd_modules()
    dd = loader.load_dd_modules()
    rule = dd_glue.build_rule()
    by_key = {(r["date"], r["index"]): r for r in rows}

    states_seen = set()
    for i in range(MIN_I, len(h[SP])):
        d = h[SP][i]["date"]
        state = _direct_ftd(ftd_mods, h[SP], h[NQ], i)
        for sym, key in ((SP, "sp500"), (NQ, "nasdaq")):
            rec = by_key[(d, sym)]
            exp = _fields_from_state(state, key)
            for k, v in exp.items():
                assert rec[k] == v, (d, sym, k)
            states_seen.add(rec["ftd_state"])

            hist85 = list(reversed(h[sym][i - 84 : i + 1]))
            recs, _ = dd.tracker.detect_distribution_days(hist85, rule)
            recs = dd.tracker.enrich_records(recs, hist85, rule)
            assert rec["d5"] == dd.tracker.count_active_in_window(recs, 5)
            assert rec["d15"] == dd.tracker.count_active_in_window(recs, 15)
            assert rec["d25"] == dd.tracker.count_active_in_window(recs, 25)
            assert rec["is_dd"] == any(r.dd_index == 0 and r.status == "active" for r in recs)
            bar, prev = h[sym][i], h[sym][i - 1]
            assert rec["close"] == bar["close"]
            assert rec["pct_change"] == pytest.approx(bar["close"] / prev["close"] - 1)
    assert len(rows) == 2 * (len(h[SP]) - MIN_I)
    if seg == "ftd_rich":
        # fixture must actually exercise the FTD machinery, otherwise the test is vacuous
        assert "FTD_CONFIRMED" in states_seen
        assert len(states_seen) >= 4


@pytest.mark.parametrize("seg", ["ftd_rich", "latest"])
def test_replay_equals_original_skill_modules(bars, original_skills, seg):
    h = _hist(bars, seg)
    orig = original_skills
    ibd = orig.ibd_monitor
    config = ibd.load_config(None)
    rule = ibd._build_rule(config)
    thresholds = ibd._build_thresholds(config)

    rows = replay.replay(h, start=h[SP][MIN_I]["date"])
    by_key = {(r["date"], r["index"]): r for r in rows}
    ftd_seen = 0
    for i in range(MIN_I, len(h[SP])):
        d = h[SP][i]["date"]
        sp80, nq80 = h[SP][i - 79 : i + 1], h[NQ][i - 79 : i + 1]
        state = orig.rally_tracker.get_market_state(list(reversed(sp80)), list(reversed(nq80)))
        state = orig.post_ftd_monitor.assess_post_ftd_health(state, sp80, nq80)
        # whole-structure equality with the vendored pipeline, not just selected fields
        mine = replay.ftd_market_state(h[SP], h[NQ], i)
        assert mine == state, d
        for sym, key in ((SP, "sp500"), (NQ, "nasdaq")):
            rec = by_key[(d, sym)]
            for k, v in _fields_from_state(state, key).items():
                assert rec[k] == v, (d, sym, k)
            ftd_seen += rec["ftd_state"] == "FTD_CONFIRMED"
            hist85 = list(reversed(h[sym][i - 84 : i + 1]))
            res, _ = ibd.analyze_index(sym, sym, hist85, config, rule, thresholds, None)
            assert (rec["d5"], rec["d15"], rec["d25"]) == (
                res.d5_count,
                res.d15_count,
                res.d25_count,
            ), (d, sym)
            assert rec["is_dd"] == res.is_distribution_day_today, (d, sym)
    if seg == "ftd_rich":
        assert ftd_seen > 0


def test_dd_glue_equals_original_analyze_index(bars, original_skills):
    ibd = original_skills.ibd_monitor
    config = ibd.load_config(None)
    rule = ibd._build_rule(config)
    thresholds = ibd._build_thresholds(config)
    checked = 0
    for seg in ("ftd_rich", "latest"):
        for sym in (SP, NQ):
            chrono = bars[seg][sym]
            # several history lengths, including shorter-than-window and the full fixture
            for i in list(range(30, len(chrono), 7)) + [len(chrono) - 1]:
                hist = list(reversed(chrono[: i + 1]))
                res, _ = ibd.analyze_index(sym, sym, hist, config, rule, thresholds, None)
                mine = dd_glue.analyze(hist)
                assert (mine.d5, mine.d15, mine.d25) == (
                    res.d5_count,
                    res.d15_count,
                    res.d25_count,
                )
                assert mine.today_is_dd == res.is_distribution_day_today
                checked += 1
    assert checked > 100


def test_default_rule_matches_original_default_yaml(original_skills):
    cfg = yaml.safe_load((original_skills.dd_dir.parent / "config" / "default.yaml").read_text())
    assert (
        cfg["distribution_day_rule"]["min_decline_pct"]
        == dd_glue.DEFAULT_RULE_CONFIG["min_decline_pct"]
    )
    for k, v in dd_glue.DEFAULT_RULE_CONFIG.items():
        assert cfg["distribution_day_rule"][k] == v
    rule = dd_glue.build_rule()
    orig_rule = original_skills.ibd_monitor._build_rule(cfg)
    assert vars(rule) == vars(orig_rule)


@pytest.mark.parametrize("k", [150, 260, 399])
def test_no_lookahead(bars, k):
    h = _hist(bars, "ftd_rich")
    t = h[SP][k - 1]["date"]
    start = h[SP][MIN_I]["date"]
    base = replay.replay({s: h[s][:k] for s in (SP, NQ)}, start=start)
    longer = replay.replay(h, start=start)
    a = [r for r in base if r["date"] == t]
    b = [r for r in longer if r["date"] == t]
    assert a and a == b
    # result at every day <= t is also unchanged by appending future bars
    assert base == [r for r in longer if r["date"] <= t]

    # even wildly different future bars must not change anything up to t
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
    assert base == [r for r in rows_t if r["date"] <= t]


def test_window_sizes_and_ordering(bars):
    h = _hist(bars, "latest")
    mods = loader.load_ftd_modules()
    dd = loader.load_dd_modules()
    i = len(h[SP]) - 1
    with (
        mock.patch.object(
            mods.rally_tracker, "get_market_state", wraps=mods.rally_tracker.get_market_state
        ) as gms,
        mock.patch.object(
            mods.post_ftd_monitor,
            "assess_post_ftd_health",
            wraps=mods.post_ftd_monitor.assess_post_ftd_health,
        ) as aph,
        mock.patch.object(
            dd.tracker, "detect_distribution_days", wraps=dd.tracker.detect_distribution_days
        ) as ddd,
    ):
        replay.replay(h, start=h[SP][i]["date"])
    t = h[SP][i]["date"]
    sp_new, nq_new = gms.call_args.args
    assert len(sp_new) == len(nq_new) == 80
    assert sp_new[0]["date"] == t and sp_new[0]["date"] > sp_new[1]["date"]  # newest first
    _, sp_chrono, nq_chrono = aph.call_args.args
    assert len(sp_chrono) == len(nq_chrono) == 80
    assert sp_chrono[-1]["date"] == t and sp_chrono[0]["date"] < sp_chrono[1]["date"]
    assert ddd.call_count == 2
    for call in ddd.call_args_list:
        hist = call.args[0]
        assert len(hist) == 85 and hist[0]["date"] == t and hist[0]["date"] > hist[1]["date"]


def test_enrich_gets_fresh_records_each_day(bars):
    # enrich_records mutates; two identical replays must be identical (no state carried over)
    h = _hist(bars, "latest")
    s = h[SP][MIN_I]["date"]
    assert replay.replay(h, start=s) == replay.replay(h, start=s)


def test_mismatched_date_axes_rejected(bars):
    h = _hist(bars, "latest")
    bad = {SP: h[SP], NQ: h[NQ][1:]}
    with pytest.raises(ValueError):
        replay.replay(bad)


def test_write_daily_status_csv(bars, tmp_path):
    h = _hist(bars, "latest")
    rows = replay.replay(h, start=h[SP][-3]["date"])
    out = tmp_path / "daily_status.csv"
    replay.write_daily_status(rows, out)
    with out.open() as f:
        got = list(csv.DictReader(f))
    assert list(got[0].keys()) == replay.COLUMNS
    assert len(got) == 6
    assert {g["index"] for g in got} == {SP, NQ}


def test_window_constants_equal_original_skills(original_skills):
    """FTD_WINDOW / DD_WINDOW must match the production skills at the pinned ref."""
    import re

    ftd_src = (original_skills.ftd_dir / "ftd_detector.py").read_text()
    days = re.findall(r"get_historical_prices\(\s*[^,]+,\s*days=(\d+)\s*\)", ftd_src)
    assert len(days) == 2, days  # S&P 500 and the NASDAQ proxy
    assert {int(d) for d in days} == {replay.FTD_WINDOW}

    cfg = yaml.safe_load((original_skills.dd_dir.parent / "config" / "default.yaml").read_text())
    ibd_src = (original_skills.dd_dir / "ibd_monitor.py").read_text()
    assert 'config.get("lookback_days", 80)' in ibd_src
    assert re.search(r"fetch_ohlcv\(client, symbol, days=lookback \+ 5\)", ibd_src)
    assert cfg["lookback_days"] + 5 == replay.DD_WINDOW
