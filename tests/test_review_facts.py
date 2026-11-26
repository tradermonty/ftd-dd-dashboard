"""Review fixes H2-H3, M1-M2, M5, L1, L3, L4: facts and page helpers on synthetic data."""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from engine import fetch
from webgen import facts, pages


def mk_facts(**kw) -> facts.Facts:
    base = dict(
        run=Path("."),
        summary={"data_range": {"last": "2026-11-06", "replay_first": "2000-01-03"}},
        selected={},
        status=[],
        ftd=[],
        dd=[],
        outcomes=[],
        bucket=[],
        risk=[],
        issues=[],
    )
    base.update(kw)
    return facts.Facts(**base)


def status_rows(n=200, start="2015-01-02"):
    dates = [d.strftime("%Y-%m-%d") for d in pd.bdate_range(start, periods=n)]
    return [
        {"index": "^GSPC", "date": d, "d25": "3", "risk_level": "NORMAL", "is_dd": "False"}
        for d in dates
    ], dates


# ---- H3 --------------------------------------------------------------------------------


def test_base_rates_share_of_severe_days_not_reached_via_d25():
    spec = [(7, "SEVERE"), (6, "SEVERE"), (5, "SEVERE"), (3, "SEVERE"), (2, "NORMAL"), (4, "HIGH")]
    rows = [
        {
            "index": "^GSPC",
            "date": f"2020-01-0{i + 1}",
            "d25": str(d25),
            "risk_level": lv,
            "is_dd": "False",
        }
        for i, (d25, lv) in enumerate(spec)
    ]
    b = facts.base_rates(mk_facts(status=rows))[0]
    assert (b["severe_days"], b["severe_not_d25"], b["share_severe_not_d25"]) == (4, 2, 0.5)
    assert b["share_d25_severe"] == 2 / 6  # d25 >= 6 on two of six days
    assert b["window"] == 26 and abs(b["expected_window"] - 26 * b["dd_share"]) < 1e-12


# ---- M5 --------------------------------------------------------------------------------


def test_ftd_overlap_counts_test_period_ftds_with_a_neighbour_within_60_sessions():
    rows, dates = status_rows()
    ftd = [
        {"index": "^GSPC", "detected_on": dates[d], "ftd_date": dates[d]}
        for d in (10, 50, 150, 190)
    ]
    # 10 and 50 are 40 apart; 150 is 100 from 50 but 40 from 190
    out = facts.ftd_overlap(mk_facts(status=rows, ftd=ftd), horizon=60)[0]
    assert (out["n"], out["k"], out["h"]) == (4, 4, 60)
    out = facts.ftd_overlap(mk_facts(status=rows, ftd=ftd[:3]), horizon=60)[0]
    assert (out["n"], out["k"]) == (3, 2)  # the one at 150 has no neighbour


def test_ftd_overlap_ignores_pre_test_events_in_the_count_but_not_as_neighbours():
    rows, dates = status_rows(start="2014-11-03", n=120)
    test_first = next(i for i, d in enumerate(dates) if d >= "2015-01-02")
    ftd = [{"index": "^GSPC", "detected_on": dates[d]} for d in (test_first - 5, test_first + 5)]
    out = facts.ftd_overlap(mk_facts(status=rows, ftd=ftd), horizon=60)[0]
    assert (out["n"], out["k"]) == (1, 1)


# ---- L3 / L4 ----------------------------------------------------------------------------


def test_same_volume_count_counts_only_that_issue_type():
    issues = [
        {"symbol": "^GSPC", "date": "2004-04-01", "issue": "volume_equals_previous_day"},
        {"symbol": "^GSPC", "date": "2004-04-02", "issue": "zero_volume"},
        {"symbol": "^IXIC", "date": "2004-04-03", "issue": "missing_volume"},
        {"symbol": "^IXIC", "date": "2004-04-04", "issue": "volume_equals_previous_day"},
    ]
    assert facts.same_volume_count(mk_facts(issues=issues)) == 2


def test_scheduled_expiry_is_26_sessions_after_on_the_xnys_calendar():
    cal = fetch.get_xnys()
    d = "2026-11-06"
    got = facts.scheduled_expiry(d)
    assert got == cal.session_offset(d, 26).strftime("%Y-%m-%d") and got > d
    sessions = cal.sessions_in_range(d, got)
    assert len(sessions) == 27  # the DD day plus 26 sessions


# ---- L1 ---------------------------------------------------------------------------------


def test_gap_is_computed_from_unrounded_values_and_operands_are_made_consistent():
    # Rule: the gap comes from the unrounded values, rounded once. 10.48% and 5.64% print as
    # 10.5% and 5.6% at one decimal, which differ by 4.9 while the true gap is 4.84 (4.8).
    a, b = 0.1048, 0.0564
    assert pages.pct(a) == "10.5%" and pages.pct(b) == "5.6%"
    assert pages.pdiff(a, b) == "4.8"
    # so the operands are shown with one more decimal, where they agree with the gap
    d = pages.gap_digits((a, b))
    assert d == 2 and pages.pct(a, d) == "10.48%" and pages.pct(b, d) == "5.64%"
    assert pages.pdiff(a, b, d) == "4.84"
    assert abs(pages.shown(a, d) - pages.shown(b, d) - 4.84) < 1e-9


def test_gap_digits_stays_at_one_decimal_when_the_numbers_already_agree():
    assert pages.gap_digits((0.20, 0.10)) == 1
    assert pages.gap_digits((0.114, 0.114)) == 1
    assert pages.gap_digits((0.2, None)) == 1  # a missing value is ignored
    # a gap that rounds to 0.0 while the printed values are equal needs no more decimals
    assert pages.gap_digits((0.12004, 0.11996)) == 1
    # printed equal (12.0% and 12.0%) but a gap that rounds to 0.1: show more decimals
    d = pages.gap_digits((0.12049, 0.11951))
    assert d == 2 and pages.pct(0.12049, d) != pages.pct(0.11951, d)


def test_gap_words_use_the_unrounded_gap_and_the_chosen_decimals():
    ja = pages.Ctx("ja", mk_facts())
    d = pages.gap_digits((0.1048, 0.0564))
    assert ja.gap(0.1048, 0.0564, d=d) == "より4.84ポイント高いです"
    assert ja.gap(0.1048, 0.0564) == "より4.8ポイント高いです"


def test_cmp_uses_displayed_precision():
    c = pages.Ctx("en", mk_facts())
    assert c.cmp(0.10001, 0.10004, d=1) == c.t("cmp.equal")
    assert c.cmp(0.1052, 0.1048, d=2) == c.t("cmp.higher")


# ---- M1 ---------------------------------------------------------------------------------


def test_ordering_labels_are_rises_falls_or_none():
    assert pages.ordering([0.10, 0.15, 0.20, 0.30]) == "rises"
    assert pages.ordering([0.10, 0.105, 0.12, 0.30]) == "rises"  # small steps still rise
    assert pages.ordering([0.30, 0.20, 0.15, 0.10]) == "falls"  # highest at NORMAL
    assert pages.ordering([0.10, 0.09, 0.14, 0.20]) == "none"  # CAUTION below NORMAL
    assert pages.ordering([0.20, 0.18, 0.15, 0.25]) == "none"  # SEVERE turns up again
    assert pages.ordering([0.10, 0.10, 0.20, 0.30]) == "none"  # equal neighbours
    assert pages.ordering([0.10, None, 0.2, 0.3]) == "none"


# ---- M2 ---------------------------------------------------------------------------------


def _sel_b(idx, sel, base, t_cagr, bh):
    row = {"cagr": t_cagr, "bh_cagr": bh}
    return {
        "baseline": base,
        "selected": sel,
        "design": {"baseline": row, "selected": row},
        "test": {"baseline": {"cagr": bh, "bh_cagr": bh}, "selected": row},
    }


def test_b_constraint_violation_is_detected_from_the_data():
    sel = {
        "evaluations": {
            "B": {
                "^GSPC": _sel_b("^GSPC", "v1", "base", 0.060, 0.081),  # 2.1 points below
                "^IXIC": _sel_b("^IXIC", "v2", "base", 0.070, 0.081),  # 1.1 points below
            }
        }
    }
    out = facts.b_constraint_violations(mk_facts(selected=sel))
    assert [v["index"] for v in out] == ["^GSPC"] and out[0]["id"] == "v1"
    same = {"evaluations": {"B": {"^GSPC": _sel_b("^GSPC", "base", "base", 0.01, 0.08)}}}
    assert facts.b_constraint_violations(mk_facts(selected=same)) == []
