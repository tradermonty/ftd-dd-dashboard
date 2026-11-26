"""P3 acceptance: event extraction equals direct vendored calls; no look-ahead; DD expiry."""

from __future__ import annotations

import copy

import pytest

from engine import dd_glue, events, loader, replay

SP, NQ = replay.SP500, replay.NASDAQ
MIN_I = 100
KEY = {SP: "sp500", NQ: "nasdaq"}


def _hist(bars, seg):
    return {SP: bars[seg][SP], NQ: bars[seg][NQ]}


@pytest.fixture(scope="module")
def rich(bars):
    h = _hist(bars, "ftd_rich")
    rows = replay.replay(h, start=h[SP][MIN_I]["date"], keep_raw=True)
    return h, rows


@pytest.fixture(scope="module")
def rich_events(rich):
    h, rows = rich
    return events.ftd_events(rows, h)


def _direct_state(h, i):
    mods = loader.load_ftd_modules()
    sp80, nq80 = h[SP][i - 79 : i + 1], h[NQ][i - 79 : i + 1]
    state = mods.rally_tracker.get_market_state(list(reversed(sp80)), list(reversed(nq80)))
    return mods.post_ftd_monitor.assess_post_ftd_health(state, sp80, nq80)


def _dates(h):
    return [b["date"] for b in h[SP]]


# ---------------------------------------------------------------- FTD events


def test_fixture_exercises_ftd_events(rich_events):
    assert {e["index"] for e in rich_events} == {SP, NQ}
    assert len(rich_events) >= 3


def test_event_columns(rich_events):
    assert list(rich_events[0].keys()) == events.FTD_EVENT_COLUMNS


def test_one_event_per_distinct_ftd_date(rich, rich_events):
    _, rows = rich
    for sym in (SP, NQ):
        first = min(r["date"] for r in rows if r["index"] == sym)
        seen = {
            r["ftd_date"]
            for r in rows
            if r["index"] == sym and r["ftd_date"] and r["ftd_date"] >= first
        }
        got = [e["ftd_date"] for e in rich_events if e["index"] == sym]
        assert sorted(got) == sorted(seen)
        assert len(got) == len(set(got))  # keyed by (index, ftd_date)


def test_events_equal_direct_vendored_calls(rich, rich_events):
    """For days around each FTD, derived fields == direct call on that day's window."""
    h, _ = rich
    dates = _dates(h)
    checked = 0
    for e in rich_events:
        i = dates.index(e["detected_on"])
        state = _direct_state(h, i)
        s = state[KEY[e["index"]]]
        ftd = s["ftd"]
        assert ftd["ftd_detected"] and ftd["ftd_date"] == e["ftd_date"]
        assert e["ftd_day_number"] == ftd["ftd_day_number"]
        assert e["gain_pct"] == ftd["gain_pct"]
        assert e["volume_above_avg"] == ftd["volume_above_avg"]
        assert e["volume_vs_prev_pct"] == pytest.approx(
            (ftd["volume"] / ftd["prev_day_volume"] - 1) * 100
        )
        assert e["rally_day1_date"] == s["rally_attempt"]["day1_date"]
        assert e["swing_low_date"] == s["swing_low"]["swing_low_date"]
        assert e["invalidated_at_detection"] == (s["state"] == "FTD_INVALIDATED")
        assert e["lag_sessions"] == i - dates.index(e["ftd_date"])
        # the day before: this ftd_date was not yet reported (detected_on is the FIRST day)
        for j in range(MIN_I, i):
            prev = _direct_state(h, j)[KEY[e["index"]]]
            pf = prev.get("ftd") or {}
            assert not (pf.get("ftd_detected") and pf["ftd_date"] == e["ftd_date"]), (j, e)
        checked += 1
    assert checked == len(rich_events)


def test_invalidation_fields_equal_direct_check(rich, rich_events):
    h, _ = rich
    dates = _dates(h)
    post = loader.load_ftd_modules().post_ftd_monitor
    n_inv = 0
    for e in rich_events:
        chrono = h[e["index"]]
        fi = dates.index(e["ftd_date"])
        bounded = chrono[: fi + 61]  # 60 sessions after the FTD
        r = post.check_ftd_invalidation(bounded, fi)
        if r["invalidated"]:
            assert e["invalidated_within_60"] is True
            assert e["invalidation_date"] == r["invalidation_date"]
            assert dates.index(r["invalidation_date"]) - fi <= 60
            n_inv += 1
        elif len(chrono) - 1 - fi >= 60:
            assert e["invalidated_within_60"] is False
            assert e["invalidation_date"] is None
        else:  # fewer than 60 future sessions and not yet invalidated: undetermined
            assert e["invalidated_within_60"] is None
        # an invalidation later than 60 sessions must not leak into the event
        full = post.check_ftd_invalidation(chrono, fi)
        if full["invalidated"] and dates.index(full["invalidation_date"]) - fi > 60:
            assert e["invalidated_within_60"] is False
    assert n_inv > 0


def test_window_exit_does_not_create_event(rich):
    """A state change where ftd_date goes away (window exit) adds no event."""
    h, rows = rich
    exits = 0
    for sym in (SP, NQ):
        prev = None
        for r in (x for x in rows if x["index"] == sym):
            if prev and prev["ftd_date"] and r["ftd_date"] != prev["ftd_date"]:
                exits += 1
            prev = r
    assert exits > 0, "fixture should contain at least one FTD leaving the window"
    evs = events.ftd_events(rows, h)
    for sym in (SP, NQ):
        first = min(r["date"] for r in rows if r["index"] == sym)
        distinct = {
            r["ftd_date"]
            for r in rows
            if r["index"] == sym and r["ftd_date"] and r["ftd_date"] >= first
        }
        assert len([e for e in evs if e["index"] == sym]) == len(distinct)


def _row(d, sym, ftd_date, state="FTD_CONFIRMED", day1="2020-01-02"):
    ftd = (
        {
            "ftd_detected": True,
            "ftd_date": ftd_date,
            "ftd_day_number": 4,
            "gain_pct": 1.5,
            "volume": 120,
            "prev_day_volume": 100,
            "volume_above_avg": True,
        }
        if ftd_date
        else None
    )
    return {
        "date": d,
        "index": sym,
        "ftd_date": ftd_date,
        "ftd_state": state,
        "rally_day1_date": day1,
        "swing_low_date": "2020-01-01",
        "_ftd": ftd,
        "_dd": [],
    }


def _bars(n, start=0):
    return [
        {
            "date": f"2020-01-{k + 1:02d}",
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.0,
            "volume": 1000,
        }
        for k in range(start, n)
    ]


def test_synthetic_window_exit_and_return_creates_no_event():
    h = {SP: _bars(12), NQ: _bars(12)}
    d = _dates(h)
    rows = [
        _row(d[5], SP, None, "RALLY_ATTEMPT"),
        _row(d[6], SP, d[5]),
        _row(d[7], SP, d[5]),
        _row(d[8], SP, None, "CORRECTION"),  # FTD left the window
        _row(d[9], SP, d[5]),  # (re)appears: same key -> still one event
    ]
    evs = events.ftd_events(rows, h)
    assert [(e["ftd_date"], e["detected_on"]) for e in evs] == [(d[5], d[6])]
    assert evs[0]["lag_sessions"] == 1


def test_rally_attempts_first_appearance(rich):
    h, rows = rich
    ra = events.rally_attempts(rows, _dates(h))
    for sym in (SP, NQ):
        mine = [r for r in ra if r["index"] == sym]
        days = [r["rally_day1_date"] for r in mine]
        assert len(days) == len(set(days))
        for r in mine:
            first = next(
                x
                for x in rows
                if x["index"] == sym and x["rally_day1_date"] == r["rally_day1_date"]
            )
            assert r["detected_on"] == first["date"]
            assert r["lag_sessions"] >= 0
    assert ra


# ------------------------------------------------------------- look-ahead


@pytest.mark.parametrize("cut", [250, 330, 450])
def test_events_unchanged_by_future_bars(bars, cut):
    h = _hist(bars, "ftd_rich")
    start = h[SP][MIN_I]["date"]
    base_rows = replay.replay({s: h[s][:cut] for s in (SP, NQ)}, start=start, keep_raw=True)
    base = events.ftd_events(base_rows, {s: h[s][:cut] for s in (SP, NQ)})

    tampered = copy.deepcopy(h)
    for s in (SP, NQ):
        for bar in tampered[s][cut:]:
            bar.update(
                close=bar["close"] * 0.3,
                high=bar["high"] * 0.3,
                low=bar["low"] * 0.3,
                volume=bar["volume"] * 7,
            )
    rows_t = replay.replay(tampered, start=start, keep_raw=True)
    after = events.ftd_events(rows_t, tampered)
    last = h[SP][cut - 1]["date"]
    keys = ("index", "ftd_date", "detected_on", "lag_sessions", "invalidated_at_detection")
    keys += ("rally_day1_date", "gain_pct", "ftd_day_number")
    a = {(e["index"], e["ftd_date"]): {k: e[k] for k in keys} for e in base}
    b = {
        (e["index"], e["ftd_date"]): {k: e[k] for k in keys}
        for e in after
        if e["detected_on"] <= last
    }
    assert a == b


# ---------------------------------------------------------- distribution days


@pytest.fixture(scope="module")
def rich_dd(rich):
    h, rows = rich
    return events.distribution_days(rows, _dates(h))


def test_dd_one_row_per_index_date_and_equals_is_dd(rich, rich_dd):
    _, rows = rich
    for sym in (SP, NQ):
        flagged = {r["date"] for r in rows if r["index"] == sym and r["is_dd"]}
        got = [r["date"] for r in rich_dd if r["index"] == sym]
        assert sorted(got) == sorted(flagged) and len(got) == len(set(got))
    assert list(rich_dd[0].keys()) == events.DD_COLUMNS


def test_dd_expires_on_is_26th_session(rich, rich_dd):
    h, _ = rich
    dates = _dates(h)
    n = len(dates)
    for r in rich_dd:
        k = dates.index(r["date"])
        if k + 26 < n:
            assert r["expires_on"] == dates[k + 26]
        else:
            assert r["expires_on"] is None


def test_dd_expiry_matches_vendored_status(rich, rich_dd):
    """At expires_on the vendored record is 'expired' (age 26 > 25); a session before it is not."""
    h, _ = rich
    dates = _dates(h)
    dd = loader.load_dd_modules()
    rule = dd_glue.build_rule()
    checked = 0
    for r in rich_dd:
        if r["expires_on"] is None or r["invalidated_on"]:
            continue
        for offset, expected in ((-1, "active"), (0, "expired")):
            t = dates.index(r["expires_on"]) + offset
            if t < 84:
                continue
            hist = list(reversed(h[r["index"]][t - 84 : t + 1]))
            recs, _ = dd.tracker.detect_distribution_days(hist, rule)
            recs = dd.tracker.enrich_records(recs, hist, rule)
            rec = next(x for x in recs if x.date == r["date"])
            assert rec.status == expected, (r, offset)
            assert rec.age_sessions == (25 if offset == -1 else 26)
        checked += 1
    assert checked > 5


def test_dd_invalidation_matches_vendored(rich, rich_dd):
    h, _ = rich
    dates = _dates(h)
    dd = loader.load_dd_modules()
    rule = dd_glue.build_rule()
    inv = [r for r in rich_dd if r["invalidated_on"]]
    assert inv, "fixture should contain invalidated DDs"
    for r in inv[:10]:
        assert r["removal_reason"] == "invalidated_5pct_gain"
        t = dates.index(r["invalidated_on"])
        hist = list(reversed(h[r["index"]][t - 84 : t + 1]))
        recs, _ = dd.tracker.detect_distribution_days(hist, rule)
        recs = dd.tracker.enrich_records(recs, hist, rule)
        rec = next(x for x in recs if x.date == r["date"])
        assert rec.status == "invalidated"
        # first such day: the day before it was not invalidated
        hist = list(reversed(h[r["index"]][t - 85 : t]))
        recs, _ = dd.tracker.detect_distribution_days(hist, rule)
        recs = dd.tracker.enrich_records(recs, hist, rule)
        prev = next(x for x in recs if x.date == r["date"])
        assert prev.status != "invalidated"


def test_dd_removal_reason_expired(rich_dd):
    exp = [r for r in rich_dd if not r["invalidated_on"] and r["expires_on"]]
    assert exp and all(r["removal_reason"] == "expired_25_sessions" for r in exp)


def test_no_event_before_replay_start(rich, rich_events):
    """An FTD dated before the first replay session cannot be detected point-in-time:
    its apparent lag is an artifact of where the replay starts, so it is excluded."""
    _, rows = rich
    first = min(r["date"] for r in rows)
    assert all(e["ftd_date"] >= first for e in rich_events)


def test_pre_start_ftd_is_dropped_but_later_kept(rich):
    h, rows = rich
    first = min(r["date"] for r in rows)
    fake = [dict(r) for r in rows if r["index"] == SP][:3]
    fake[0]["ftd_date"] = h[SP][MIN_I - 5]["date"]
    for r in fake[1:]:
        r["ftd_date"] = None
    assert fake[0]["ftd_date"] < first
    assert events.ftd_events(fake, h) == []


# ------------------------------------------- rally attempts: replay-start rule (finding 4)


def test_rally_attempts_exclude_day1_before_replay_start():
    h = {SP: _bars(12), NQ: _bars(12)}
    d = _dates(h)
    rows = [
        # first replay day is d[5]; this rally started (day1) before it -> excluded
        _row(d[5], SP, None, "RALLY_ATTEMPT", day1=d[2]),
        _row(d[6], SP, None, "RALLY_ATTEMPT", day1=d[2]),
        _row(d[7], SP, None, "RALLY_ATTEMPT", day1=d[7]),  # starts inside the replay -> kept
        _row(d[8], SP, None, "RALLY_ATTEMPT", day1=d[7]),
    ]
    ra = events.rally_attempts(rows, d)
    assert [(r["rally_day1_date"], r["detected_on"]) for r in ra] == [(d[7], d[7])]


def test_rally_attempts_day1_equal_to_replay_start_is_kept():
    h = {SP: _bars(12), NQ: _bars(12)}
    d = _dates(h)
    rows = [_row(d[5], SP, None, "RALLY_ATTEMPT", day1=d[5])]
    assert len(events.rally_attempts(rows, d)) == 1


def test_rally_attempts_no_pre_start_rows_on_real_replay(rich):
    h, rows = rich
    for sym in (SP, NQ):
        first = min(r["date"] for r in rows if r["index"] == sym)
        assert all(
            r["rally_day1_date"] >= first
            for r in events.rally_attempts(rows, _dates(h))
            if r["index"] == sym
        )


def test_rally_detected_on_semantics_documented():
    doc = events.rally_attempts.__doc__
    assert "first day the rally appeared in the replay" in doc
    assert "not a point-in-time" in doc


# ----------------------------------------- DD expiry bounded by the replay (finding 6)


def _truncate(rows, last):
    return [r for r in rows if r["date"] <= last]


def test_dd_expiry_not_asserted_beyond_last_replay_row(rich):
    h, rows = rich
    dates = _dates(h)
    last = dates[300]
    cut = _truncate(rows, last)
    dd = events.distribution_days(cut, dates)  # session axis still extends past `last`
    assert dd
    for r in dd:
        if r["invalidated_on"]:
            continue
        # expiry is only reported if it falls inside the replayed range
        if r["expires_on"] is None:
            assert r["removal_reason"] is None
        else:
            assert r["expires_on"] <= last
            assert r["removal_reason"] == "expired_25_sessions"
    # at least one DD in the last 25 sessions is still live at the cut
    assert any(
        not r["invalidated_on"] and r["expires_on"] is None and r["date"] > dates[270] for r in dd
    )


def test_dd_invalidated_keeps_expires_on_and_reports_invalidation_only(rich):
    h, rows = rich
    dates = _dates(h)
    full = events.distribution_days(rows, dates)
    cut = events.distribution_days(_truncate(rows, dates[300]), dates)
    inv = [r for r in cut if r["invalidated_on"]]
    assert inv
    by_key = {(r["index"], r["date"]): r for r in full}
    for r in inv:
        assert r["removal_reason"] == "invalidated_5pct_gain"
        assert r["expires_on"] == by_key[(r["index"], r["date"])]["expires_on"]
        assert r["expires_on"] is not None
