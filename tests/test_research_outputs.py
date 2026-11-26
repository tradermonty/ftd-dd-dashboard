"""Output files and the HTML report from a small synthetic pipeline run. No network."""

from __future__ import annotations

import csv
import json

from research import outputs, pipeline, report
from tests.test_research_selection import _subset, _synthetic


def _run():
    h = {"^GSPC": _synthetic(3), "^IXIC": _synthetic(4)}
    f, d = _subset()
    res = pipeline.run_pipeline(
        h,
        ftd_vars=f,
        dd_vars=d,
        design_start=h["^GSPC"][5]["date"],
        design_end=h["^GSPC"][600]["date"],
        test_start=h["^GSPC"][601]["date"],
    )
    return res, f, d, h


def test_outputs_and_report(tmp_path):
    res, f, d, h = _run()
    rng = {"first": h["^GSPC"][0]["date"], "last": res["last_date"]}
    outputs.write_csv(
        tmp_path / "variants.csv", outputs.VARIANT_COLUMNS, outputs.variants_rows(res, f, d)
    )
    outputs.write_csv(
        tmp_path / "test_results.csv", outputs.TEST_COLUMNS, outputs.test_result_rows(res)
    )
    sel = outputs.selected_json(res, f, d, rng)
    (tmp_path / "selected.json").write_text(json.dumps(sel))
    html = report.render(res, sel)

    rows = list(csv.DictReader((tmp_path / "variants.csv").open()))
    assert len(rows) == (len(f) + len(d)) * 2  # variants x indices
    assert all(r["period"] == "design" for r in rows)
    trows = list(csv.DictReader((tmp_path / "test_results.csv").open()))
    assert trows and all(r["period"] == "test" for r in trows)
    assert {r["evaluation"] for r in trows} == {"A", "B", "C"}
    assert sel["n_variants_tried"] == len(f) + len(d)
    for tag in ("<h2>3. 試した案の数", "baseline", "selected", "test", "^GSPC", "^IXIC"):
        assert tag in html
    assert "None" not in html and "nan" not in html.lower().replace("financ", "")


def test_exposure_matched_benchmark_in_outputs_and_selection_unchanged(tmp_path):
    from research import selection

    res, f, d, h = _run()
    # selection (re)computed without any em_ field equals what the pipeline selected
    stripped = [{k: v for k, v in r.items() if not k.startswith("em_")} for r in res["design"]["B"]]
    again = selection.select_b(stripped, res["baseline"]["B"])
    assert {i: c["selected"] for i, c in again.items()} == {
        i: c["selected"] for i, c in res["chosen"]["B"].items()
    }
    assert all("em_cagr" in r and "em_max_dd" in r for r in res["design"]["B"] + res["test"]["B"])

    outputs.write_csv(
        tmp_path / "variants.csv", outputs.VARIANT_COLUMNS, outputs.variants_rows(res, f, d)
    )
    outputs.write_csv(
        tmp_path / "test_results.csv", outputs.TEST_COLUMNS, outputs.test_result_rows(res)
    )
    vrows = list(csv.DictReader((tmp_path / "variants.csv").open()))
    assert {"B_em_cagr", "B_em_max_dd"} <= set(vrows[0])
    assert any(r["B_em_cagr"] != "" for r in vrows if r["family"] == "dd")
    trows = list(csv.DictReader((tmp_path / "test_results.csv").open()))
    assert {"em_cagr", "em_max_dd"} <= set(trows[0])
    assert any(r["em_cagr"] != "" for r in trows if r["evaluation"] == "B")

    sel = outputs.selected_json(res, f, d, {"first": "x", "last": res["last_date"]})
    html = report.render(res, sel)
    assert "同じ平均投資比率" in html and "投資比率一定" in html


def test_selected_json_carries_amendment_freeze_and_renamed_column():
    res, f, d, h = _run()
    rng = {"first": h["^GSPC"][0]["date"], "last": res["last_date"]}
    sel = outputs.selected_json(res, f, d, rng)
    assert sel["post_hoc_amendments"][0]["date"] == "2026-11-07"
    assert len(sel["freeze"]["grid_sha256"]) == 64
    assert sel["freeze"]["pre_registered"] == "2026-11-07"
    assert "more design trades" in sel["tie_rule"]
    assert "min_decline_frac" in outputs.VARIANT_COLUMNS
    assert "min_decline_pct" not in outputs.VARIANT_COLUMNS
    html = report.render(res, sel)
    assert "tie-break rule defined" in html and sel["freeze"]["grid_sha256"] in html
