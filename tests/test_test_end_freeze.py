"""The frozen end of the test period (research.split.TEST_END), the forward record of research R,
the third test-period exception, the Home key-point additions and the renamed avg_exposure.

Synthetic histories for the pipeline properties; the real run (copied from build/) for the pages.
"""

from __future__ import annotations

import json
import re

import pytest

from research import contamination, overlay, pipeline, split
from tests.test_rereview import copy_run, overlay_json, selected_json, text_of  # noqa: F401
from tests.test_research_selection import _subset, _synthetic
from tests.test_site import built, read, real_run  # noqa: F401
from tests.webfixtures import build_run
from webgen import facts, i18n, layout, pages, validate

# ----------------------------------------------------------------------------- the constant


def test_the_test_end_is_a_frozen_date_constant():
    assert split.TEST_END == "2026-11-06"
    assert split.TEST_START < split.TEST_END
    assert overlay.FORWARD_AFTER == split.TEST_END  # R2 and R share the same frozen end
    assert split.day_after(split.TEST_END) == "2026-11-07"


# ------------------------------------------------------------------- pipeline: R and sessions after


def _run(h, test_end):
    f, d = _subset()
    return pipeline.run_pipeline(
        h,
        ftd_vars=f,
        dd_vars=d,
        design_start=h["^GSPC"][5]["date"],
        design_end=h["^GSPC"][600]["date"],
        test_start=h["^GSPC"][601]["date"],
        test_end=test_end,
    )


@pytest.fixture(scope="module")
def synthetic():
    return {"^GSPC": _synthetic(11), "^IXIC": _synthetic(12)}


def test_sessions_after_the_test_end_do_not_change_the_test_period(synthetic):
    end = synthetic["^GSPC"][800]["date"]
    longer = _run(synthetic, end)
    cut = {s: [b for b in bars if b["date"] <= end] for s, bars in synthetic.items()}
    shorter = _run(cut, end)
    assert longer["test_end"] == shorter["test_end"] == end
    assert longer["design"] == shorter["design"] and longer["chosen"] == shorter["chosen"]
    for ev in "ABC":  # the test rows are the same with or without the later sessions
        assert longer["test"][ev] == shorter["test"][ev]
    assert longer["last_date"] == synthetic["^GSPC"][-1]["date"]
    assert shorter["last_date"] == end


def test_forward_record_has_only_the_sessions_after_the_end(synthetic):
    end = synthetic["^GSPC"][800]["date"]
    res = _run(synthetic, end)
    fw = res["forward"]
    assert fw["after"] == end and fw["last"] == synthetic["^GSPC"][-1]["date"]
    assert fw["n_sessions"] == {"^GSPC": 99, "^IXIC": 99}
    for idx in ("^GSPC", "^IXIC"):
        for role, row in fw["B"][idx].items():
            assert row["n_days"] == 99 and row["first_date"] > end, (idx, role)
            assert row["cum_return"] is not None and "levered_days" not in row
        for row in fw["A"][idx].values():  # the same row layout as the test period
            assert row["period"] == "forward" and row["n_events"] >= 0
        for row in fw["C"][idx].values():
            assert row["period"] == "forward" and "p_elevated" in row
    # the baseline is always there; the pick only when it differs from the baseline
    assert set(fw["B"]["^GSPC"]) <= {"baseline", "selected"} and "baseline" in fw["B"]["^GSPC"]


def test_forward_record_is_empty_when_the_data_ends_at_the_test_end(synthetic):
    end = synthetic["^GSPC"][800]["date"]
    cut = {s: [b for b in bars if b["date"] <= end] for s, bars in synthetic.items()}
    fw = _run(cut, end)["forward"]
    assert fw["n_sessions"] == {"^GSPC": 0, "^IXIC": 0}
    for idx in ("^GSPC", "^IXIC"):
        for row in fw["B"][idx].values():
            assert row["n_days"] == 0 and row["cum_return"] is None
        for row in fw["A"][idx].values():
            assert row["n_events"] == 0 and row["n_trades"] == 0
        for row in fw["C"][idx].values():
            assert row["n_elevated"] == 0 and row["n_normal"] == 0


def test_forward_b_uses_the_same_return_code_as_the_test_period(synthetic):
    end = synthetic["^GSPC"][800]["date"]
    res = _run(synthetic, end)
    idx = "^GSPC"
    bars = synthetic[idx]
    sig, base = res["signals"], res["baseline"]["B"]
    f, d = _subset()
    v = next(x for x in d if x["id"] == base)
    flags = sig.flags(idx, v["min_decline_pct"], v["volume_rule"], v["risk_rule"])
    eq = 1.0
    for k in range(801, len(bars)):  # return days whose previous close is on or after the end
        e = v["exposure"] if flags[k - 1] else 1.0
        eq *= 1 + e * (bars[k]["close"] / bars[k - 1]["close"] - 1)
    got = res["forward"]["B"][idx]["baseline"]["cum_return"]
    assert got == pytest.approx(eq - 1)


def test_test_end_defaults_to_the_frozen_constant_and_never_exceeds_the_data(synthetic):
    res = _run(synthetic, None)  # the synthetic bars end in 2013: the data is shorter
    assert res["test_end"] == res["last_date"] < split.TEST_END
    assert res["forward"]["n_sessions"] == {"^GSPC": 0, "^IXIC": 0}


# ------------------------------------------------------------------------------- the gate


def test_gate_checks_the_frozen_end_not_the_newest_session(tmp_path):
    run = build_run(tmp_path / "run", through="2026-11-09", last="2026-11-09")
    res = validate._check_research(run, "2026-11-09")
    assert res["status"] == "FAIL"
    assert f"test end 2026-11-09 != {split.TEST_END}" in res["detail"]


def test_gate_accepts_the_frozen_end_when_the_data_runs_on(tmp_path):
    run = build_run(tmp_path / "run", through="2026-11-09", last="2026-11-09")
    for name in ("selected.json", "overlay_selected.json"):
        p = run / "research" / name
        d = json.loads(p.read_text())
        d["split"]["test"][1] = split.TEST_END
        p.write_text(json.dumps(d))
    res = validate._check_research(run, "2026-11-09")
    assert res["status"] == "PASS", res["detail"]
    assert "frozen end 2026-11-06" in res["detail"]


# ----------------------------------------------------------------- the real run: records and pages


def test_selected_json_records_the_frozen_end_the_forward_record_and_the_contamination(built):  # noqa: F811
    sel = selected_json(built)
    assert sel["split"]["test"] == [split.TEST_START, split.TEST_END]
    assert overlay_json(built)["split"]["test"] == [split.TEST_START, split.TEST_END]
    fw = sel["forward_record"]
    assert fw["after"] == split.TEST_END and set(fw) >= {"A", "B", "C", "n_sessions"}
    cd = sel["test_period_contamination"]
    assert cd["clean_out_of_sample"] is False and cd["date"] == "2026-11-07"
    assert cd["figures"]["dd_day_share"] == {"^GSPC": 0.198, "^IXIC": 0.205}
    assert cd["figures"]["p_drawdown_60d"]["^GSPC"] == {"NORMAL": 0.12, "SEVERE": 0.197}
    assert cd["figures"]["p_drawdown_60d"]["^IXIC"] == {"NORMAL": 0.264, "SEVERE": 0.235}
    assert "test_period_contamination" in overlay_json(built)  # R2's own is still there
    assert contamination.verify(built / "data" / "daily_status.csv") == []


@pytest.mark.parametrize("lang", layout.LANGS)
def test_research_page_has_the_forward_record_for_r_and_the_third_exception(built, lang):  # noqa: F811
    d = i18n.load(lang)
    plain = " ".join(text_of(built, f"{lang}/research.html").split())
    assert d["research.h_forward"] in plain
    assert d["research.ov_forward_none"] in plain  # "not started" until data exists
    assert d["research.r_contam_h"] in plain and d["research.r_contam_verdict"] in plain
    for fig in ("19.8%", "20.5%", "12.0%", "19.7%", "26.4%", "23.5%"):
        assert fig in plain, fig
    assert split.TEST_END in plain
    # three exceptions, never "two"
    note = d["research.conclusions_note"]
    assert ("三つ" in note) if lang == "ja" else ("three exceptions" in note)
    assert "例外が二つ" not in note and "two exceptions" not in note


@pytest.mark.parametrize("lang", layout.LANGS)
def test_home_key_point_note_names_all_three_exceptions(built, lang):  # noqa: F811
    home = " ".join(text_of(built, f"{lang}/index.html").split())
    note = " ".join(i18n.load(lang)["research.conclusions_note"].split())
    assert note in home


def test_no_leftover_two_exceptions_wording_anywhere():
    for lang in layout.LANGS:
        for key, text in i18n.load(lang).items():
            assert "two exceptions" not in text and "例外が二つ" not in text, key
            assert "with two exceptions" not in text, key


def test_report_html_has_the_third_exception_the_forward_record_and_no_run_time(built):  # noqa: F811
    text = (built / "research" / "report.html").read_text(encoding="utf-8")
    assert "三つ目の例外" in text and "R の今後の記録" in text and "記録開始前" in text
    assert "実行時間" not in text
    assert f"検証期間 (test) {split.TEST_START} 〜 {split.TEST_END}" in text
    assert "19.8%" in text and "26.4%" in text


# ------------------------------------------------------------ Home bullets: overlap, dropped, R2


def test_ftd_trade_bullets_state_dropped_events_and_the_overlap(built):  # noqa: F811
    f = facts.load_facts(built)
    kf = pages.key_facts(f)
    for lang in layout.LANGS:
        c = pages.Ctx(lang, f)
        home = " ".join(text_of(built, f"{lang}/index.html").split())
        for idx, d in kf["a"].items():
            t = d["test"]
            ov = kf["overlap"][idx]
            bullet = pages._k_a(c, idx, t, ov, kf["horizon"])
            assert " ".join(bullet.split()) in home
            assert str(t["n_events"]) in bullet and str(t["n_trades"]) in bullet
            assert str(ov["k"]) in bullet and str(ov["n"]) in bullet
            dropped = t["n_dropped_incomplete_window"]
            assert (c.t("research.k_a_dropped", n=dropped, h=kf["horizon"]) in bullet) == bool(
                dropped
            )
            # every event is a trade, was dropped, or was skipped
            assert t["n_events"] == t["n_trades"] + dropped + t["n_skipped_invalidated_at_entry"]
            assert ov["n"] == t["n_events"]  # the overlap counts the same events
    # the real data: one Nasdaq event has no complete 60-session window (63 events, 62 trades)
    nas = kf["a"]["^IXIC"]["test"]
    assert (nas["n_events"], nas["n_trades"], nas["n_dropped_incomplete_window"]) == (63, 62, 1)


# ---------------------------------------------------------------- avg_exposure rename and compat


def test_avg_exposure_replaces_time_invested_everywhere(built):  # noqa: F811
    sel = selected_json(built)
    b = sel["evaluations"]["B"]["^GSPC"]["test"]["baseline"]
    assert "avg_exposure" in b and "time_invested" not in b
    assert "time_invested" not in (built / "research" / "selected.json").read_text()
    header = (built / "research" / "variants.csv").read_text().splitlines()[0].split(",")
    assert "B_avg_exposure" in header and "B_time_invested" not in header
    assert "avg_exposure" in (built / "research" / "test_results.csv").read_text().splitlines()[0]
    assert "time_invested" not in (built / "research" / "report.html").read_text(encoding="utf-8")


def test_readers_still_understand_the_old_key():
    old = {"evaluations": {"B": {"^GSPC": {"test": {"baseline": {"time_invested": 0.7}}}}}}
    new = facts.rename_legacy_keys(old)
    assert new["evaluations"]["B"]["^GSPC"]["test"]["baseline"] == {"avg_exposure": 0.7}
    assert facts.rename_legacy_keys([{"time_invested": 1}, 2]) == [{"avg_exposure": 1}, 2]


# ----------------------------------------------------------------------- docs


def test_plan_documents_the_frozen_end_the_third_exception_and_the_freeze_dates():
    plan = (
        split.__file__
        and __import__("pathlib").Path(split.__file__).parents[1] / "docs" / "plan.md"
    )
    text = plan.read_text(encoding="utf-8")
    assert "TEST_END" in text and "2026-11-06" in text
    assert "third exception" in text
    assert "UTC" in text and "04:06:20Z" in text
    assert re.search(r"[Pp]review", text)
    assert "daily_status" in text  # the full-recomputation gate is described
