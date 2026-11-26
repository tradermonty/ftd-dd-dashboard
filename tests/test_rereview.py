"""Verified re-review findings (R2 contamination, tie-break amendment, gate hardening, wording).

Uses the real research outputs in build/research and the cached price history (no network).
"""

from __future__ import annotations

import csv
import hashlib
import html as htmllib
import json
import re
import shutil

import pytest

from engine import fetch
from research import contamination, freeze, overlay, recheck, selection
from tests.test_site import built, read, real_run  # noqa: F401  (fixtures)
from webgen import facts, i18n, layout, pages, validate

# --------------------------------------------------------------------------- helpers


def text_of(run, rel) -> str:
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", read(run, rel)))


def overlay_json(run) -> dict:
    return json.loads((run / "research" / "overlay_selected.json").read_text())


def selected_json(run) -> dict:
    return json.loads((run / "research" / "selected.json").read_text())


@pytest.fixture
def copy_run(built, tmp_path):  # noqa: F811
    """A private copy of the built run directory that a test may damage."""
    dest = tmp_path / "run"
    shutil.copytree(built, dest)
    return dest


def _no_network(url, params):
    raise fetch.FetchError("cache miss")


@pytest.fixture(scope="module")
def fetched(built):  # noqa: F811
    """(histories, issues) from the local fetch cache; skipped without the cache."""
    try:
        return recheck.cached_histories(validate.last_session_date(built))
    except fetch.FetchError:
        pytest.skip("raw price cache not present")


@pytest.fixture(scope="module")
def histories(fetched):
    return fetched[0]


@pytest.fixture(scope="module")
def fresh(built, fetched, tmp_path_factory):  # noqa: F811
    """The full recomputation of the real run (about 20 seconds), made once for the module."""
    out = tmp_path_factory.mktemp("fresh")
    recheck.regenerate(fetched[0], fetched[1], built / "research" / "fred_DFF.csv", out)
    return out


# ------------------------------------------------------------ item 1: test-period contamination


def test_overlay_json_records_the_contamination_with_date_and_figures(built):  # noqa: F811
    cd = overlay_json(built)["test_period_contamination"]
    assert cd["date"] == "2026-11-07" and cd["clean_out_of_sample"] is False
    res = {(r["index"], tuple(r["period"])): r for r in cd["preview"]["results"]}
    assert res[("^GSPC", (2000, 2014))]["preview_cagr"] == 0.021
    assert res[("^GSPC", (2000, 2014))]["buy_hold_cagr"] == 0.023
    assert res[("^GSPC", (2015, 2026))]["preview_cagr"] == 0.114
    assert res[("^IXIC", (2000, 2014))]["preview_cagr"] == -0.008
    assert res[("^IXIC", (2000, 2014))]["buy_hold_cagr"] == 0.009
    assert res[("^IXIC", (2015, 2026))]["preview_cagr"] == 0.172
    assert res[("^IXIC", (2015, 2026))]["buy_hold_cagr"] == 0.149
    assert cd["preview"]["leverage"] == 1.5 and cd["preview"]["borrow_rate"] == 0.05
    assert cd["preview"]["levered_share_range"] == [0.10, 0.20]
    assert cd["forward_holdout"]["data_after"] == "2026-11-06"
    assert "preview" in cd["description"]


@pytest.mark.parametrize("lang", layout.LANGS)
def test_research_page_discloses_the_preview_in_the_r2_chapter(built, lang):  # noqa: F811
    plain = " ".join(text_of(built, f"{lang}/research.html").split())
    for fig in ("2.1%", "2.3%", "11.4%", "-0.8%", "0.9%", "17.2%", "14.9%", "150%", "5%"):
        assert fig in plain, fig
    assert "2026-11-07" in plain
    assert i18n.load(lang)["research.ov_contam_h"] in plain
    assert i18n.load(lang)["research.ov_contam_verdict"] in plain


def test_no_claim_that_the_r2_rule_was_fixed_before_any_result():
    en, ja = i18n.load("en"), i18n.load("ja")
    for k in ("research.ov_m_criterion", "research.ov_m_split"):
        assert "before any result" not in en[k]
        assert "結果を見る前" not in ja[k]
    assert "own computations" in en["research.ov_m_criterion"]
    assert "R2 自身の計算の前" in ja["research.ov_m_criterion"]
    assert "before any result" not in overlay.CRITERION
    assert "FIXED BEFORE R2's OWN COMPUTATIONS" in overlay.__doc__


def test_report_html_discloses_the_preview(built):  # noqa: F811
    text = (built / "research" / "report.html").read_text(encoding="utf-8")
    assert "検証期間は下見で見ていた" in text
    for fig in ("2.1%", "2.3%", "17.2%", "14.9%", "2026-11-07"):
        assert fig in text
    assert "選ぶ期間" not in text and "確かめる期間" not in text


def test_forward_record_is_empty_until_new_sessions_exist(built):  # noqa: F811
    ov = overlay_json(built)
    for info in ov["indices"].values():
        fw = info["forward"]
        assert fw["after"] == "2026-11-06"
        for role in [r for r in ("baseline", "selected") if r in fw]:
            assert fw[role]["n_days"] == 0 and fw[role]["cum_return"] is None
    for lang in layout.LANGS:
        assert i18n.load(lang)["research.ov_forward_none"] in read(built, f"{lang}/research.html")


def _bars(n=12):
    dates = [f"2030-01-{d:02d}" for d in range(1, n + 1)]
    closes = [100 + i for i in range(n)]
    return dates, closes


def test_forward_record_counts_only_sessions_after_the_cutoff_with_the_same_code():
    dates, closes = _bars()
    exp = [1.0] * 4 + [1.5] * 8
    rates = [0.04] * 12
    empty = overlay.forward_record(dates, closes, exp, rates, dates[-1], dates[-1])
    assert empty["n_days"] == 0 and empty["cum_return"] is None
    fw = overlay.forward_record(dates, closes, exp, rates, dates[7], dates[-1])
    assert fw["n_days"] == 4 and fw["first_date"] == dates[8] and fw["last_date"] == dates[-1]
    r = overlay.strategy_returns(dates, closes, exp, rates, dates[7], dates[-1])
    prod = 1.0
    for x in r["ret"]:
        prod *= 1 + x
    assert fw["cum_return"] == pytest.approx(prod - 1)
    assert fw["bh_cum_return"] == pytest.approx(closes[-1] / closes[7] - 1)
    assert fw["levered_days"] == 4
    # the same figures as evaluate() over the same window (shared code)
    m = overlay.evaluate(dates, closes, exp, rates, dates[7], dates[-1])
    assert m["n_days"] == fw["n_days"] and m["max_dd"] == pytest.approx(fw["max_dd"])


def test_forward_record_grows_when_a_session_is_added():
    dates, closes = _bars(12)
    exp, rates = [1.0] * 13, [0.04] * 13
    d13 = [*dates, "2030-01-13"]
    c13 = [*closes, 120.0]
    before = overlay.forward_record(dates, closes, exp[:12], rates[:12], dates[10], dates[-1])
    after = overlay.forward_record(d13, c13, exp, rates, dates[10], d13[-1])
    assert before["n_days"] == 1 and after["n_days"] == 2


def test_chapter_data_page_and_home_bullet(built):  # noqa: F811
    for lang in layout.LANGS:
        c = pages.Ctx(lang, facts.load_facts(built))
        home = read(built, f"{lang}/index.html")
        kf = pages.key_facts(c.f)
        assert kf["ov"] is not None
        bullets = [p for p in pages.render_key_points(c, kf) if "R2" in p]
        assert len(bullets) == 1
        assert f"<li>{pages.esc(bullets[0])}</li>" in home
        assert f"<li>{pages.esc(bullets[0])}</li>" in read(built, f"{lang}/research.html")
        tol = kf["ov"]["tolerance"]
        for idx, v in kf["ov"]["indices"].items():
            te, de = v["test"], v["design"]
            d_t = pages.gap_digits((te["cagr"], te["bh_cagr"]), (te["max_dd"], te["bh_max_dd"]))
            for key in ("cagr", "bh_cagr", "max_dd", "bh_max_dd"):  # test period, both indices
                assert pages.pct(te[key], d_t) in bullets[0], (idx, key)
            # the design-period line: CAGR and max drawdown against buy and hold, and the margin
            limit = de["bh_max_dd"] - tol
            d_d = pages.gap_digits((de["cagr"], de["bh_cagr"]), (de["max_dd"], limit), d=2)
            for x in (de["cagr"], de["bh_cagr"], de["max_dd"], limit):
                assert pages.pct(x, d_d) in bullets[0], (idx, x)
            assert f"{(de['max_dd'] - limit) * 100:.{d_d}f}" in bullets[0]
            below = de["cagr"] < de["bh_cagr"]
            assert (c.than(de["cagr"], de["bh_cagr"], d=d_d) in bullets[0]) and (
                below == (idx == "^IXIC")
            )
        assert kf["ov"]["after"] in bullets[0]
        # the caveat about the seen test period is in the bullet itself
        caveat = {
            "en": "not a clean out-of-sample check",
            "ja": "検証外データによる確認ではありません",
        }
        assert caveat[lang] in bullets[0]


# --------------------------------------------------------- item 2: tie-break amendment


def _a(vid, n, med):
    return {"variant_id": vid, "index": "^GSPC", "n_trades": n, "median": med, "period": "design"}


def test_old_tie_rule_reproduces_the_string_order_pick_and_affected_picks_are_listed():
    rows = [
        _a("ftd_c10_g2_v1", 15, 0.0123),
        _a("ftd_c3_g2_v1", 29, 0.0123),
        _a("ftd_c8_g2_v1", 17, 0.0123),
    ]
    old = {"A": selection.select_a(rows, "base", tie=selection.TIE_OLD)}
    new = {"A": selection.select_a(rows, "base")}
    assert old["A"]["^GSPC"]["selected"] == "ftd_c10_g2_v1"  # "ftd_c10" < "ftd_c3"
    assert new["A"]["^GSPC"]["selected"] == "ftd_c3_g2_v1"
    assert selection.affected_picks(new, old) == [
        {"evaluation": "A", "index": "^GSPC", "before": "ftd_c10_g2_v1", "after": "ftd_c3_g2_v1"}
    ]
    assert selection.affected_picks(new, new) == []


def test_unknown_tie_rule_is_rejected():
    with pytest.raises(ValueError):
        selection.select_a([_a("x", 20, 0.1)], "base", tie="alphabetical")


def test_selected_json_lists_the_affected_picks_computed_by_rerunning_the_old_rule(built):  # noqa: F811
    sel = selected_json(built)
    assert sel["tie_break_affected_picks"] == [
        {"evaluation": "A", "index": "^GSPC", "before": "ftd_c10_g2_v1", "after": "ftd_c3_g2_v1"}
    ]
    got = recheck.recompute_picks(built)  # selectors re-run on the run's own design rows
    assert got["affected"] == sel["tie_break_affected_picks"]
    assert got["old"]["A"]["^GSPC"]["selected"] == "ftd_c10_g2_v1"
    assert got["new"]["A"]["^GSPC"]["selected"] == sel["evaluations"]["A"]["^GSPC"]["selected"]


def test_tie_comparison_has_both_picks_test_results_and_the_tally_is_computed(built):  # noqa: F811
    sel = selected_json(built)
    (cmp,) = sel["tie_break_comparison"]
    assert cmp["before"]["id"] == "ftd_c10_g2_v1" and cmp["after"]["id"] == "ftd_c3_g2_v1"
    for which in ("before", "after"):
        assert cmp[which]["test"]["n_trades"] > 0
    f = facts.load_facts(built)
    tally = facts.tie_tally(f)
    assert tally["new"]["n"] == len([r for r in facts.selected_beats_baseline(f) if not r["same"]])
    for lang in layout.LANGS:
        plain = " ".join(text_of(built, f"{lang}/research.html").split())
        for key in ("ftd_c10_g2_v1", "ftd_c3_g2_v1"):
            assert key in plain
        for which in ("before", "after"):
            t = cmp[which]["test"]
            for s in (pages.spct(t["median"], 2), pages.spct(t["mean"], 2)):
                assert s in plain
            assert str(t["n_trades"]) in plain
        c = pages.Ctx(lang, f)
        if tally["new"] == tally["old"]:
            stem = c.t(
                "research.tie_tally_same", n=tally["new"]["n"], better=tally["new"]["better"]
            )
        else:
            stem = c.t(
                "research.tie_tally_changed",
                n=tally["new"]["n"],
                better=tally["new"]["better"],
                n_old=tally["old"]["n"],
                better_old=tally["old"]["better"],
            )
        assert stem in plain


def test_tally_counts_the_old_pick_when_the_change_moves_it():
    f = type("F", (), {})()
    f.selected = {
        "evaluations": {
            "A": {
                "^GSPC": {
                    "baseline": "base",
                    "selected": "new",
                    "test": {"baseline": {"median": 0.01}, "selected": {"median": 0.02}},
                }
            },
            "B": {},
            "C": {},
        },
        "tie_break_comparison": [
            {"evaluation": "A", "index": "^GSPC", "before": {"id": "old", "test": {"median": 0.0}}}
        ],
    }
    t = facts.tie_tally(f)
    assert t["new"] == {"n": 1, "better": 1} and t["old"] == {"n": 1, "better": 0}


def test_en_amendment_sentence_does_not_read_as_if_picks_still_use_string_order():
    en = i18n.load("en")["research.m_amend"]
    assert "string order" in en and "Before that" in en and "now in force" in en
    ja = i18n.load("ja")["research.m_amend"]
    assert "それまでは" in ja and "今の選択" in ja


@pytest.mark.parametrize("lang", layout.LANGS)
def test_key_point_note_mentions_the_post_hoc_tie_rule_and_the_r2_preview(lang):
    note = i18n.load(lang)["research.conclusions_note"]
    assert ("tie rule" in note and "preview" in note) or ("同点" in note and "下見" in note)
    assert ("candidates in the selection" in note) or ("候補に入っていた" in note)


def test_r2_tie_rule_effect_is_computed_and_had_none_on_the_real_data(built):  # noqa: F811
    ov = overlay_json(built)
    for info in ov["indices"].values():
        assert info["tie_effect"]["changed"] is False
        assert info["tie_effect"]["n_exact_ties_at_top"] == 0
    for lang in layout.LANGS:
        assert i18n.load(lang)["research.ov_tie_none"] in read(built, f"{lang}/research.html")


def _ov(vid, cagr, dd=-0.4):
    return {
        "variant_id": vid,
        "index": "I",
        "period": "design",
        "cagr": cagr,
        "max_dd": dd,
        "bh_max_dd": -0.4,
    }


def test_r2_tie_effect_detects_a_pick_the_rule_changed():
    a, b = overlay.variant_id(3, True, 1.5), overlay.variant_id(5, True, 1.25)  # a < b as strings
    rows = [_ov(a, 0.05), _ov(b, 0.05)]
    eff = overlay.tie_rule_effect(rows)["I"]
    assert eff["n_exact_ties_at_top"] == 1
    assert eff["pick"] == b and eff["pick_string_order"] == a and eff["changed"] is True
    none = overlay.tie_rule_effect([_ov(a, 0.05), _ov(b, 0.04)])["I"]
    assert none["changed"] is False and none["n_exact_ties_at_top"] == 0


# ------------------------------------------------------ item 3: R2 design-period sentences


@pytest.mark.parametrize("lang", layout.LANGS)
def test_design_period_sentences_are_generated_per_index(built, lang):  # noqa: F811
    ov = overlay_json(built)
    f = facts.load_facts(built)
    c = pages.Ctx(lang, f)
    plain = " ".join(text_of(built, f"{lang}/research.html").split())
    main = ov["main_cost_model"]
    worse = []
    for idx, info in ov["indices"].items():
        sel, bh = info["design"]["selected"][main], info["design"]["baseline"][main]
        margin = (sel["max_dd"] - (bh["max_dd"] - ov["max_dd_tolerance"])) * 100
        sentences = pages._design_sentences(c, ov, idx, info, main)
        for s in sentences:
            assert " ".join(s.split()) in plain
        d_c = pages.gap_digits((sel["cagr"], bh["cagr"]))
        limit = bh["max_dd"] - ov["max_dd_tolerance"]
        d_m = pages.gap_digits((sel["max_dd"], limit), (sel["max_dd"], bh["max_dd"]), d=2)
        assert pages.pct(sel["cagr"], d_c) in sentences[0]
        assert pages.pct(bh["cagr"], d_c) in sentences[0]
        assert pages.pct(sel["max_dd"], d_m) in sentences[0]
        assert pages.pct(bh["max_dd"], d_m) in sentences[0]
        assert f"{margin:.{d_m}f}" in " ".join(sentences)
        if sel["cagr"] < bh["cagr"]:
            worse.append(idx)
            assert c.t("research.ov_design_worse", idx=c.name(idx)) in sentences
        narrow = c.t("research.ov_design_narrow") in sentences
        assert narrow == (margin < pages.NARROW_MARGIN_POINTS)
    # the real data: the picked ^IXIC variant has a lower design CAGR than buy & hold
    assert worse == ["^IXIC"]
    gspc = ov["indices"]["^GSPC"]
    m = (
        gspc["design"]["selected"][main]["max_dd"]
        - (gspc["design"]["baseline"][main]["max_dd"] - ov["max_dd_tolerance"])
    ) * 100
    assert 0.05 < m < 0.12  # about 0.08 points


# ---------------------------------------------------------------- item 4: the gate


def _site_problem(run) -> dict:
    return validate._check_site_render(run)


def test_untouched_run_passes_the_render_and_picks_checks(built, copy_run):  # noqa: F811
    assert _site_problem(copy_run)["status"] == "PASS", _site_problem(copy_run)
    assert recheck.check_picks(copy_run)["status"] == "PASS", recheck.check_picks(copy_run)
    # re-running the gate on a run that already has validation.json must not change the render
    (copy_run / "validation.json").write_text("{}")
    assert _site_problem(copy_run)["status"] == "PASS"


def test_validate_run_includes_the_new_checks(copy_run):
    res = validate.validate_run(copy_run, check_site=True)
    by = {c["name"]: c for c in res["checks"]}
    assert by["site_render"]["status"] == "PASS" and by["picks_recomputed"]["status"] == "PASS"


def _edit(run, rel, fn):
    p = run / "site" / rel
    p.write_text(fn(p.read_text(encoding="utf-8")), encoding="utf-8")


def _r2(text):
    a = text.index('id="overlay"')
    return a, text.index('id="caveats"')


def test_gate_fails_when_a_number_in_the_r2_prose_is_changed(copy_run):
    def change(t):
        a, b = _r2(t)
        seg = t[a:b]
        m = re.search(r"(\d+\.\d)%", seg[seg.index("<p>") :])
        assert m
        return t[:a] + seg.replace(m.group(0), "99.9%", 1) + t[b:]

    _edit(copy_run, "ja/research.html", change)
    res = _site_problem(copy_run)
    assert res["status"] == "FAIL" and "ja/research.html" in res["detail"]


def test_gate_fails_when_a_table_cell_is_changed(copy_run):
    def change(t):
        a, b = _r2(t)
        seg = t[a:b]
        m = re.search(r'<td class="num">(\-?\d+\.\d%)</td>', seg)
        assert m
        return t[:a] + seg.replace(m.group(0), '<td class="num">1.0%</td>', 1) + t[b:]

    _edit(copy_run, "en/research.html", change)
    assert _site_problem(copy_run)["status"] == "FAIL"


def test_gate_fails_on_an_extra_list_item(copy_run):
    def add(t):
        a, b = _r2(t)
        seg = t[a:b].replace("<ul>", "<ul><li>Returns are guaranteed.</li>", 1)
        return t[:a] + seg + t[b:]

    _edit(copy_run, "en/research.html", add)
    res = _site_problem(copy_run)
    assert res["status"] == "FAIL" and "en/research.html" in res["detail"]


def test_gate_fails_when_a_disclaimer_is_deleted(copy_run):
    def drop(t):
        c = pages.Ctx("ja", facts.load_facts(copy_run))
        out = t.replace(pages.esc(c.t("research.ov_disclaimer")), "", 1)
        assert out != t
        return out

    _edit(copy_run, "ja/research.html", drop)
    assert _site_problem(copy_run)["status"] == "FAIL"


def test_gate_fails_on_an_edited_static_asset_and_an_extra_page(copy_run):
    p = copy_run / "site" / "assets" / layout.ASSETS[0]
    p.write_text(p.read_text() + "\n/* x */\n")
    assert _site_problem(copy_run)["status"] == "FAIL"


def test_gate_fails_on_an_unexpected_page(copy_run):
    (copy_run / "site" / "en" / "extra.html").write_text("<p>x</p>")
    assert "unexpected pages" in _site_problem(copy_run)["detail"]


def test_gate_ignores_only_the_generation_timestamp(copy_run):
    s = json.loads((copy_run / "data" / "summary.json").read_text())
    old = s["generated_at"]
    for lang in layout.LANGS:
        _edit(copy_run, f"{lang}/index.html", lambda t: t.replace(old, "2000-01-01T00:00:00Z"))
    assert _site_problem(copy_run)["status"] == "PASS"


def test_gate_fails_when_the_recorded_pick_is_swapped(copy_run):
    p = copy_run / "research" / "selected.json"
    d = json.loads(p.read_text())
    cur = d["evaluations"]["A"]["^GSPC"]["selected"]
    d["evaluations"]["A"]["^GSPC"]["selected"] = d["evaluations"]["A"]["^GSPC"]["baseline"]
    assert cur != d["evaluations"]["A"]["^GSPC"]["selected"]
    p.write_text(json.dumps(d))
    res = recheck.check_picks(copy_run)
    assert res["status"] == "FAIL" and "A ^GSPC" in res["detail"]


def test_gate_fails_when_the_r2_pick_or_the_affected_list_is_changed(copy_run):
    p = copy_run / "research" / "overlay_selected.json"
    d = json.loads(p.read_text())
    d["indices"]["^IXIC"]["selected"] = overlay.variant_id(5, False, 1.5)
    p.write_text(json.dumps(d))
    assert "R2 ^IXIC" in recheck.check_picks(copy_run)["detail"]
    q = copy_run / "research" / "selected.json"
    s = json.loads(q.read_text())
    s["tie_break_affected_picks"] = []
    q.write_text(json.dumps(s))
    assert "tie_break_affected_picks" in recheck.check_picks(copy_run)["detail"]


def check(run, fetched, fresh):
    return recheck.check_results(run, fetched[0], fetched[1], fresh=fresh)


def _rewrite_csv(path, fn):
    rows = list(csv.DictReader(path.open()))
    fn(rows)
    with path.open("w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


def _edit_json(path, fn):
    d = json.loads(path.read_text())
    fn(d)
    path.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")


def test_full_recomputation_passes_on_the_real_run(built, fetched, fresh):  # noqa: F811
    res = check(built, fetched, fresh)
    assert res["status"] == "PASS", res["detail"]
    assert "full recomputation" in res["detail"]


def test_recomputation_ignores_only_the_generation_timestamps(copy_run, fetched, fresh):
    p = copy_run / "data" / "summary.json"
    _edit_json(p, lambda d: d.__setitem__("generated_at", "2030-01-01T00:00:00Z"))
    rp = copy_run / "research" / "report.html"
    rp.write_text(
        re.sub(r"(生成: )[\d-]+ [\d:]+ UTC", r"\g<1>2030-01-01 00:00 UTC", rp.read_text("utf-8")),
        encoding="utf-8",
    )
    assert check(copy_run, fetched, fresh)["status"] == "PASS"
    _edit_json(p, lambda d: d["counts"].__setitem__("ftd_events", d["counts"]["ftd_events"] + 1))
    res = check(copy_run, fetched, fresh)
    assert res["status"] == "FAIL" and "summary.json" in res["detail"]


def test_gate_fails_when_daily_status_risk_or_d25_is_tampered(copy_run, fetched, fresh):
    def risk(rows):
        victim = next(r for r in rows if r["risk_level"] == "NORMAL")
        victim["risk_level"] = "SEVERE"

    _rewrite_csv(copy_run / "data" / "daily_status.csv", risk)
    res = check(copy_run, fetched, fresh)
    assert res["status"] == "FAIL" and "daily_status.csv" in res["detail"]
    assert "risk_level" not in res["detail"] or "column" in res["detail"]


def test_gate_fails_when_d25_is_tampered(copy_run, fetched, fresh):
    def d25(rows):
        rows[2000]["d25"] = str(int(rows[2000]["d25"]) + 1)

    _rewrite_csv(copy_run / "data" / "daily_status.csv", d25)
    assert "daily_status.csv" in check(copy_run, fetched, fresh)["detail"]


def test_gate_fails_when_a_variants_design_median_is_tampered(copy_run, fetched, fresh):
    def median(rows):
        victim = next(r for r in rows if r["family"] == "ftd" and r["A_median"] != "")
        victim["A_median"] = str(float(victim["A_median"]) + 0.0001)

    _rewrite_csv(copy_run / "research" / "variants.csv", median)
    res = check(copy_run, fetched, fresh)
    assert res["status"] == "FAIL" and "variants.csv" in res["detail"]


@pytest.mark.parametrize("column", ["gain_pct", "invalidation_date", "invalidated_within_60"])
def test_gate_fails_when_an_ftd_event_is_tampered(copy_run, fetched, fresh, column):
    def ftd(rows):
        victim = next(r for r in rows if r[column] != "")
        if column == "gain_pct":
            victim[column] = "9.99"
        elif column == "invalidated_within_60":
            victim[column] = "False" if victim[column] == "True" else "True"
        else:
            victim[column] = "2001-01-02"

    _rewrite_csv(copy_run / "data" / "ftd_events.csv", ftd)
    res = check(copy_run, fetched, fresh)
    assert res["status"] == "FAIL" and "ftd_events.csv" in res["detail"]


def test_gate_fails_when_the_overlay_disclosure_fields_are_tampered(copy_run, fetched, fresh):
    p = copy_run / "research" / "overlay_selected.json"
    _edit_json(p, lambda d: d["test_period_contamination"].__setitem__("clean_out_of_sample", True))
    res = check(copy_run, fetched, fresh)
    assert res["status"] == "FAIL" and "overlay_selected.json" in res["detail"]


def test_gate_fails_when_the_r_disclosure_fields_are_tampered(copy_run, fetched, fresh):
    p = copy_run / "research" / "selected.json"
    _edit_json(
        p,
        lambda d: d["test_period_contamination"]["figures"]["dd_day_share"].update({"^GSPC": 0.5}),
    )
    res = check(copy_run, fetched, fresh)
    assert res["status"] == "FAIL" and "selected.json" in res["detail"]


def test_gate_fails_when_the_forward_record_or_test_end_is_tampered(copy_run, fetched, fresh):
    p = copy_run / "research" / "selected.json"
    _edit_json(p, lambda d: d["split"]["test"].__setitem__(1, "2026-11-09"))
    assert "selected.json" in check(copy_run, fetched, fresh)["detail"]


def test_gate_fails_when_report_html_text_is_changed(copy_run, fetched, fresh):
    rp = copy_run / "research" / "report.html"
    rp.write_text(
        rp.read_text("utf-8").replace("検証期間を見ていた", "検証期間は見ていない", 1),
        encoding="utf-8",
    )
    res = check(copy_run, fetched, fresh)
    assert res["status"] == "FAIL" and "report.html" in res["detail"]


def test_gate_fails_when_test_results_csv_is_tampered(copy_run, fetched, fresh):
    def cagr(rows):
        victim = next(r for r in rows if r["evaluation"] == "B")
        victim["cagr"] = str(float(victim["cagr"]) + 0.01)

    _rewrite_csv(copy_run / "research" / "test_results.csv", cagr)
    res = check(copy_run, fetched, fresh)
    assert res["status"] == "FAIL" and "test_results.csv" in res["detail"]


@pytest.mark.parametrize("name", ["overlay_test_results.csv", "overlay_variants.csv"])
def test_gate_fails_when_an_overlay_csv_is_tampered(copy_run, fetched, fresh, name):
    def cagr(rows):
        rows[3]["cagr"] = str(float(rows[3]["cagr"]) * 1.1 + 0.001)

    _rewrite_csv(copy_run / "research" / name, cagr)
    res = check(copy_run, fetched, fresh)
    assert res["status"] == "FAIL" and name in res["detail"]


def test_gate_fails_when_overlay_selected_json_numbers_are_tampered(copy_run, fetched, fresh):
    p = copy_run / "research" / "overlay_selected.json"
    d = json.loads(p.read_text())
    main = d["main_cost_model"]
    d["indices"]["^GSPC"]["test"]["selected"][main]["cagr"] += 1e-12  # even a tiny change
    p.write_text(json.dumps(d, indent=2, ensure_ascii=False) + "\n")
    assert check(copy_run, fetched, fresh)["status"] == "FAIL"


def test_recorded_contamination_figures_equal_the_data(built):  # noqa: F811
    assert contamination.verify(built / "data" / "daily_status.csv") == []
    bad = dict(contamination.CONTAMINATION_R["figures"]["dd_day_share"])
    assert (
        abs(
            contamination.recompute(built / "data" / "daily_status.csv")["dd_day_share"]["^GSPC"]
            - bad["^GSPC"]
        )
        < 6e-4
    )


def test_contamination_verify_reports_missing_data_instead_of_crashing(tmp_path):
    """An index with no rows in the recorded period is a problem to report, not a crash."""
    p = tmp_path / "daily_status.csv"
    p.write_text("date,index,close,is_dd,d25,risk_level\n"
                 "2026-12-01,^GSPC,100,False,0,NORMAL\n", encoding="utf-8")
    problems = contamination.verify(p)
    assert any("^GSPC" in x and "no rows" in x for x in problems)
    assert any("^IXIC" in x and "no rows" in x for x in problems)


def test_contamination_figures_ignore_sessions_after_the_recorded_period(tmp_path):
    """New daily sessions must not move the figures recorded for 2000-01-03..2026-11-06."""
    import csv as _csv

    end = contamination.CONTAMINATION_R["period"][1]
    days = [f"2026-{m:02d}-{d:02d}" for m in range(8, 13) for d in range(1, 29)]
    cols = ["date", "index", "close", "is_dd", "d25", "risk_level"]

    def write(path, rows):
        with path.open("w", newline="", encoding="utf-8") as f:
            w = _csv.DictWriter(f, fieldnames=cols)
            w.writeheader()
            w.writerows(rows)

    rows = []
    for sym in ("^GSPC", "^IXIC"):
        for i, d in enumerate(days):
            after = d > end
            rows.append({
                "date": d, "index": sym, "close": 100 - 30 * after + (i % 5),
                "is_dd": str(after or i % 4 == 0), "d25": 6 if after else 5 + i % 3,
                "risk_level": "SEVERE" if after else ("NORMAL" if i % 2 else "SEVERE"),
            })
    full, cut = tmp_path / "full.csv", tmp_path / "cut.csv"
    write(full, rows)
    write(cut, [r for r in rows if r["date"] <= end])
    assert contamination.recompute(full) == contamination.recompute(cut)


# ---------------------------------------------------------------- item 5: ordering labels


def test_ordering_detail_reports_the_smallest_step_and_its_levels():
    d = pages.ordering_detail([0.10, 0.15, 0.20, 0.205])
    assert d["kind"] == "rises" and d["step"] == pytest.approx(0.5)
    assert (d["lo"], d["hi"]) == (2, 3)  # HIGH -> SEVERE
    big = pages.ordering_detail([0.10, 0.15, 0.20, 0.30])
    assert big["kind"] == "rises" and big["step"] == pytest.approx(5.0)
    assert pages.ordering_detail([0.30, 0.20, 0.10, 0.05])["kind"] == "falls"
    assert pages.ordering_detail([0.10, 0.10, 0.20, 0.30])["kind"] == "none"
    assert pages.ordering_detail([0.10, 0.30, 0.20, 0.40])["kind"] == "none"


def test_mono_phrase_names_the_small_step_and_otherwise_stays_plain():
    f = type("F", (), {})()
    en = pages.Ctx("en", f)
    ja = pages.Ctx("ja", f)
    small = {"p_NORMAL": 0.10, "p_CAUTION": 0.20, "p_HIGH": 0.30, "p_SEVERE": 0.305}
    assert pages._mono(en, small) == "rises at every step, but HIGH to SEVERE is only +0.5 points"
    assert "高い" in pages._mono(ja, small) and "0.5ポイント" in pages._mono(ja, small)
    plain = {"p_NORMAL": 0.10, "p_CAUTION": 0.20, "p_HIGH": 0.30, "p_SEVERE": 0.40}
    assert pages._mono(en, plain) == "rises at every step"
    falls = {"p_NORMAL": 0.40, "p_CAUTION": 0.30, "p_HIGH": 0.20, "p_SEVERE": 0.10}
    assert pages._mono(en, falls) == "falls with the level, highest at NORMAL"
    assert pages._mono(en, {"p_NORMAL": 0.1, "p_CAUTION": 0.1, "p_HIGH": 0.3, "p_SEVERE": 0.4}) == (
        "shows no consistent ordering"
    )


# ------------------------------------------------------------------- item 6: units


@pytest.mark.parametrize("lang", layout.LANGS)
def test_data_page_states_the_pct_change_units_in_both_files(built, lang):  # noqa: F811
    plain = " ".join(text_of(built, f"{lang}/data.html").split())
    assert "pct_change" in plain and "distribution_days.csv" in plain
    assert "daily_status.csv" in plain
    assert "-0.5%" in plain and "-0.005" in plain


# ---------------------------------------------------------- items 7 and 8: wording


def test_japanese_wording_fixes(built):  # noqa: F811
    ja = i18n.load("ja")
    plain = " ".join(text_of(built, "ja/research.html").split())
    assert "より同じ" not in plain and "ポイント同じ" not in plain
    assert "と同じです" in plain  # S&P 500 max drawdown equals buy & hold
    assert "fixed 0%" not in plain and "固定金利 0%" in plain
    assert "借入費用を変えた場合の検証期間" in plain and "検証期間." not in plain
    assert "終値が FTD 当日の安値を割り込んで" in plain
    assert "安値を終値で割" not in "".join(ja.values())
    for bad in ("選ぶ期間", "確かめる期間"):
        assert bad not in "".join(ja.values())
    for rel in ("ja/index.html", "ja/definitions.html"):
        assert "安値を終値で割" not in read(built, rel)


def test_english_list_wording(built):  # noqa: F811
    plain = " ".join(text_of(built, "en/research.html").split())
    assert "3, 4 or 5 Distribution Days" in plain
    assert "125% or 150%" in plain
    assert "fixed rates of 0%, 3% and 6% a year" in plain
    assert "0%, 3% or 6%" not in plain


def test_japanese_list_wording(built):  # noqa: F811
    plain = " ".join(text_of(built, "ja/research.html").split())
    assert "k は 3、4、5 のいずれかです" in plain
    assert "比率は 125%または150% です" in plain
    assert "年 0%、3%、6% の固定金利でも計算します" in plain
    assert "3、4か5" not in plain and "0%、3%か6%" not in plain
    # the rule of B: not more than the tolerance below buy and hold
    assert "2ポイントを超えて下がらない" in plain
    assert "ポイント以上は下がらない" not in plain
    assert "回のうち" in plain


def test_unchanged_rate_effect_says_so_in_both_languages(built):  # noqa: F811
    for lang in layout.LANGS:
        c = pages.Ctx(lang, facts.load_facts(built))
        same = pages._before_after(c, {"cagr_before": 0.127022, "cagr_after": 0.127022})
        assert c.t("research.ov_rate_same", value="12.7022%") == same
        assert "から" not in same and "to" not in same.split()
        moved = pages._before_after(c, {"cagr_before": 0.127022, "cagr_after": 0.127031})
        assert "12.7022%" in moved and "12.7031%" in moved
    plain = " ".join(text_of(built, "ja/research.html").split())
    assert not re.search(r"(\d+\.\d+%) から \1 ", plain)


def test_equal_comparison_phrases_read_naturally():
    f = type("F", (), {})()
    ja, en = pages.Ctx("ja", f), pages.Ctx("en", f)
    assert ja.than(0.114, 0.114) == "と同じです" and en.than(0.114, 0.114) == "the same as"
    assert ja.than(0.2, 0.1) == "より高いです" and en.than(0.2, 0.1) == "higher than"
    assert ja.gap(0.1, 0.1) == "と差がありません" and en.gap(0.1, 0.1) == "no different from"
    assert ja.gap(0.123, 0.1, hi="shallower", lo="deeper") == "より2.3ポイント浅いです"
    assert en.gap(0.1, 0.123, hi="shallower", lo="deeper") == "2.3 points deeper than"


# --------------------------------------------------------------- item 9: rate timing


def test_rate_timing_effect_is_recorded_and_negligible(built):  # noqa: F811
    ov = overlay_json(built)
    assert ov["rate_lag_sessions"] == 1
    for info in ov["indices"].values():
        eff = info["rate_timing_effect"]
        for period in ("design", "test"):
            assert abs(eff[period]["cagr_after"] - eff[period]["cagr_before"]) < 5e-4
        assert eff["pick_changed"] is False
    assert any("rate" in a["what"] for a in ov["post_hoc_amendments"])


def test_rate_effect_compares_the_two_timings_on_the_same_variant():
    dates, closes = _bars(10)
    dff = [("2029-12-31", 0.01), ("2030-01-05", 0.09)]
    new = overlay.rate_known(dates, dff)
    old = overlay.rate_known(dates, dff, lag=0)
    assert new[4] == 0.01 and old[4] == 0.09 and new[5] == 0.09 and old[3] == 0.01


# ----------------------------------------------------------- items 10 and 11: freeze, key point


def test_freeze_file_records_a_utc_timestamp_and_the_info_block_carries_it():
    data = freeze.load()
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", data["frozen_at_utc"])
    assert freeze.info("overlay")["frozen_at_utc"] == data["frozen_at_utc"]


def test_freeze_write_adds_a_timestamp(tmp_path):
    out = freeze.write(tmp_path / "f.json")
    assert re.fullmatch(r"\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ", out["frozen_at_utc"])


def _make_runs(tmp_path, spec):
    for name, files in spec.items():
        for rel, body in files.items():
            p = tmp_path / name / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(body)


def test_provenance_counts_only_runs_made_before_the_freeze(tmp_path):
    _make_runs(
        tmp_path,
        {
            "20261108T034158Z": {"research/overlay_variants.csv": "h\na\n"},
            "20261108T034340Z": {"research/overlay_variants.csv": "h\na\n"},
            # named in the second of the freeze and later runs began after the freeze file
            "20261108T040620Z": {"research/overlay_variants.csv": "h\nb\n"},
            "20261108T050000Z": {"research/overlay_variants.csv": "h\nc\n"},
            "20261108T030000Z": {},  # a run without the file is not counted
        },
    )
    prov = freeze.compute_provenance(tmp_path, "2026-11-08T04:06:20Z")
    ov = prov["files"]["overlay"]
    assert ov["n_runs"] == 2 and ov["first_run"] == "20261108T034158Z"
    assert ov["identical_bytes"] is True and ov["identical_rows"] is True
    assert ov["runs"][0]["sha256"] == hashlib.sha256(b"h\na\n").hexdigest()
    assert prov["files"]["selection"]["n_runs"] == 0  # no variants.csv anywhere
    assert (
        freeze.compute_provenance(tmp_path / "missing", "2026-11-08T04:06:20Z")["files"]["overlay"][
            "n_runs"
        ]
        == 0
    )


def test_provenance_tells_a_header_only_difference_from_a_real_one(tmp_path):
    _make_runs(
        tmp_path,
        {
            "20261108T032834Z": {"research/variants.csv": "old_name,x\n1,2\n"},
            "20261108T034158Z": {"research/variants.csv": "new_name,x\n1,2\n"},
        },
    )
    sel = freeze.compute_provenance(tmp_path, "2026-11-08T04:06:20Z")["files"]["selection"]
    assert sel["identical_bytes"] is False and sel["identical_rows"] is True
    (tmp_path / "20261108T034158Z" / "research" / "variants.csv").write_text("new_name,x\n1,3\n")
    sel = freeze.compute_provenance(tmp_path, "2026-11-08T04:06:20Z")["files"]["selection"]
    assert sel["identical_rows"] is False


def test_record_provenance_keeps_an_existing_block(tmp_path):
    path = tmp_path / "f.json"
    runs = tmp_path / "runs"
    _make_runs(runs, {"20200101T000000Z": {"research/variants.csv": "h\nx\n"}})
    freeze.write(path, runs_dir=runs)  # the block is computed when the file is written
    assert freeze.load(path)["provenance"]["files"]["selection"]["n_runs"] == 1
    _edit_json(path, lambda d: d.pop("provenance"))  # an older file without the block
    first = freeze.record_provenance(runs, path)
    assert first["files"]["selection"]["n_runs"] == 1
    (runs / "20200101T000000Z" / "research" / "variants.csv").unlink()
    assert freeze.record_provenance(runs, path) == first  # a past fact does not change
    assert freeze.record_provenance(runs, path, replace=True)["files"]["selection"]["n_runs"] == 0


def test_freeze_local_date_explains_the_utc_date():
    # 2026-11-07 20:06:20 PST (UTC-8) is 2026-11-08T04:06:20Z
    assert freeze.local_date_of("2026-11-08T04:06:20Z") == "2026-11-07"
    data = freeze.load()
    assert freeze.local_date_of(data["frozen_at_utc"]) == data["pre_registered"]


def test_real_provenance_is_computed_and_shown_on_the_page(built):  # noqa: F811
    stored = freeze.stored_provenance()
    assert stored is not None
    for kind, doc in (("overlay", overlay_json(built)), ("selection", selected_json(built))):
        prov = doc["freeze_provenance"]
        assert prov == stored["files"][kind]
        assert doc["freeze"]["frozen_at_utc"] == freeze.load()["frozen_at_utc"]
        # only runs made before the freeze, and the sentence says how many
        assert prov["n_runs"] >= 1
        assert all(
            r["run"] < freeze.load()["frozen_at_utc"].replace("-", "").replace(":", "")
            for r in prov["runs"]
        )
    for lang in layout.LANGS:
        plain = " ".join(text_of(built, f"{lang}/research.html").split())
        ts = freeze.load()["frozen_at_utc"]
        assert ts in plain
        sel = stored["files"]["selection"]
        assert f"{sel['n_runs']}" in plain and sel["first_run"] in plain
        # R's variants.csv differ only in a renamed header: the page says so, not "identical"
        assert sel["identical_bytes"] is False and sel["identical_rows"] is True
        assert i18n.load(lang)["research.prov_rows_same"] in plain
        ovp = stored["files"]["overlay"]
        assert ovp["runs"][0]["sha256"][:12] in plain


@pytest.mark.parametrize("lang", layout.LANGS)
def test_freeze_wording_no_longer_claims_it_was_fixed_before_the_runs(lang):
    d = i18n.load(lang)
    for k in ("research.m_freeze", "research.freeze_head"):
        assert "fixed on" not in d[k] and "に固定し" not in d[k]
    assert "docs/plan.md" in d["research.freeze_head"]
    assert "UTC-8" in d["research.freeze_head"]


def test_dff_source_is_kept_in_the_run_and_hashed(built):  # noqa: F811
    ov = overlay_json(built)
    src = ov["dff_source"]
    kept = built / "research" / "fred_DFF.csv"
    assert kept.is_file() and hashlib.sha256(kept.read_bytes()).hexdigest() == src["sha256"]
    assert src["last_observation"] <= validate.last_session_date(built)


def test_baseline_key_point_is_not_called_untouched_by_the_selection():
    en = i18n.load("en")["research.conclusions_note"]
    assert "never picked" not in en
    assert "design-period rows were candidates in the selection" in en
    assert "skill's own definition" in en
