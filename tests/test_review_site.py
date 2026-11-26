"""Review fixes on the generated site (H2, H3, M1-M5, M7, M8, L2-L6). Real data, no network."""

from __future__ import annotations

import html as htmllib
import json
import re

import pytest

from research import variants
from tests.test_site import built, read, real_run  # noqa: F401  (fixtures)
from webgen import dictionary, facts, i18n, layout, pages


def texts(lang):
    return i18n.load(lang)


def f_of(run):
    return facts.load_facts(run)


def plain(run, rel):
    """Page text with tags removed and entities decoded."""
    return htmllib.unescape(re.sub(r"<[^>]+>", " ", read(run, rel)))


# ---- H2: d5 / d15 / d25 count today plus N previous sessions ------------------------------

STALE = {
    "ja": ("25日で", "15日で", "5日で", "直近25セッション", "25日）", "（25日"),
    "en": (
        "in 25 days",
        "in 15 days",
        "in 5 days",
        "last 25 sessions",
        "(25d)",
        "last 5, 15 and 25",
    ),
}


@pytest.mark.parametrize("lang", layout.LANGS)
def test_no_stale_window_wording_in_ui_text(lang):
    for k, v in texts(lang).items():
        for bad in STALE[lang]:
            assert bad not in v, (lang, k, bad)


def test_definitions_state_the_26_session_window(built):  # noqa: F811
    assert "26セッション" in plain(built, "ja/definitions.html")
    assert "26 sessions" in plain(built, "en/definitions.html")
    chart = read(built, "en/chart.html")
    assert "previous 25 sessions" in chart  # legend text passed to the chart script


def test_dictionary_states_the_window_for_d_columns_and_bucket():
    md = dictionary.render_markdown()
    rows = {line.split("|")[1].strip(" `"): line for line in md.splitlines() if "|" in line}
    for col, n in (("d5", "6"), ("d15", "16"), ("d25", "26")):
        assert f"{n} sessions" in rows[col], col
    bucket = next(line for line in md.splitlines() if line.startswith("| `bucket`"))
    assert "26 sessions" in bucket


# ---- H3: SEVERE is reached in several ways -------------------------------------------------


@pytest.mark.parametrize("lang", layout.LANGS)
def test_base_rate_paragraph_does_not_claim_severe_needs_d25(built, lang):  # noqa: F811
    text = plain(built, f"{lang}/definitions.html")
    assert "SEVERE needs d25" not in text and "深刻の条件は d25" not in text
    for b in facts.base_rates(f_of(built)):
        assert pages.pct(b["share_severe_not_d25"]) in text
        assert str(b["severe_days"]) in text.replace(",", "")


# ---- M1: design-period baseline lines, no false "rises every step" -------------------------


def test_key_points_include_design_baseline_line_per_index(built):  # noqa: F811
    f = f_of(built)
    kf = pages.key_facts(f)
    for lang in layout.LANGS:
        c = pages.Ctx(lang, f)
        points = pages.render_key_points(c, kf)
        for idx in f.indices:
            dc = f.selected["evaluations"]["C"][idx]["design"]["baseline"]
            db = f.selected["evaluations"]["B"][idx]["design"]["baseline"]
            d_dd = pages.gap_digits((db["max_dd"], db["em_max_dd"]))
            d_p = pages.gap_digits((dc["p_elevated"], dc["p_normal"]))
            both = (
                pages.pct(dc["p_elevated"], d_p),
                pages.pct(dc["p_normal"], d_p),
                pages.pct(db["em_max_dd"], d_dd),
            )
            line = [p for p in points if all(x in p for x in both) and c.name(idx) in p]
            assert len(line) == 1, (lang, idx)
        page = plain(built, f"{lang}/research.html")
        for p in points:
            assert " ".join(p.split()) in " ".join(page.split())


def test_monotonic_claim_follows_the_ordering_function(built):  # noqa: F811
    f = f_of(built)
    kf = pages.key_facts(f)
    c = pages.Ctx("en", f)
    points = pages.render_key_points(c, kf)
    computed = {
        pages.ordering([d[period].get(f"p_{lv}") for lv in facts.RISK_LEVELS])
        for d in kf["c"].values()
        for period in ("design", "test")
    }
    stems = {
        "rises": c.t("mono.rises"),
        "falls": c.t("mono.falls"),
        "none": c.t("mono.none"),
    }
    found = {k for k, stem in stems.items() if any(stem in p for p in points)}
    assert found == computed  # no ordering phrase appears that the data does not support
    # every sentence carries exactly the phrase computed for its own four values
    for idx, d in kf["c"].items():
        for period in ("design", "test"):
            phrase = pages._mono(c, d[period])
            assert any(phrase in p and c.name(idx) in p for p in points), (idx, period, phrase)


# ---- M2: B selection constraint broken out of sample ---------------------------------------


@pytest.mark.parametrize("lang", layout.LANGS)
def test_b_violation_sentences_match_the_data(built, lang):  # noqa: F811
    f = f_of(built)
    viol = facts.b_constraint_violations(f)
    c = pages.Ctx(lang, f)
    points = pages.render_key_points(c, pages.key_facts(f))
    marker = c.t("research.k_b_violation", idx="@", id="@", cagr="@", bh_cagr="@", gap="@", tol="@")
    stem = marker.split("@")[1]  # fixed words between the first two placeholders
    hits = [p for p in points if stem in p]
    assert len(hits) == len(viol)
    for v, p in zip(viol, hits, strict=True):
        assert v["id"] in p and pages.pct(v["cagr"]) in p


# ---- M3 / M4 / M5 ---------------------------------------------------------------------------


def test_rally_day1_definition_matches_the_vendored_code(built):  # noqa: F811
    ja, en = plain(built, "ja/definitions.html"), plain(built, "en/definitions.html")
    assert "上半分" in ja and "取り消" in ja
    assert "top half" in en and "reset" in en


def test_index_difference_explains_qqq_etf_and_volume(built):  # noqa: F811
    ja, en = plain(built, "ja/definitions.html"), plain(built, "en/definitions.html")
    for text in (ja, en):
        assert "QQQ" in text and "ETF" in text
    assert "ナスダック 100" in ja and "Nasdaq-100" in en
    assert "出来高" in ja and "volume" in en


@pytest.mark.parametrize("lang", layout.LANGS)
def test_trade_overlap_is_reported_from_data(built, lang):  # noqa: F811
    f = f_of(built)
    text = plain(built, f"{lang}/research.html")
    ov = facts.ftd_overlap(f)
    assert ov and all(o["n"] > 0 for o in ov)
    section = text[text.index(i18n.load(lang)["research.h_caveats"]) :]
    for o in ov:
        assert f"{o['k']}" in section and f"{o['n']}" in section
    assert i18n.load(lang)["research.cv_trade_overlap"].split("{")[0][:6] in section


# ---- M7 / M8 -------------------------------------------------------------------------------


def test_calendar_year_n_shown_as_years_and_period_stated(built):  # noqa: F811
    f = f_of(built)
    en, ja = plain(built, "en/research.html"), plain(built, "ja/research.html")
    years = {int(r["n_years"]) for r in f.bucket if r["n_years"]}
    assert any(f"{y} years" in en for y in years) and any(f"{y}年" in ja for y in years)
    for text in (en, ja):
        assert not re.search(r"\d+y\)", text)
        assert f.first_date in text and f.last_date in text


def test_unit_sentence_names_the_percent_columns_and_the_fraction_one(built):  # noqa: F811
    for lang in layout.LANGS:
        text = plain(built, f"{lang}/data.html")
        for name in ("correction_pct", "min_gain", "min_decline_frac"):
            assert name in text, (lang, name)
    assert "| `min_decline_pct` |" not in dictionary.render_markdown()
    assert "| `min_decline_frac` |" in dictionary.render_markdown()


# ---- L2 / L1 ---------------------------------------------------------------------------------


@pytest.mark.parametrize("lang", layout.LANGS)
def test_amendment_and_freeze_are_shown_on_the_research_page(built, lang):  # noqa: F811
    f = f_of(built)
    text = plain(built, f"{lang}/research.html")
    assert f.selected["freeze"]["grid_sha256"] in text
    assert f.overlay["freeze"]["grid_sha256"] in text
    assert f.selected["post_hoc_amendments"][0]["date"] in text
    assert f.selected["freeze"]["pre_registered"] in text


def test_pctl_rule_discloses_minimum_observations_and_ties(built):  # noqa: F811
    for lang, tie in (("en", "ties"), ("ja", "同じ値")):
        text = plain(built, f"{lang}/research.html")
        assert str(variants.PCTL_MIN_OBS) in text and tie in text


# ---- L4 / L5 -----------------------------------------------------------------------------------


@pytest.mark.parametrize("lang,word", (("ja", "予定"), ("en", "scheduled")))
def test_active_dd_rows_show_the_scheduled_expiry(built, lang, word):  # noqa: F811
    f = f_of(built)
    active = [r for r in f.dd if not r["expires_on"] and not r["invalidated_on"]]
    assert active
    page = read(built, f"{lang}/events.html")
    dd_html = page.split('id="tbl-dd"')[1].split("</table>")[0]
    assert dd_html.count(word) == len(active)
    for r in active:
        assert facts.scheduled_expiry(r["date"]) in dd_html
    top = read(built, f"{lang}/index.html")
    assert word in top


def test_ja_has_no_english_leftovers_or_day_units_for_sessions():
    ja = texts("ja")
    assert "highで" not in " ".join(ja.values())
    for k in ("col.fwd_5", "col.fwd_60", "col.horizon", "col.invalidated_60"):
        assert "日" not in ja[k], k
    assert "セッション" in ja["col.fwd_5"]


def test_definitions_page_translates_the_invalidation_price_source(built):  # noqa: F811
    assert "日中の高値" in plain(built, "ja/definitions.html")
    assert "intraday high" in plain(built, "en/definitions.html")


@pytest.mark.parametrize("lang,phrase", (("en", "HIGH or SEVERE"), ("ja", "高いか深刻")))
def test_elevated_is_defined(built, lang, phrase):  # noqa: F811
    assert phrase in plain(built, f"{lang}/research.html")


def test_dictionary_avg_exposure_and_expires_on_wording():
    md = dictionary.render_markdown()
    inv = next(line for line in md.splitlines() if line.startswith("| `B_avg_exposure`"))
    assert "not a share of time" in inv or "mean" in inv
    assert "share of time invested" not in inv
    exp = next(line for line in md.splitlines() if line.startswith("| `expires_on`"))
    assert "empty" in exp.lower() and "scheduled" in exp.lower()


def test_key_facts_json_roundtrip_is_stable(built):  # noqa: F811
    kf = pages.key_facts(f_of(built))
    assert json.loads(json.dumps(kf)) == kf


# ---- M6: key-fact gate on a real build ------------------------------------------------------


def _copy(run, tmp_path):
    import shutil

    dest = tmp_path / "run"
    shutil.copytree(run, dest)
    return dest


def test_key_facts_gate_passes_on_a_clean_build(built):  # noqa: F811
    from webgen import validate

    res = validate._check_key_facts(built)
    assert res["status"] == "PASS", res
    assert (built / "site" / "key_facts.json").is_file()


def test_key_facts_gate_fails_when_the_saved_facts_are_edited(built, tmp_path):  # noqa: F811
    from webgen import validate

    run = _copy(built, tmp_path)
    p = run / "site" / "key_facts.json"
    kf = json.loads(p.read_text())
    kf["a"]["^GSPC"]["test"]["n_trades"] += 1
    p.write_text(json.dumps(kf))
    res = validate._check_key_facts(run)
    assert res["status"] == "FAIL" and "n_trades" in res["detail"]


def test_key_facts_gate_fails_when_a_number_in_a_page_sentence_is_changed(built, tmp_path):  # noqa: F811
    from webgen import validate

    run = _copy(built, tmp_path)
    f = f_of(run)
    sentence = pages.esc(pages.render_key_points(pages.Ctx("ja", f), pages.key_facts(f))[2])
    page = run / "site" / "ja" / "index.html"
    html = page.read_text(encoding="utf-8")
    assert sentence in html
    page.write_text(html.replace(sentence, sentence.replace("%", "0%", 1)), encoding="utf-8")
    res = validate._check_key_facts(run)
    assert res["status"] == "FAIL" and "ja/index.html" in res["detail"]
