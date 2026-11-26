"""Review fixes H4, M6, L2: history_diff cutoff, content checks, browser check, grid freeze."""

from __future__ import annotations

import csv
import json
import shutil
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish  # noqa: E402
import webfixtures as wf  # noqa: E402

from research import freeze  # noqa: E402
from webgen import validate  # noqa: E402

PREV_LAST = "2024-03-05"  # three weeks before wf.LAST (2024-03-28)


def run_gate(run, **kw):
    kw.setdefault("now", wf.NOW)
    kw.setdefault("check_site", False)
    return validate.validate_run(run, **kw)


def failed(res):
    return {c["name"] for c in res["checks"] if c["status"] == "FAIL"}


def check(res, name):
    return next(c for c in res["checks"] if c["name"] == name)


# ---- H4: history diff ------------------------------------------------------------------


def _old_events():
    return wf.ftd_rows(), wf.dd_rows()


def _new_events():
    """The old events plus 6 events that only exist because 3 more weeks of data arrived."""
    ftd, dd = _old_events()
    sess = wf.sessions()
    dd = dd + [
        dict(dd[0], date=sess[k], expires_on="", index=idx)
        for idx in wf.INDICES
        for k in (46, 47, 48)  # 2024-03-08 .. after the previous run's last session
    ]
    return ftd, dd


def test_three_week_gap_with_only_new_events_passes(tmp_path):
    ftd, dd = _old_events()
    prev = wf.build_run(tmp_path / "prev", ftd=ftd, dd=dd, through=PREV_LAST)
    nftd, ndd = _new_events()
    assert min(r["date"] for r in ndd[len(dd) :]) > PREV_LAST
    new = wf.build_run(tmp_path / "new", ftd=nftd, dd=ndd)
    res = run_gate(new, previous_dir=prev)
    assert check(res, "history_diff")["status"] == "PASS", res["diff"]
    assert res["diff"]["total_changes"] == 0 and res["diff"]["status"] == "compared"
    assert res["diff"]["cutoff"] == PREV_LAST  # min(previous last, new last)


def test_changed_old_event_is_still_counted_across_the_gap(tmp_path):
    ftd, dd = _old_events()
    prev = wf.build_run(tmp_path / "prev", ftd=ftd, dd=dd, through=PREV_LAST)
    nftd, ndd = _new_events()
    ndd[0] = dict(ndd[0], pct_change=-3.3)
    new = wf.build_run(tmp_path / "new", ftd=nftd, dd=ndd)
    res = run_gate(new, previous_dir=prev)
    assert (res["diff"]["changed"], res["diff"]["total_changes"]) == (1, 1)


def test_late_detected_ftd_with_old_date_is_not_a_change(tmp_path):
    ftd, dd = _old_events()
    prev = wf.build_run(tmp_path / "prev", ftd=ftd, dd=dd, through=PREV_LAST)
    late = dict(ftd[0], ftd_date="2024-03-04", detected_on="2024-03-12")  # seen only later
    new = wf.build_run(tmp_path / "new", ftd=[*ftd, late], dd=dd)
    assert run_gate(new, previous_dir=prev)["diff"]["total_changes"] == 0


@pytest.mark.parametrize("damage", ["missing_file", "garbled", "no_directory"])
def test_unreadable_previous_fails_instead_of_skipping(tmp_path, damage):
    prev = wf.build_run(tmp_path / "prev", through=PREV_LAST)
    new = wf.build_run(tmp_path / "new")
    if damage == "missing_file":
        (prev / "data" / "ftd_events.csv").unlink()
    elif damage == "garbled":
        (prev / "data" / "daily_status.csv").write_bytes(b"\xff\xfe\x00 not,a,csv\n\x00")
    else:
        shutil.rmtree(prev)
    res = run_gate(new, previous_dir=prev)
    assert "history_diff" in failed(res) and res["status"] == "FAIL"
    assert res["diff"]["status"] == "error"


def test_no_previous_build_still_skips(tmp_path):
    res = run_gate(wf.build_run(tmp_path / "new"), previous_dir=None)
    assert check(res, "history_diff")["status"] == "SKIP"


# ---- M6: research content checks -----------------------------------------------------------


@pytest.fixture
def run(tmp_path):
    return wf.build_run(tmp_path / "run")


def test_clean_research_files_pass(run):
    res = run_gate(run)
    assert check(res, "research_files")["status"] == "PASS", check(res, "research_files")


def _edit_json(path, fn):
    data = json.loads(path.read_text())
    fn(data)
    path.write_text(json.dumps(data))


@pytest.mark.parametrize(
    "damage",
    [
        "missing_variants",
        "missing_overlay_selected",
        "selected_id_unknown",
        "test_end_not_last_date",
        "variant_rows_missing",
        "overlay_rows_missing",
        "overlay_selected_unknown",
        "test_result_unknown_id",
    ],
)
def test_research_files_content_failures(run, damage):
    r = run / "research"
    if damage == "missing_variants":
        (r / "variants.csv").unlink()
    elif damage == "missing_overlay_selected":
        (r / "overlay_selected.json").unlink()
    elif damage == "selected_id_unknown":
        _edit_json(
            r / "selected.json", lambda d: d["evaluations"]["A"]["^GSPC"].update(selected="nope")
        )
    elif damage == "test_end_not_last_date":
        _edit_json(
            r / "selected.json", lambda d: d["split"].update(test=["2015-01-02", "2024-03-01"])
        )
    elif damage == "variant_rows_missing":
        rows = list(csv.DictReader((r / "variants.csv").open()))
        wf.write_csv(r / "variants.csv", list(rows[0]), rows[:-1])
    elif damage == "overlay_rows_missing":
        rows = list(csv.DictReader((r / "overlay_variants.csv").open()))
        wf.write_csv(r / "overlay_variants.csv", list(rows[0]), rows[:-3])
    elif damage == "overlay_selected_unknown":
        _edit_json(
            r / "overlay_selected.json", lambda d: d["indices"]["^IXIC"].update(selected="zzz")
        )
    else:
        rows = list(csv.DictReader((r / "test_results.csv").open()))
        rows[0]["variant_id"] = "ghost"
        wf.write_csv(r / "test_results.csv", list(rows[0]), rows)
    res = run_gate(run)
    assert "research_files" in failed(res), damage


# ---- L2: freeze ---------------------------------------------------------------------------------


def test_freeze_info_and_hashes_are_deterministic():
    a, b = freeze.current_hashes(), freeze.current_hashes()
    assert a == b and set(a) == {"selection", "overlay"} and all(len(h) == 64 for h in a.values())
    assert freeze.info("selection")["grid_sha256"] == a["selection"]


def test_grid_hash_changes_when_the_grid_changes():
    g = {"k": [3, 4, 5]}
    assert freeze.grid_sha256(g) != freeze.grid_sha256({"k": [3, 4, 6]})
    assert freeze.grid_sha256(g) == freeze.grid_sha256({"k": [3, 4, 5]})


def _frozen(tmp_path, **override):
    p = tmp_path / "grid_freeze.json"
    data = freeze.write(p)
    for k, v in override.items():
        data["grid_sha256"][k] = v
    p.write_text(json.dumps(data))
    return p


def _run_with_current_hashes(tmp_path):
    run = wf.build_run(tmp_path / "run")
    h = freeze.current_hashes()
    _edit_json(
        run / "research" / "selected.json", lambda d: d["freeze"].update(grid_sha256=h["selection"])
    )
    _edit_json(
        run / "research" / "overlay_selected.json",
        lambda d: d["freeze"].update(grid_sha256=h["overlay"]),
    )
    return run


def test_grid_freeze_passes_when_hashes_match(tmp_path):
    run = _run_with_current_hashes(tmp_path)
    res = run_gate(run, freeze_path=_frozen(tmp_path))
    assert check(res, "grid_freeze")["status"] == "PASS"


def test_grid_freeze_fails_when_code_grid_differs_from_frozen(tmp_path):
    run = _run_with_current_hashes(tmp_path)
    res = run_gate(run, freeze_path=_frozen(tmp_path, selection="0" * 64))
    assert "grid_freeze" in failed(res)


def test_grid_freeze_fails_when_run_records_a_different_grid(tmp_path):
    run = wf.build_run(tmp_path / "run")  # fixture records an arbitrary hash
    res = run_gate(run, freeze_path=_frozen(tmp_path))
    assert "grid_freeze" in failed(res)


def test_allow_grid_change_warns_and_records_both_hashes(tmp_path):
    run = _run_with_current_hashes(tmp_path)
    res = run_gate(run, freeze_path=_frozen(tmp_path, overlay="1" * 64), allow_grid_change=True)
    c = check(res, "grid_freeze")
    assert c["status"] == "WARN" and res["status"] == "PASS"
    assert res["grid_freeze"]["allow_grid_change"] is True
    assert res["grid_freeze"]["frozen"]["overlay"] == "1" * 64
    assert res["grid_freeze"]["current"]["overlay"] == freeze.current_hashes()["overlay"]


def test_missing_freeze_file_fails_the_gate(tmp_path):
    run = _run_with_current_hashes(tmp_path)
    assert "grid_freeze" in failed(run_gate(run, freeze_path=tmp_path / "absent.json"))


def test_freeze_write_keeps_history(tmp_path):
    p = tmp_path / "f.json"
    first = freeze.write(p)
    second = freeze.write(p, previous=first)
    assert second["history"][-1]["grid_sha256"] == first["grid_sha256"]


# ---- publish orchestration: freeze file, browser check, screens hash, WARN -----------------


def gen_ok(run_dir, **_):
    h = freeze.current_hashes()
    wf.build_run(run_dir)
    _edit_json(
        run_dir / "research" / "selected.json",
        lambda d: d["freeze"].update(grid_sha256=h["selection"]),
    )
    _edit_json(
        run_dir / "research" / "overlay_selected.json",
        lambda d: d["freeze"].update(grid_sha256=h["overlay"]),
    )


def go(build, ts, **kw):
    kw.setdefault("check_site", False)
    return publish.publish(build, generate_fn=gen_ok, run_name=ts, now=wf.NOW, **kw)


def validation(build, ts):
    return json.loads((build / "runs" / ts / "validation.json").read_text())


def test_publish_creates_the_freeze_file_on_first_run_then_enforces_it(tmp_path):
    build, fz = tmp_path / "build", tmp_path / "grid_freeze.json"
    assert not fz.exists()
    assert go(build, "20240328T220000Z", freeze_path=fz) == 0
    assert fz.exists() and json.loads(fz.read_text())["grid_sha256"] == freeze.current_hashes()
    data = json.loads(fz.read_text())
    data["grid_sha256"]["selection"] = "f" * 64
    fz.write_text(json.dumps(data))
    assert go(build, "20240329T220000Z", freeze_path=fz) == 1
    assert "grid_freeze" in failed(validation(build, "20240329T220000Z"))


def test_publish_allow_grid_change_records_and_refreezes(tmp_path):
    build, fz = tmp_path / "build", tmp_path / "grid_freeze.json"
    assert go(build, "20240328T220000Z", freeze_path=fz) == 0
    data = json.loads(fz.read_text())
    data["grid_sha256"]["overlay"] = "e" * 64
    fz.write_text(json.dumps(data))
    assert go(build, "20240329T220000Z", freeze_path=fz, allow_grid_change=True) == 0
    v = validation(build, "20240329T220000Z")
    assert check(v, "grid_freeze")["status"] == "WARN"
    assert json.loads(fz.read_text())["grid_sha256"] == freeze.current_hashes()
    assert json.loads(fz.read_text())["history"]
    assert go(build, "20240330T220000Z", freeze_path=fz) == 0  # frozen again, passes plainly


def test_no_site_check_is_a_visible_warning(tmp_path):
    build = tmp_path / "build"
    assert go(build, "20240328T220000Z", freeze_path=tmp_path / "f.json", check_site=False) == 0
    v = validation(build, "20240328T220000Z")
    warned = {c["name"] for c in v["checks"] if c["status"] == "WARN"}
    assert {"site_files", "browser_check"} <= warned
    assert "--no-site-check" in check(v, "site_files")["detail"]


def _site_stub(run_dir):
    """Minimal pages so validate._check_site passes without a real build."""
    from webgen import layout

    for rel in layout.required_site_files():
        p = run_dir / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text("x")


def test_browser_check_runs_before_hashing_and_screens_are_hashed(tmp_path):
    build = tmp_path / "build"
    seen = {}

    def fake_browser(site_dir, out_dir):
        seen["site"], seen["out"] = site_dir, out_dir
        out_dir.mkdir(parents=True, exist_ok=True)
        (out_dir / "ja_index.png").write_bytes(b"png")
        return {"status": "PASS", "detail": "12/12 pages OK"}

    def gen(run_dir, **_):
        gen_ok(run_dir)
        _site_stub(run_dir)

    code = publish.publish(
        build,
        generate_fn=gen,
        run_name="20240328T220000Z",
        now=wf.NOW,
        check_site=True,
        browser_check=fake_browser,
        keep_screens=True,
        freeze_path=tmp_path / "f.json",
        key_facts_check=False,
    )
    assert code == 0
    v = validation(build, "20240328T220000Z")
    assert check(v, "browser_check")["status"] == "PASS"
    assert "screens/ja_index.png" in v["files"]
    assert seen["site"].name == "site"
    assert not seen["out"].exists()  # the temporary folder is gone after the copy


def test_screens_are_not_stored_in_the_run_by_default(tmp_path):
    build = tmp_path / "build"

    def fake_browser(site_dir, out_dir):
        (out_dir / "ja_index.png").write_bytes(b"png")
        return {"status": "PASS", "detail": "12/12 pages OK; screenshots in /tmp/gone"}

    def gen(run_dir, **_):
        gen_ok(run_dir)
        _site_stub(run_dir)

    code = publish.publish(
        build,
        generate_fn=gen,
        run_name="20240328T220000Z",
        now=wf.NOW,
        check_site=True,
        browser_check=fake_browser,
        freeze_path=tmp_path / "f.json",
        key_facts_check=False,
    )
    assert code == 0
    run = build / "runs" / "20240328T220000Z"
    assert not (run / "screens").exists()
    v = validation(build, "20240328T220000Z")
    assert "/tmp/gone" not in check(v, "browser_check")["detail"]
    assert sum(f.stat().st_size for f in run.rglob("*") if f.is_file()) < 10 * 1024 * 1024


def test_screens_of_a_failed_browser_check_are_kept_for_diagnosis(tmp_path):
    build = tmp_path / "build"

    def bad_browser(site_dir, out_dir):
        (out_dir / "ja_index.png").write_bytes(b"png")
        return {"status": "FAIL", "detail": "chart not ready"}

    def gen(run_dir, **_):
        gen_ok(run_dir)
        _site_stub(run_dir)

    code = publish.publish(
        build,
        generate_fn=gen,
        run_name="20240328T220000Z",
        now=wf.NOW,
        check_site=True,
        browser_check=bad_browser,
        freeze_path=tmp_path / "f.json",
        key_facts_check=False,
    )
    assert code == 1
    assert (build / "runs" / "20240328T220000Z" / "screens" / "ja_index.png").is_file()


def test_a_browser_check_that_could_not_run_fails_unless_allowed(tmp_path):
    def skipped(site_dir, out_dir):
        return {"status": "SKIP", "detail": "playwright is not importable"}

    def gen(run_dir, **_):
        gen_ok(run_dir)
        _site_stub(run_dir)

    def run(name, **kw):
        return publish.publish(
            tmp_path / "build",
            generate_fn=gen,
            run_name=name,
            now=wf.NOW,
            check_site=True,
            browser_check=skipped,
            freeze_path=tmp_path / "f.json",
            key_facts_check=False,
            **kw,
        )

    assert run("20240328T220000Z") == 1
    v = validation(tmp_path / "build", "20240328T220000Z")
    assert check(v, "browser_check")["status"] == "FAIL"
    assert "--allow-no-browser" in check(v, "browser_check")["detail"]
    assert run("20240329T220000Z", allow_no_browser=True) == 0
    v = validation(tmp_path / "build", "20240329T220000Z")
    assert check(v, "browser_check")["status"] == "SKIP"


def test_failed_browser_check_fails_the_publish(tmp_path):
    build = tmp_path / "build"

    def gen(run_dir, **_):
        gen_ok(run_dir)
        _site_stub(run_dir)

    code = publish.publish(
        build,
        generate_fn=gen,
        run_name="20240328T220000Z",
        now=wf.NOW,
        check_site=True,
        browser_check=lambda s, o: {"status": "FAIL", "detail": "chart not ready"},
        freeze_path=tmp_path / "f.json",
        key_facts_check=False,
    )
    assert code == 1
    assert "browser_check" in failed(validation(build, "20240328T220000Z"))


def test_missing_playwright_is_a_recorded_skip(tmp_path):
    res = publish.run_browser_check(
        tmp_path / "site", tmp_path / "screens", python="/nonexistent/python3"
    )
    assert res["status"] == "SKIP" and "playwright" in res["detail"].lower()
