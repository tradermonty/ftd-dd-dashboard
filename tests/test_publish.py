"""scripts/publish.py orchestration: new run dir, validation.json, atomic symlink repoint."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish  # noqa: E402
import webfixtures as wf  # noqa: E402


def gen_ok(run_dir, **_):
    wf.build_run(run_dir)


def gen_bad(run_dir, **_):
    wf.build_run(run_dir)
    p = run_dir / "data" / "summary.json"
    s = json.loads(p.read_text())
    s["counts"]["ftd_events"] += 7
    p.write_text(json.dumps(s))


def go(build, gen, ts, **kw):
    return publish.publish(
        build,
        generate_fn=gen,
        run_name=ts,
        now=wf.NOW,
        check_site=False,
        **kw,
    )


def test_pass_creates_run_writes_validation_and_repoints_current(tmp_path):
    build = tmp_path / "build"
    code = go(build, gen_ok, "20240328T220000Z")
    assert code == 0
    cur = build / "current"
    assert cur.is_symlink()
    assert cur.resolve() == (build / "runs" / "20240328T220000Z").resolve()
    v = json.loads((cur / "validation.json").read_text())
    assert v["status"] == "PASS" and v["market"]["last_date"] == wf.LAST
    assert v["diff"]["status"] == "skipped"
    assert v["vendor_ref"] and v["generated_at"] and v["files"]
    assert not os.path.isabs(os.readlink(cur))  # relative link: the build dir stays movable


def test_fail_leaves_current_untouched_and_exits_nonzero(tmp_path):
    build = tmp_path / "build"
    assert go(build, gen_ok, "20240328T220000Z") == 0
    before = os.readlink(build / "current")
    code = go(build, gen_bad, "20240329T220000Z")
    assert code == 1
    assert os.readlink(build / "current") == before
    failed = build / "runs" / "20240329T220000Z"
    v = json.loads((failed / "validation.json").read_text())
    assert v["status"] == "FAIL"
    assert any(c["name"] == "counts_consistent" and c["status"] == "FAIL" for c in v["checks"])


def test_first_run_fail_creates_no_current(tmp_path):
    build = tmp_path / "build"
    assert go(build, gen_bad, "20240328T220000Z") == 1
    assert not (build / "current").exists() and not (build / "current").is_symlink()


def test_existing_run_dir_is_refused(tmp_path):
    build = tmp_path / "build"
    (build / "runs" / "20240328T220000Z").mkdir(parents=True)
    with pytest.raises(FileExistsError):
        go(build, gen_ok, "20240328T220000Z")


def test_diff_uses_previous_current(tmp_path):
    build = tmp_path / "build"
    assert go(build, gen_ok, "20240328T220000Z") == 0

    def gen_changed(run_dir, **_):
        dd = wf.dd_rows()
        for r in dd[:7]:
            r["pct_change"] = -4.0
        wf.build_run(run_dir, dd=dd)

    assert go(build, gen_changed, "20240329T220000Z") == 1
    v = json.loads((build / "runs" / "20240329T220000Z" / "validation.json").read_text())
    assert v["diff"]["total_changes"] == 7 and v["diff"]["previous"] == "20240328T220000Z"
    assert os.readlink(build / "current").endswith("20240328T220000Z")


def test_generation_exception_is_a_failure_not_a_crash(tmp_path):
    def boom(run_dir, **_):
        raise RuntimeError("fetch exploded")

    build = tmp_path / "build"
    assert go(build, gen_ok, "20240328T220000Z") == 0
    assert go(build, boom, "20240329T220000Z") == 1
    v = json.loads((build / "runs" / "20240329T220000Z" / "validation.json").read_text())
    assert v["status"] == "FAIL" and "fetch exploded" in json.dumps(v["checks"])
    assert os.readlink(build / "current").endswith("20240328T220000Z")


def test_current_that_is_a_real_directory_is_not_replaced(tmp_path):
    build = tmp_path / "build"
    (build / "current").mkdir(parents=True)
    assert go(build, gen_ok, "20240328T220000Z") == 1
    assert (build / "current").is_dir() and not (build / "current").is_symlink()


def test_default_run_name_is_utc_timestamp():
    name = publish.default_run_name()
    assert len(name) == 16 and name.endswith("Z") and name[8] == "T"


def test_main_passes_use_cache_through(tmp_path, monkeypatch):
    seen = {}

    def fake_generate(run_dir, **kw):
        seen.update(kw)
        wf.build_run(run_dir)

    monkeypatch.setattr(publish, "generate", fake_generate)
    # never touch the repository's real freeze file from a test
    monkeypatch.setattr(publish.freeze_mod, "FREEZE_PATH", tmp_path / "grid_freeze.json")
    publish.main(["--build-dir", str(tmp_path / "b"), "--use-cache", "--no-site-check"])
    assert seen["use_cache"] is True
    assert (tmp_path / "grid_freeze.json").exists()
