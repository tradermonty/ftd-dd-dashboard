"""Baseline == P3 results, and no look-ahead, for the research signal code. No network."""

from __future__ import annotations

import copy

import pytest

from engine import dd_glue, events, loader, replay
from research import dd_signals, ftd_signals, variants

SP, NQ = replay.SP500, replay.NASDAQ
MIN_I = 100


def _hist(bars, seg):
    return {SP: bars[seg][SP], NQ: bars[seg][NQ]}


def _rec_tuple(r):
    return (
        r.date,
        r.dd_index,
        r.age_sessions,
        r.close,
        r.pct_change,
        r.volume,
        r.prev_volume,
        r.volume_change_pct,
        r.high_since,
        r.invalidation_date,
        r.status,
        r.removal_reason,
    )


def _perturb_after(hist, t):
    out = copy.deepcopy(hist)
    for sym in out:
        for k, b in enumerate(out[sym]):
            if k > t:
                f = 1.11 if k % 2 else 0.93
                b["close"] *= f
                b["high"] *= f * 1.01
                b["low"] *= f * 0.99
                b["volume"] = int(b["volume"] * (0.4 if k % 3 else 2.5))
    return out


@pytest.mark.parametrize("seg", ["ftd_rich", "latest"])
def test_baseline_ftd_events_equal_p3(bars, seg):
    h = _hist(bars, seg)
    start = h[SP][MIN_I]["date"]
    rows = replay.replay(h, start=start, keep_raw=True)
    expected = events.ftd_events(rows, h)
    got = ftd_signals.ftd_event_table(h, 3.0, 1.25, start)
    assert got == expected


def test_baseline_ftd_events_nonempty_somewhere(bars):
    h = _hist(bars, "ftd_rich")
    start = h[SP][MIN_I]["date"]
    assert len(ftd_signals.ftd_event_table(h, 3.0, 1.25, start)) > 0


@pytest.mark.parametrize("seg", ["ftd_rich", "latest"])
def test_baseline_dd_equals_p3_replay_and_vendored_records(bars, seg):
    h = _hist(bars, seg)
    start = h[SP][MIN_I]["date"]
    rows = replay.replay(h, start=start)
    by_key = {(r["date"], r["index"]): r for r in rows}
    dd = loader.load_dd_modules()
    rule = dd_glue.build_rule()
    for sym in (SP, NQ):
        chrono = h[sym]
        s = dd_signals.dd_series(chrono, -0.002, "prev_day")
        protos = dd_signals.detection_protos(chrono, -0.002, "prev_day")
        for t, d in enumerate(s.dates):
            if d < start:
                continue
            r = by_key[(d, sym)]
            assert (s.d5[t], s.d15[t], s.d25[t], s.is_dd[t], s.level[t]) == (
                r["d5"],
                r["d15"],
                r["d25"],
                r["is_dd"],
                r["risk_level"],
            ), (sym, d)
        # record-level equality with the vendored detect + enrich on sampled windows
        for t in range(MIN_I, len(chrono), 17):
            window = chrono[max(0, t - 84) : t + 1][::-1]
            v_recs, _ = dd.tracker.detect_distribution_days(window, rule)
            v_recs = dd.tracker.enrich_records(v_recs, window, rule)
            mine = dd.tracker.enrich_records(dd_signals.records_at(protos, t), window, rule)
            assert [_rec_tuple(x) for x in mine] == [_rec_tuple(x) for x in v_recs], (sym, t)


def test_decline_variant_matches_vendored_rule(bars):
    h = _hist(bars, "ftd_rich")[SP]
    dd = loader.load_dd_modules()
    rule = dd_glue.build_rule({**dd_glue.DEFAULT_RULE_CONFIG, "min_decline_pct": -0.005})
    protos = dd_signals.detection_protos(h, -0.005, "prev_day")
    for t in range(MIN_I, len(h), 23):
        window = h[max(0, t - 84) : t + 1][::-1]
        v_recs, _ = dd.tracker.detect_distribution_days(window, rule)
        mine = dd_signals.records_at(protos, t)
        assert [(r.date, r.dd_index) for r in mine] == [(r.date, r.dd_index) for r in v_recs]


def test_volume_rules_are_ordered_subsets(bars):
    h = _hist(bars, "ftd_rich")[SP]
    flagged = {
        vr: {j for j, p in enumerate(dd_signals.detection_protos(h, -0.002, vr)) if p}
        for vr in variants.VOLUME_RULES
    }
    assert flagged["both"] == flagged["prev_day"] & flagged["avg50"]
    assert flagged["both"] <= flagged["prev_day"] and flagged["both"] <= flagged["avg50"]
    assert flagged["avg50"] != flagged["prev_day"]  # the rule actually changes something
    # avg50 definition by hand on one flagged day
    j = max(flagged["avg50"])
    prior = [b["volume"] for b in h[j - 50 : j]]
    assert h[j]["volume"] > sum(prior) / len(prior)


def test_percentile_elevated_hand_checked():
    d25 = [1, 1, 1, 1, 5, 1, 5]
    got = dd_signals.percentile_elevated(d25, 0.25, lookback=4, min_obs=2)
    assert got == [False, False, False, False, True, False, True]


def test_percentile_uses_only_prior_values():
    base = [3, 4, 2, 5, 3, 2, 6, 1, 0, 2, 3, 4, 5, 6, 2, 1]
    changed = [*base[:10], 99, 0, 0, 0, 0, 0]
    a = dd_signals.percentile_elevated(base, 0.2, lookback=6, min_obs=3)
    b = dd_signals.percentile_elevated(changed, 0.2, lookback=6, min_obs=3)
    assert a[:10] == b[:10]  # values after t cannot change flags up to t


def test_no_lookahead_dd_series_and_flags(bars):
    h = _hist(bars, "ftd_rich")
    t = 450
    alt = _perturb_after(h, t)
    for sym in (SP, NQ):
        for decl, vr in [(-0.002, "prev_day"), (-0.01, "both"), (-0.005, "avg50")]:
            a = dd_signals.dd_series(h[sym], decl, vr)
            b = dd_signals.dd_series(alt[sym], decl, vr)
            for field in ("d5", "d15", "d25", "is_dd", "level", "dates"):
                assert getattr(a, field)[: t + 1] == getattr(b, field)[: t + 1], (sym, decl, vr)
            for rr in variants.RISK_RULES:
                fa = dd_signals.elevated_flags(a, rr)
                fb = dd_signals.elevated_flags(b, rr)
                assert fa[: t + 1] == fb[: t + 1], (sym, rr)


def test_percentile_flags_match_truncated_series(bars):
    """Flags computed on a series cut at t equal the flags on the full series up to t."""
    h = _hist(bars, "ftd_rich")[SP]
    full = dd_signals.dd_series(h, -0.002, "prev_day")
    t = 400
    cut = dd_signals.dd_series(h[: t + 1], -0.002, "prev_day")
    for rr in ("pctl20", "pctl10", "pctl5"):
        assert dd_signals.elevated_flags(full, rr)[: t + 1] == dd_signals.elevated_flags(cut, rr)


def test_no_lookahead_ftd_events(bars):
    h = _hist(bars, "ftd_rich")
    t = 450
    start = h[SP][MIN_I]["date"]
    cutoff = h[SP][t]["date"]
    alt = _perturb_after(h, t)
    keys = ("index", "ftd_date", "detected_on", "gain_pct", "volume_above_avg", "ftd_day_number")
    for c, g in [(3.0, 1.25), (5.0, 1.5), (3.0, 2.0)]:
        a = [e for e in ftd_signals.ftd_event_table(h, c, g, start) if e["detected_on"] <= cutoff]
        b = [e for e in ftd_signals.ftd_event_table(alt, c, g, start) if e["detected_on"] <= cutoff]
        assert [{k: e[k] for k in keys} for e in a] == [{k: e[k] for k in keys} for e in b]


def test_gain_and_depth_overrides_change_events(bars):
    h = _hist(bars, "ftd_rich")
    start = h[SP][MIN_I]["date"]
    base = ftd_signals.ftd_event_table(h, 3.0, 1.25, start)
    strict = ftd_signals.ftd_event_table(h, 3.0, 2.0, start)
    assert all(e["gain_pct"] >= 2.0 for e in strict)
    assert len(strict) <= len(base)
    deep = ftd_signals.ftd_event_table(h, 10.0, 1.25, start)
    assert isinstance(deep, list)
    assert {e["ftd_date"] for e in deep} != {e["ftd_date"] for e in base} or not base


def test_volume_post_filter():
    evs = [
        {"volume_above_avg": True},
        {"volume_above_avg": False},
        {"volume_above_avg": None},
    ]
    assert ftd_signals.apply_volume_filter(evs, False) == evs
    assert ftd_signals.apply_volume_filter(evs, True) == [{"volume_above_avg": True}]
