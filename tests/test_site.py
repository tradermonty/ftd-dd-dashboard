"""Site: i18n parity, data dictionary, and a full build on the real P3/R outputs (no network)."""

from __future__ import annotations

import csv
import json
import re
import shutil
from pathlib import Path

import pytest

from webgen import dictionary, i18n, layout, pages, validate

ROOT = Path(__file__).resolve().parents[1]
BUILD = ROOT / "build"
PLACEHOLDER = re.compile(r"\{([a-zA-Z_0-9]+)\}")


# ------------------------------------------------------------------------------------ i18n


def test_ja_and_en_have_identical_key_sets():
    ja, en = i18n.load("ja"), i18n.load("en")
    assert set(ja) == set(en), sorted(set(ja) ^ set(en))


def test_ja_and_en_use_identical_placeholders():
    ja, en = i18n.load("ja"), i18n.load("en")
    bad = {k: (PLACEHOLDER.findall(ja[k]), PLACEHOLDER.findall(en[k])) for k in ja}
    bad = {k: v for k, v in bad.items() if set(v[0]) != set(v[1])}
    assert not bad, bad


def test_no_empty_text():
    for lang in layout.LANGS:
        assert all(v.strip() for v in i18n.load(lang).values())


def test_japanese_text_has_no_heavy_parentheses():
    # at most one full-width parenthetical per string (house style)
    for k, v in i18n.load("ja").items():
        assert v.count("（") <= 2, k


# ---------------------------------------------------------------------------- dictionary


def test_dictionary_covers_code_columns_and_roundtrips():
    md = dictionary.render_markdown()
    parsed = dictionary.parse_markdown(md)
    assert set(parsed) == set(dictionary.files())
    for path, cols in dictionary.files().items():
        assert parsed[path] == [c for c, _ in cols]
        for name, (typ, _unit, desc) in cols:
            assert typ and desc, (path, name)
    assert "percent (1.5 = 1.5%)" in md and "fraction (0.01 = 1%)" in md


@pytest.fixture(scope="module")
def real_run(tmp_path_factory):
    needed = ["daily_status.csv", "ftd_events.csv", "distribution_days.csv", "summary.json"]
    if not all((BUILD / n).exists() for n in needed) or not (BUILD / "research").exists():
        pytest.skip("P3/R build outputs not present")
    run = tmp_path_factory.mktemp("run")
    (run / "data").mkdir()
    for p in BUILD.iterdir():
        if p.is_file() and p.suffix in (".csv", ".json"):
            shutil.copyfile(p, run / "data" / p.name)
    shutil.copytree(BUILD / "research", run / "research")
    (run / "data" / "data_dictionary.md").write_text(dictionary.render_markdown())
    return run


def test_real_csv_headers_match_dictionary(real_run):
    for path in dictionary.files():
        with (real_run / path).open(newline="") as f:
            assert next(csv.reader(f)) == dictionary.columns(path), path


# ------------------------------------------------------------------------------ site build


@pytest.fixture(scope="module")
def built(real_run):
    from webgen import site

    site.build_site(real_run)
    return real_run


def read(run, rel):
    return (run / "site" / rel).read_text(encoding="utf-8")


def test_all_required_files_exist(built):
    assert validate._check_site(built)["status"] == "PASS"


def test_pages_have_matching_structure_in_both_languages(built):
    for p in layout.PAGES:
        ja, en = read(built, f"ja/{p}.html"), read(built, f"en/{p}.html")
        assert '<html lang="ja">' in ja and '<html lang="en">' in en
        assert ja.count("<h2") == en.count("<h2"), p
        assert ja.count("<table") == en.count("<table"), p
        assert f"../en/{p}.html" in ja and f"../ja/{p}.html" in en


def test_no_unfilled_placeholders(built):
    for lang in layout.LANGS:
        for p in layout.PAGES:
            text = re.sub(r"<script.*?</script>", "", read(built, f"{lang}/{p}.html"), flags=re.S)
            assert not PLACEHOLDER.search(text), (lang, p, PLACEHOLDER.search(text).group(0))


def test_only_pinned_plotly_is_external(built):
    for p in layout.PAGES:
        for lang in layout.LANGS:
            html = read(built, f"{lang}/{p}.html")
            urls = re.findall(r'(?:src|href)="(https?://[^"]+)"', html)
            if p == "chart":
                assert urls == [
                    "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
                ]
                assert 'integrity="sha384-' in html
            else:
                assert urls == [], (lang, p)
    for a in layout.ASSETS:
        assert "http" not in read(built, f"assets/{a}").replace("https://www.w3.org", "")


def test_research_numbers_come_from_the_data(built):
    sel = json.loads((built / "research" / "selected.json").read_text())
    n_total = sel["variant_counts"]["total_variants"]
    b = sel["evaluations"]["A"]["^GSPC"]["test"]["baseline"]
    for lang in layout.LANGS:
        html = read(built, f"{lang}/research.html")
        assert f"{n_total}" in html
        assert f"{pages.pct(b['win_rate'], 0)}" in html
        assert f"n={b['n_trades']}" not in html  # A uses 'trades', not n=
        assert pages.pct(sel["evaluations"]["B"]["^GSPC"]["test"]["baseline"]["em_max_dd"]) in html
        for sec in ("conclusions", "caveats", "blocks", "eval-a", "eval-b", "eval-c"):
            assert f'id="{sec}"' in html
        # calendar-year block stats and exposure-matched benchmark are rendered
        assert html.count("<table") >= 8


def test_top_page_state_matches_last_row(built):
    rows = list(csv.DictReader((built / "data" / "daily_status.csv").open()))
    last = [r for r in rows if r["index"] == "^GSPC"][-1]
    html = read(built, "en/index.html")
    assert last["date"] in html
    assert f"{last['d5']} / {last['d15']} / {last['d25']}" in html
    assert last["risk_level"] in html
    assert "personal local use" in html and "FMP" in html


def test_events_tables_are_filled_and_counts_match(built):
    html = read(built, "ja/events.html")
    ftd_rows = len(list(csv.DictReader((built / "data" / "ftd_events.csv").open())))
    dd_rows = len(list(csv.DictReader((built / "data" / "distribution_days.csv").open())))
    ftd_html = html.split('id="tbl-ftd"')[1].split("</table>")[0]
    dd_html = html.split('id="tbl-dd"')[1].split("</table>")[0]
    assert ftd_html.count("<tr data-index") == ftd_rows
    assert dd_html.count("<tr data-index") == dd_rows
    assert 'id="f-index"' in html and 'id="f-year"' in html
    assert '<script src="../assets/events.js">' in html


def test_definitions_cite_vendored_thresholds(built):
    from webgen import facts

    d = facts.definition_values()
    assert d["gain_min"] == 1.25 and d["min_correction_pct"] == 3.0  # from vendor, not typed here
    html = read(built, "en/definitions.html")
    assert f"{d['gain_min']:g}%" in html
    assert str(d["analysis_bars"]) in html and str(d["swing_lookback"]) in html
    shares = facts.base_rates(facts.load_facts(built))
    assert pages.pct(shares[0]["dd_share"]) in html


def test_data_page_links_and_llms_txt(built):
    html = read(built, "en/data.html")
    for name in ("ftd_events.csv", "data_dictionary.md", "variants.csv"):
        assert name in html
    txt = read(built, "llms.txt")
    assert (
        "data_dictionary.md" in txt
        and "NOT official IBD" in txt
        and "must not be redistributed" in txt
    )


def test_chart_data_has_events_stars_and_bands(built):
    js = read(built, "assets/chart_data.js")
    assert js.startswith("window.CHART_DATA = ")
    data = json.loads(js[len("window.CHART_DATA = ") : -2])
    ev = list(csv.DictReader((built / "data" / "ftd_events.csv").open()))
    for sym, d in data.items():
        n = sum(1 for r in ev if r["index"] == sym)
        assert len(d["ftd_date"]) == n and len(d["ftd_detected"]) == n
        assert d["bands"] and len(d["date"]) == len(d["close"]) == len(d["d25"])


def test_chooser_links_both_languages(built):
    html = read(built, "index.html")
    assert 'href="ja/index.html"' in html and 'href="en/index.html"' in html


# ------------------------------------------------------------------------- research R2 overlay


def _overlay(built):
    p = built / "research" / "overlay_selected.json"
    if not p.exists():
        pytest.skip("R2 overlay outputs not present")
    return json.loads(p.read_text())


def test_overlay_chapter_numbers_come_from_the_data(built):
    ov = _overlay(built)
    main = ov["main_cost_model"]
    for lang in layout.LANGS:
        html = read(built, f"{lang}/research.html")
        assert 'id="overlay"' in html
        assert str(ov["n_variants"]) in html
        for info in ov["indices"].values():
            if info["fallback_to_baseline"]:
                continue
            r = info["test"]["selected"][main]
            assert pages.pct(r["cagr"]) in html and pages.pct(r["bh_cagr"]) in html
            assert pages.pct(r["em_cagr"]) in html
            assert info["selected"] in html
        assert pages.pct(ov["maintenance_equity_ratio"], 0) in html
        for name in ("overlay_variants.csv", "overlay_test_results.csv", "overlay_selected.json"):
            assert name in html
    assert "research, not a trading recommendation" in read(built, "en/research.html")
    assert "売買をすすめるものではありません" in read(built, "ja/research.html")


def test_overlay_chart_band_payload_and_toggle(built):
    ov = _overlay(built)
    js = read(built, "assets/chart_data.js")
    data = json.loads(js[len("window.CHART_DATA = ") : -2])
    for sym, info in ov["indices"].items():
        if info["bands"]:
            assert len(data[sym]["lev"]["bands"]) == len(info["bands"])
            assert data[sym]["lev"]["label"] == info["selected"]
    for lang in layout.LANGS:
        assert 'id="lev-toggle"' in read(built, f"{lang}/chart.html")
