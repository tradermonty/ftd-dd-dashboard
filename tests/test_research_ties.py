"""H1: the documented tie rule (more design observations, then simpler parameters, then id)."""

from __future__ import annotations

from research import overlay, selection


def _a(vid, idx, n, med):
    return {"variant_id": vid, "index": idx, "n_trades": n, "median": med, "period": "design"}


def _b(vid, cagr, mdd, bh=0.08):
    return {
        "variant_id": vid,
        "index": "I",
        "period": "design",
        "cagr": cagr,
        "max_dd": mdd,
        "bh_cagr": bh,
    }


def _c(sid, ne, nn, spread):
    return {
        "signal_id": sid,
        "index": "I",
        "period": "design",
        "n_elevated": ne,
        "n_normal": nn,
        "spread": spread,
    }


def test_select_a_tie_prefers_more_trades_not_string_order():
    # the real case on ^GSPC: three variants tie on the design median; "ftd_c10" < "ftd_c3"
    # as a string, so the old rule picked the n=15 variant.
    rows = [
        _a("ftd_c10_g2_v1", "I", 15, 0.0123),
        _a("ftd_c3_g2_v1", "I", 29, 0.0123),
        _a("ftd_c8_g2_v1", "I", 17, 0.0123),
    ]
    assert selection.select_a(rows, "ftd_c3_g1.25_v0")["I"]["selected"] == "ftd_c3_g2_v1"


def test_select_a_tie_then_simpler_parameters_then_id():
    rows = [
        _a("ftd_c5_g1.5_v0", "I", 20, 0.02),
        _a("ftd_c3_g2_v0", "I", 20, 0.02),
        _a("ftd_c3_g1.5_v1", "I", 20, 0.02),
        _a("ftd_c3_g1.5_v0", "I", 20, 0.02),
    ]
    assert selection.select_a(rows, "x")["I"]["selected"] == "ftd_c3_g1.5_v0"
    # volume filter off beats on; smaller gain beats larger; smaller correction beats larger
    assert selection.select_a(rows[:3], "x")["I"]["selected"] == "ftd_c3_g1.5_v1"
    assert selection.select_a(rows[:2], "x")["I"]["selected"] == "ftd_c3_g2_v0"


def test_select_b_tie_prefers_parameters_closer_to_baseline():
    rows = [
        _b("dd_d-0.5_prev_day_skill_high_e0", 0.07, -0.30),
        _b("dd_d-0.2_avg50_skill_high_e50", 0.07, -0.30),
        _b("dd_d-0.2_prev_day_skill_high_e50", 0.07, -0.30),
        _b("hold", 0.08, -0.55),
    ]
    assert selection.select_b(rows, "hold")["I"]["selected"] == "dd_d-0.2_prev_day_skill_high_e50"


def test_select_c_tie_prefers_more_observations_then_simpler():
    rows = [
        _c("dd_d-0.5_prev_day_skill_severe", 300, 3000, 0.09),
        _c("dd_d-0.2_prev_day_skill_high", 300, 3000, 0.09),
        _c("dd_d-0.2_avg50_skill_high", 301, 3000, 0.09),
    ]
    assert selection.select_c(rows, "base")["I"]["selected"] == "dd_d-0.2_avg50_skill_high"
    assert selection.select_c(rows[:2], "base")["I"]["selected"] == "dd_d-0.2_prev_day_skill_high"


def test_post_hoc_amendment_is_recorded_and_tie_rule_stated():
    am = selection.POST_HOC_AMENDMENTS[0]
    assert am["date"] == "2026-11-07" and "tie" in am["what"] and am["reason"]
    assert "more design trades" in selection.TIE_RULE
    assert "id ascending" not in selection.CRITERIA["A"]
    # R2 records the same tie amendment plus its own borrow-rate timing amendment
    assert overlay.POST_HOC_AMENDMENTS[: len(selection.POST_HOC_AMENDMENTS)] == (
        selection.POST_HOC_AMENDMENTS
    )
    assert overlay.POST_HOC_AMENDMENTS[-1] is overlay.RATE_TIMING_AMENDMENT


def test_overlay_selection_tie_prefers_lower_leverage_then_fewer_dds_then_invalidation():
    base = {"index": "I", "period": "design", "cagr": 0.05, "max_dd": -0.4, "bh_max_dd": -0.4}
    ids = [
        overlay.variant_id(5, True, 1.5),
        overlay.variant_id(3, False, 1.25),
        overlay.variant_id(3, True, 1.25),
        overlay.variant_id(4, True, 1.25),
    ]
    rows = [{**base, "variant_id": v} for v in ids]
    assert overlay.select_overlay(rows)["I"]["selected"] == overlay.variant_id(3, True, 1.25)
    assert overlay.select_overlay(rows[:2])["I"]["selected"] == overlay.variant_id(3, False, 1.25)
    assert overlay.select_overlay(rows[:1])["I"]["selected"] == overlay.variant_id(5, True, 1.5)
