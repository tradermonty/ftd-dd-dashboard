"""Publish gate (docs/plan.md section 5): each corrupted input must fail the gate."""

from __future__ import annotations

import csv
import json
import sys
from datetime import datetime
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))

import webfixtures as wf  # noqa: E402

from webgen import validate  # noqa: E402


def run_gate(run, **kw):
    kw.setdefault("now", wf.NOW)
    kw.setdefault("check_site", False)
    return validate.validate_run(run, **kw)


def failed(res):
    return {c["name"] for c in res["checks"] if c["status"] == "FAIL"}


@pytest.fixture
def run(tmp_path):
    return wf.build_run(tmp_path / "run")


def test_clean_run_passes_and_records_metadata(run):
    res = run_gate(run)
    assert res["status"] == "PASS", res["checks"]
    names = [c["name"] for c in res["checks"]]
    for n in (
        "latest_date",
        "indices_sessions",
        "dictionary_headers",
        "counts_consistent",
        "vendor_lock",
        "history_diff",
    ):
        assert n in names
    assert res["market"]["last_date"] == wf.LAST
    assert res["market"]["expected_latest_final_session"] == wf.LAST
    assert res["vendor_ref"]
    assert "data/ftd_events.csv" in res["files"]
    assert len(res["files"]["data/ftd_events.csv"]) == 64  # sha256 hex
    assert "validation.json" not in res["files"]


def test_stale_latest_date_fails(run):
    res = run_gate(run, now=datetime(2024, 4, 2, 17, 0, tzinfo=wf.ET))
    assert "latest_date" in failed(res)


def test_before_final_bar_expects_previous_session(run):
    # 03-28 15:00 ET: the 03-28 bar is not final yet, so the expected latest is 03-27
    res = run_gate(run, now=datetime(2024, 3, 28, 15, 0, tzinfo=wf.ET))
    assert "latest_date" in failed(res)


def _drop_index(run, sym):
    p = run / "data" / "daily_status.csv"
    rows = list(csv.DictReader(p.open()))
    wf.write_csv(p, list(rows[0].keys()), [r for r in rows if r["index"] != sym])


def test_missing_index_fails(run):
    _drop_index(run, "^IXIC")
    assert "indices_sessions" in failed(run_gate(run))


def test_session_mismatch_fails(run):
    p = run / "data" / "daily_status.csv"
    rows = list(csv.DictReader(p.open()))
    victim = next(r for r in rows if r["index"] == "^IXIC" and r["date"] == "2024-02-15")
    rows.remove(victim)
    wf.write_csv(p, list(rows[0].keys()), rows)
    assert "indices_sessions" in failed(run_gate(run))


def test_wrong_header_fails(run):
    p = run / "data" / "ftd_events.csv"
    text = p.read_text().replace("gain_pct", "gain", 1)
    p.write_text(text)
    assert "dictionary_headers" in failed(run_gate(run))


def test_research_header_checked_too(run):
    p = run / "research" / "variants.csv"
    p.write_text(p.read_text().replace("family", "fam", 1))
    assert "dictionary_headers" in failed(run_gate(run))


def test_missing_dictionary_fails(run):
    (run / "data" / "data_dictionary.md").unlink()
    assert "dictionary_headers" in failed(run_gate(run))


def test_ftd_count_mismatch_fails(run):
    p = run / "data" / "summary.json"
    s = json.loads(p.read_text())
    s["counts"]["ftd_events"] += 1
    p.write_text(json.dumps(s))
    assert "counts_consistent" in failed(run_gate(run))


def test_dd_count_mismatch_fails(run):
    p = run / "data" / "summary.json"
    s = json.loads(p.read_text())
    s["per_index"]["^GSPC"]["distribution_days"] += 2
    p.write_text(json.dumps(s))
    assert "counts_consistent" in failed(run_gate(run))


def test_tampered_vendor_file_fails(run, tmp_path):
    vend = wf.copy_vendor(tmp_path / "vendor_copy")
    # the gate reads <vendor_root>/vendor/...; mirror that layout
    root = tmp_path / "vroot"
    root.mkdir()
    vend.rename(root / "vendor")
    assert run_gate(run, vendor_root=root)["status"] == "PASS"
    f = root / "vendor" / "ftd_detector" / "rally_tracker.py"
    f.write_text(f.read_text() + "\n# tampered\n")
    res = run_gate(run, vendor_root=root)
    assert "vendor_lock" in failed(res) and res["status"] == "FAIL"


def test_vendor_ref_mismatch_with_summary_fails(tmp_path):
    run = wf.build_run(tmp_path / "r", vendor_ref="0" * 40)
    assert "vendor_lock" in failed(run_gate(run))


# ---- history diff ------------------------------------------------------------------


def _prev_and_new(tmp_path, mutate):
    prev = wf.build_run(tmp_path / "prev")
    ftd, dd = wf.ftd_rows(), wf.dd_rows()
    mutate(ftd, dd)
    new = wf.build_run(tmp_path / "new", ftd=ftd, dd=dd)
    return prev, new


def test_first_run_skips_diff_and_records_it(run):
    res = run_gate(run, previous_dir=None)
    chk = next(c for c in res["checks"] if c["name"] == "history_diff")
    assert chk["status"] == "SKIP" and res["status"] == "PASS"
    assert res["diff"]["status"] == "skipped"
    assert "no previous" in res["diff"]["reason"]


def test_identical_previous_has_zero_changes(tmp_path):
    prev, new = _prev_and_new(tmp_path, lambda f, d: None)
    res = run_gate(new, previous_dir=prev)
    assert res["status"] == "PASS"
    assert res["diff"]["total_changes"] == 0


def test_newest_session_events_are_not_counted(tmp_path):
    def mutate(ftd, dd):
        # a DD on the newest session exists only in the new build -> ignored
        dd.append(dict(dd[0], date=wf.LAST, expires_on=""))

    prev, new = _prev_and_new(tmp_path, mutate)
    res = run_gate(new, previous_dir=prev)
    assert res["diff"]["total_changes"] == 0 and res["status"] == "PASS"


def test_state_evolution_columns_are_not_changes(tmp_path):
    def mutate(ftd, dd):
        dd[0]["invalidated_on"] = "2024-03-01"
        dd[0]["removal_reason"] = "invalidated_5pct_gain"
        ftd[0]["invalidation_date"] = "2024-03-05"
        ftd[0]["invalidated_within_60"] = True

    prev, new = _prev_and_new(tmp_path, mutate)
    assert run_gate(new, previous_dir=prev)["diff"]["total_changes"] == 0


def test_historical_changes_counted_and_limit_enforced(tmp_path):
    def mutate(ftd, dd):
        del dd[0]  # removed
        dd[1]["pct_change"] = -0.9  # changed
        ftd.append(dict(ftd[0], ftd_date="2024-01-30", detected_on="2024-01-30"))  # added

    prev, new = _prev_and_new(tmp_path, mutate)
    res = run_gate(new, previous_dir=prev, max_historical_changes=5)
    assert res["status"] == "PASS"
    d = res["diff"]
    assert (d["added"], d["removed"], d["changed"], d["total_changes"]) == (1, 1, 1, 3)
    res = run_gate(new, previous_dir=prev, max_historical_changes=2)
    assert "history_diff" in failed(res) and res["status"] == "FAIL"


def test_too_many_historical_changes_fail_by_default(tmp_path):
    def mutate(ftd, dd):
        for r in dd[:6]:
            r["pct_change"] = -3.0

    prev, new = _prev_and_new(tmp_path, mutate)
    assert validate.MAX_HISTORICAL_CHANGES == 5
    res = run_gate(new, previous_dir=prev)
    assert res["diff"]["total_changes"] == 6 and "history_diff" in failed(res)


def test_validation_json_hashes_every_file(run):
    res = run_gate(run)
    on_disk = {p.relative_to(run).as_posix() for p in run.rglob("*") if p.is_file()}
    assert set(res["files"]) == on_disk
