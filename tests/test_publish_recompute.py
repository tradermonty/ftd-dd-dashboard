"""The publish gate runs the results recomputation (item 4c) and fails the publish on it."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import publish  # noqa: E402
import webfixtures as wf  # noqa: E402


def gen_ok(run_dir, **_):
    wf.build_run(run_dir)


def go(build, ts, **kw):
    return publish.publish(
        build, generate_fn=gen_ok, run_name=ts, now=wf.NOW, check_site=False, **kw
    )


def check_of(build, ts, name="results_recomputed"):
    v = json.loads((build / "runs" / ts / "validation.json").read_text())
    return next(c for c in v["checks"] if c["name"] == name), v


def test_not_configured_is_recorded_as_skip(tmp_path):
    build = tmp_path / "build"
    assert go(build, "20240328T220000Z") == 0
    chk, _ = check_of(build, "20240328T220000Z")
    assert chk["status"] == "SKIP"


def test_a_failing_recompute_check_stops_the_publish(tmp_path):
    build = tmp_path / "build"
    assert go(build, "20240328T220000Z") == 0
    before = os.readlink(build / "current")

    def bad(run_dir):
        return {"name": "results_recomputed", "status": "FAIL", "detail": "numbers differ"}

    assert go(build, "20240329T220000Z", recompute_check=bad) == 1
    assert os.readlink(build / "current") == before
    chk, v = check_of(build, "20240329T220000Z")
    assert v["status"] == "FAIL" and chk["status"] == "FAIL"


def test_a_recompute_check_that_cannot_run_is_a_failure_not_a_skip(tmp_path):
    def boom(run_dir):
        raise RuntimeError("cache missing")

    build = tmp_path / "build"
    assert go(build, "20240328T220000Z", recompute_check=boom) == 1
    chk, _ = check_of(build, "20240328T220000Z")
    assert chk["status"] == "FAIL" and "cache missing" in chk["detail"]


def test_a_passing_recompute_check_is_recorded(tmp_path):
    def ok(run_dir):
        return {"name": "results_recomputed", "status": "PASS", "detail": "equal"}

    build = tmp_path / "build"
    assert go(build, "20240328T220000Z", recompute_check=ok) == 0
    chk, _ = check_of(build, "20240328T220000Z")
    assert chk["status"] == "PASS"
