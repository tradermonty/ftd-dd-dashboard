"""Daily-run readiness: retention, Playwright resolution, NEEDS_REVIEW, FRED refresh, rate-data
and upstream-vendor gate checks, the wrapper script and the launchd template. No network."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib  # noqa: F401  (python >= 3.11)
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import publish  # noqa: E402
import webfixtures as wf  # noqa: E402

from engine import retention  # noqa: E402
from research import overlay  # noqa: E402
from webgen import validate  # noqa: E402

NOW = datetime(2026, 11, 20, 12, 0, tzinfo=UTC)


def name_of(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).strftime("%Y%m%dT%H%M%SZ")


def make_run(build: Path, days_ago: float, status: str | None, size: int = 10) -> Path:
    d = build / "runs" / name_of(days_ago)
    d.mkdir(parents=True)
    (d / "data.bin").write_bytes(b"x" * size)
    if status is not None:
        (d / "validation.json").write_text(json.dumps({"status": status}))
    return d


def point_current(build: Path, run: Path) -> None:
    link = build / "current"
    link.symlink_to(os.path.relpath(run, build))


# ------------------------------------------------------------------ retention


def test_retention_keeps_the_last_ten_pass_runs_and_the_current_target(tmp_path):
    build = tmp_path / "build"
    runs = [make_run(build, 30 - i, "PASS") for i in range(14)]  # 14 PASS runs, oldest first
    # the current target is an old run: it must stay although it is outside the newest ten
    point_current(build, runs[1])
    deleted = retention.prune(build, now=NOW)
    left = sorted(d.name for d in (build / "runs").iterdir())
    assert runs[1].name in left  # the current target
    assert [r.name for r in runs[4:]] == [n for n in left if n != runs[1].name]  # newest ten
    assert sorted(deleted) == sorted(r.name for r in (runs[0], *runs[2:4]))


def test_retention_deletes_failed_runs_after_seven_days_only(tmp_path):
    build = tmp_path / "build"
    good = make_run(build, 1, "PASS")
    point_current(build, good)
    young = make_run(build, 3, "FAIL")
    old = make_run(build, 8, "FAIL")
    no_validation_old = make_run(build, 9, None)  # a crash before validation.json
    no_validation_new = make_run(build, 0.01, None)  # still being written
    retention.prune(build, now=NOW)
    left = {d.name for d in (build / "runs").iterdir()}
    assert left == {good.name, young.name, no_validation_new.name}
    assert old.name not in left and no_validation_old.name not in left


def test_retention_never_deletes_the_current_target_even_if_it_is_old_and_failed(tmp_path):
    build = tmp_path / "build"
    cur = make_run(build, 40, "FAIL")
    point_current(build, cur)
    assert retention.prune(build, now=NOW) == []
    assert cur.exists()


def test_retention_survives_a_malformed_validation_json_and_a_symlinked_run(tmp_path):
    """Retention runs after build/current was repointed: a bad entry must not crash it."""
    build = tmp_path / "build"
    keep = make_run(build, 0.1, "PASS")
    point_current(build, keep)
    bad = make_run(build, 9, None)
    (bad / "validation.json").write_text("[]", encoding="utf-8")  # valid JSON, not an object
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    (outside / "keep.txt").write_text("x", encoding="utf-8")
    link = build / "runs" / name_of(10)
    link.symlink_to(outside, target_is_directory=True)
    assert retention.run_status(bad) == "NONE"
    deleted = retention.prune(build, now=NOW)  # must not raise
    assert bad.name in deleted and link.name not in deleted
    assert (outside / "keep.txt").exists() and link.is_symlink()
    (outside / "screens").mkdir()
    assert retention.drop_other_screens(build, keep) == []  # a symlinked run is left alone
    assert (outside / "screens").is_dir()


def test_publish_uses_one_clock_for_fetch_and_validation(tmp_path, monkeypatch):
    """Fetch and the latest_date gate must agree on 'now': a run that starts before the
    16:30 ET bar is final and validates after it would otherwise fail latest_date."""
    seen = {}

    def gen(run_dir, *, now=None, **_):
        seen["generate"] = now
        raise RuntimeError("stop after recording the clock")

    real = validate.validate_run

    def spy(run_dir, *, now=None, **kw):
        seen["validate"] = now
        return real(run_dir, now=now, **kw)

    monkeypatch.setattr(publish.validate, "validate_run", spy)
    publish.publish(tmp_path / "build", generate_fn=gen, check_site=False, prune_runs=False,
                    logs_dir=tmp_path / "logs")
    assert seen["generate"] is not None and seen["generate"] == seen["validate"]


def test_drop_other_screens_keeps_only_the_given_runs(tmp_path):
    build = tmp_path / "build"
    a, b = make_run(build, 2, "PASS"), make_run(build, 1, "PASS")
    for r in (a, b):
        (r / "screens").mkdir()
        (r / "screens" / "x.png").write_bytes(b"png")
    assert retention.drop_other_screens(build, b) == [a.name]
    assert not (a / "screens").exists() and (b / "screens").exists()


def test_publish_prunes_old_runs_after_a_pass(tmp_path):
    build = tmp_path / "build"
    for d in range(1, 13):  # twelve PASS runs made earlier in March 2024
        r = build / "runs" / f"202403{d:02d}T000000Z"
        r.mkdir(parents=True)
        (r / "validation.json").write_text(json.dumps({"status": "PASS"}))

    def publish_run(name, **kw):
        return publish.publish(
            build,
            generate_fn=lambda run_dir, **_: wf.build_run(run_dir),
            run_name=name,
            now=wf.NOW,
            check_site=False,
            upstream_root=None,
            log=lambda m: None,
            **kw,
        )

    assert publish_run("20240327T220000Z", prune_runs=False) == 0
    assert len(list((build / "runs").iterdir())) == 13  # nothing pruned when switched off
    assert publish_run("20240328T220000Z") == 0
    left = sorted(d.name for d in (build / "runs").iterdir())
    assert len(left) == 10 and left[-1] == "20240328T220000Z" and left[-2] == "20240327T220000Z"
    assert (build / "current").resolve().name == "20240328T220000Z"


# ------------------------------------------------------------------ Playwright python


def test_playwright_python_env_var_is_used_and_a_wrong_one_is_reported(monkeypatch):
    py, how = publish.resolve_playwright_python({publish.PLAYWRIGHT_ENV: sys.executable})
    assert py is None and "cannot import playwright" in how  # the venv python has none
    py, how = publish.resolve_playwright_python({publish.PLAYWRIGHT_ENV: "/nonexistent/python3"})
    assert py is None and publish.PLAYWRIGHT_ENV in how


def test_playwright_python_auto_detection_tries_import_playwright(monkeypatch):
    seen = []

    def fake(py):
        seen.append(py)
        return py == "/opt/pw/python3"

    monkeypatch.setattr(publish, "_can_import_playwright", fake)
    monkeypatch.setattr(publish.shutil, "which", lambda name: "/usr/bin/python3")
    py, how = publish.resolve_playwright_python({}, candidates=["/opt/pw/python3"])
    assert py == "/opt/pw/python3" and how == "auto-detected"
    assert seen[0] == "/usr/bin/python3"  # python3 on PATH is tried first
    monkeypatch.setattr(publish, "_can_import_playwright", lambda py: False)
    py, how = publish.resolve_playwright_python({}, candidates=["/opt/pw/python3"])
    assert py is None and publish.PLAYWRIGHT_ENV in how


def test_run_browser_check_skips_with_the_reason_when_no_python_has_playwright(
    tmp_path, monkeypatch
):
    monkeypatch.setattr(publish, "resolve_playwright_python", lambda: (None, "no interpreter"))
    res = publish.run_browser_check(tmp_path / "site", tmp_path / "out")
    assert res["status"] == "SKIP" and "no interpreter" in res["detail"]


# ------------------------------------------------------------------ NEEDS_REVIEW marker


def _gen_changed(run_dir, **_):
    """A run whose history differs from wf.ftd_rows(): every FTD of 2024-01-1x is moved."""
    changed = wf.ftd_rows()
    for r in changed:
        r["gain_pct"] = 3.3
    wf.build_run(run_dir, ftd=changed)


def test_history_diff_over_the_limit_writes_needs_review_and_a_log(tmp_path):
    build, logs = tmp_path / "build", tmp_path / "logs"
    lines = []
    ok = publish.publish(
        build,
        generate_fn=lambda run_dir, **_: wf.build_run(
            run_dir, through="2024-03-05", last="2024-03-05"
        ),
        run_name="20240305T220000Z",
        now=datetime(2024, 3, 5, 17, 0, tzinfo=wf.ET),
        check_site=False,
        upstream_root=None,
        log=lambda m: None,
    )
    assert ok == 0
    code = publish.publish(
        build,
        generate_fn=_gen_changed,
        run_name="20240328T220000Z",
        now=wf.NOW,
        check_site=False,
        max_historical_changes=2,
        upstream_root=None,
        logs_dir=logs,
        log=lines.append,
    )
    assert code == 1
    marker = build / "NEEDS_REVIEW"
    assert marker.is_file() and "20240328T220000Z" in marker.read_text()
    assert "historical changes" in marker.read_text()
    assert any(m.startswith("NEEDS REVIEW:") for m in lines)  # a clear message in the log
    assert "changed" in (logs / "needs_review.log").read_text()
    # the next run that passes removes the marker
    code = publish.publish(
        build,
        generate_fn=lambda run_dir, **_: wf.build_run(run_dir),
        run_name="20240329T220000Z",
        now=wf.NOW,
        check_site=False,
        max_historical_changes=100,
        upstream_root=None,
        log=lambda m: None,
    )
    assert code == 0 and not marker.exists()


def test_scheduled_job_refuses_no_site_check_and_allow_no_browser(monkeypatch, capsys):
    monkeypatch.setenv(publish.SCHEDULED_ENV, "1")
    assert publish.main(["--no-site-check", "--build-dir", "/nonexistent"]) == 2
    assert publish.main(["--allow-no-browser", "--build-dir", "/nonexistent"]) == 2
    assert "FTD_DD_SCHEDULED" in capsys.readouterr().err


# ------------------------------------------------------------------ FRED refresh


def write_dff(path: Path, last_obs: str, first: str = "2026-01-01") -> None:
    wf.write_fred(path, last_obs, first=first)


SESSIONS = ["2026-11-02", "2026-11-03", "2026-11-04", "2026-11-05", "2026-11-06"]


def fake_fred(through: str):
    def fetch(url: str) -> str:
        assert url == overlay.FRED_URL
        d, out = datetime.fromisoformat("2026-01-01"), ["observation_date,DFF"]
        end = datetime.fromisoformat(through)
        while d <= end:
            out.append(f"{d.date().isoformat()},3.88")
            d += timedelta(days=1)
        return "\n".join(out) + "\n"

    return fetch


def test_fred_file_is_refreshed_when_its_last_observation_is_before_the_second_to_last_session(
    tmp_path,
):
    p = tmp_path / "fred_DFF.csv"
    write_dff(p, "2026-11-03")  # second-to-last session is 2026-11-05
    calls = []

    def fetch(url):
        calls.append(url)
        return fake_fred("2026-11-05")(url)

    rows = overlay.load_or_fetch_dff(p, fetch, dates=SESSIONS, allow_network=True, now=NOW)
    assert calls == [overlay.FRED_URL] and rows[-1][0] == "2026-11-05"


def test_fred_file_that_is_current_is_not_downloaded_again(tmp_path):
    p = tmp_path / "fred_DFF.csv"
    write_dff(p, "2026-11-05")
    now = datetime.now(UTC)  # a file just written is fresh
    rows = overlay.load_or_fetch_dff(
        p, lambda url: pytest.fail("downloaded"), dates=SESSIONS, allow_network=True, now=now
    )
    assert rows[-1][0] == "2026-11-05"


def test_fred_file_older_than_three_days_is_refreshed_when_the_network_is_allowed(tmp_path):
    p = tmp_path / "fred_DFF.csv"
    write_dff(p, "2026-11-05")
    old = (NOW - timedelta(days=4)).timestamp()
    os.utime(p, (old, old))
    called = []
    overlay.load_or_fetch_dff(
        p, lambda u: called.append(u) or fake_fred("2026-11-05")(u), dates=SESSIONS, now=NOW
    )
    assert called


def test_stale_fred_under_use_cache_fails_with_a_clear_message(tmp_path):
    p = tmp_path / "fred_DFF.csv"
    write_dff(p, "2026-11-03")
    with pytest.raises(overlay.StaleRateData) as err:
        overlay.load_or_fetch_dff(
            p, lambda u: pytest.fail("network"), dates=SESSIONS, allow_network=False, now=NOW
        )
    assert "--refresh-fred" in str(err.value) and "2026-11-03" in str(err.value)
    # a merely old file whose observations still cover the data is accepted offline
    write_dff(p, "2026-11-05")
    os.utime(p, ((NOW - timedelta(days=9)).timestamp(),) * 2)
    assert overlay.load_or_fetch_dff(
        p, lambda u: pytest.fail("network"), dates=SESSIONS, allow_network=False, now=NOW
    )


def test_fred_file_covering_the_rate_the_last_return_needs_is_enough(tmp_path):
    """The last return uses the rate dated on or before dates[-3] (rate_known lag 1 + rates[k-1]),
    the same rule as the rate_data gate. A Fed holiday on an NYSE session (Veterans Day) leaves
    the file one observation short of dates[-2]: that must not stop an offline recompute."""
    p = tmp_path / "fred_DFF.csv"
    write_dff(p, "2026-11-04")  # dates[-3] of SESSIONS
    assert overlay.dff_staleness(p, SESSIONS, now=datetime.now(UTC))["hard"] is None
    rows = overlay.load_or_fetch_dff(
        p, lambda u: pytest.fail("network"), dates=SESSIONS, allow_network=False,
        now=datetime.now(UTC),
    )
    assert rows[-1][0] == "2026-11-04"


def test_a_refresh_that_is_still_insufficient_fails_like_the_offline_path(tmp_path):
    """Network and offline paths must agree: a download that still lacks dates[-3] is an error."""
    p = tmp_path / "fred_DFF.csv"
    write_dff(p, "2026-11-02")
    with pytest.raises(overlay.StaleRateData) as err:
        overlay.load_or_fetch_dff(p, fake_fred("2026-11-03"), dates=SESSIONS, now=NOW)
    assert "2026-11-03" in str(err.value)
    with pytest.raises(overlay.StaleRateData) as err:
        overlay.load_or_fetch_dff(
            p, lambda u: pytest.fail("network"), dates=SESSIONS, allow_network=False, now=NOW
        )
    # the gate recompute is offline without --use-cache: the message must not claim the flag
    assert "(--use-cache)" not in str(err.value) and "--refresh-fred" in str(err.value)


def test_a_short_download_does_not_replace_a_sufficient_but_old_file(tmp_path):
    """A refresh triggered only by the file's age must not swap a good cache for a worse one."""
    p = tmp_path / "fred_DFF.csv"
    write_dff(p, "2026-11-05")
    old = (NOW - timedelta(days=5)).timestamp()
    os.utime(p, (old, old))
    before = p.read_text()
    rows = overlay.load_or_fetch_dff(p, fake_fred("2026-10-30"), dates=SESSIONS, now=NOW)
    assert p.read_text() == before and rows[-1][0] == "2026-11-05"
    assert not p.with_name(p.name + ".tmp").exists()


def test_failed_refresh_of_an_insufficient_file_is_an_error_not_a_silent_keep(tmp_path):
    p = tmp_path / "fred_DFF.csv"
    write_dff(p, "2026-11-03")

    def boom(url):
        raise OSError("offline")

    with pytest.raises(overlay.StaleRateData):
        overlay.load_or_fetch_dff(p, boom, dates=SESSIONS, now=NOW)


def test_a_bad_download_does_not_replace_the_cache(tmp_path):
    p = tmp_path / "fred_DFF.csv"
    write_dff(p, "2026-11-05")
    old = (NOW - timedelta(days=5)).timestamp()
    os.utime(p, (old, old))
    before = p.read_text()
    overlay.load_or_fetch_dff(p, lambda u: "<html>error</html>", dates=SESSIONS, now=NOW)
    assert p.read_text() == before  # sufficient file kept when the refresh fails


# ------------------------------------------------------------------ gate: rate_data


def _rate_check(tmp_path, last_obs: str, through: str = wf.LAST):
    run = wf.build_run(tmp_path / "run", through=through)
    wf.write_fred(run / "research" / "fred_DFF.csv", last_obs)
    return validate._check_rate_data(run)


def test_rate_data_passes_when_the_needed_observation_exists(tmp_path):
    res = _rate_check(tmp_path, "2024-03-26")  # sessions end 03-28, need 03-26
    assert res["status"] == "PASS" and "no rate is carried forward" in res["detail"]


def test_rate_data_warns_for_the_publication_lag_and_fails_beyond_three_business_days(tmp_path):
    warn = _rate_check(tmp_path / "a", "2024-03-25")  # one business day carried forward
    assert warn["status"] == "WARN" and "1 business day" in warn["detail"]
    ok3 = _rate_check(tmp_path / "b", "2024-03-21")  # 03-22, 03-25, 03-26 = 3 business days
    assert ok3["status"] == "WARN" and "3 business day" in ok3["detail"]
    bad = _rate_check(tmp_path / "c", "2024-03-20")  # 4 business days
    assert bad["status"] == "FAIL" and "more than 3" in bad["detail"]


def test_rate_data_fails_when_the_file_is_missing_or_starts_too_late(tmp_path):
    run = wf.build_run(tmp_path / "run")
    (run / "research" / "fred_DFF.csv").unlink()
    assert validate._check_rate_data(run)["status"] == "FAIL"
    wf.write_fred(run / "research" / "fred_DFF.csv", wf.LAST, first="2024-03-27")
    res = validate._check_rate_data(run)
    assert res["status"] == "FAIL" and "no FRED observation on or before" in res["detail"]


# ------------------------------------------------------------------ gate: upstream vendor


def _git(cwd: Path, *args: str) -> str:
    return subprocess.run(
        ["git", "-C", str(cwd), "-c", "user.email=t@t", "-c", "user.name=t", *args],
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()


def _vendor_and_upstream(tmp_path: Path):
    """A vendor tree whose files were committed to a throwaway 'upstream' repository."""
    up, vend = tmp_path / "upstream", tmp_path / "vend"
    (up / "skills" / "a").mkdir(parents=True)
    (vend / "vendor").mkdir(parents=True)
    (up / "skills" / "a" / "mod.py").write_bytes(b"X = 1\r\nY = 2\n")
    _git(up, "init", "-q")
    _git(up, "add", ".")
    _git(up, "commit", "-q", "-m", "c")
    ref = _git(up, "rev-parse", "HEAD")
    (vend / "vendor" / "mod.py").write_bytes(b"X = 1\r\nY = 2\n")
    lock = {
        "ref": ref,
        "files": [{"dest": "vendor/mod.py", "source": "skills/a/mod.py", "sha256": "unused"}],
    }
    (vend / "vendor" / "VENDOR.lock").write_text(json.dumps(lock))
    return vend, up, ref


def test_vendor_files_must_equal_git_show_of_the_upstream_ref(tmp_path):
    vend, up, ref = _vendor_and_upstream(tmp_path)
    res = validate._check_vendor_upstream(vend, up)
    assert res["status"] == "PASS" and "byte for byte" in res["detail"]
    (vend / "vendor" / "mod.py").write_bytes(b"X = 1\nY = 2\n")  # only the line ending differs
    res = validate._check_vendor_upstream(vend, up)
    assert res["status"] == "FAIL" and "differs from upstream" in res["detail"]


def test_vendor_check_is_a_skip_with_the_reason_when_the_upstream_is_missing(tmp_path):
    vend, up, ref = _vendor_and_upstream(tmp_path)
    res = validate._check_vendor_upstream(vend, tmp_path / "nowhere")
    assert res["status"] == "SKIP" and "not found" in res["detail"]
    assert validate._check_vendor_upstream(vend, None)["status"] == "SKIP"
    lock = json.loads((vend / "vendor" / "VENDOR.lock").read_text())
    lock["ref"] = "0" * 40
    (vend / "vendor" / "VENDOR.lock").write_text(json.dumps(lock))
    res = validate._check_vendor_upstream(vend, up)
    assert res["status"] == "SKIP" and "is not in" in res["detail"]


def test_real_vendor_files_equal_the_upstream_when_the_checkout_exists():
    if not (validate.DEFAULT_UPSTREAM / ".git").exists():
        pytest.skip("claude-trading-skills checkout not available")
    res = validate._check_vendor_upstream(ROOT, validate.DEFAULT_UPSTREAM)
    assert res["status"] in ("PASS", "SKIP"), res


# ------------------------------------------------------------------ wrapper + launchd


def test_run_daily_script_and_launchd_template_exist_and_are_not_installed():
    sh = (ROOT / "scripts" / "run_daily.sh").read_text()
    assert os.access(ROOT / "scripts" / "run_daily.sh", os.X_OK)
    assert "FTD_DD_SCHEDULED=1" in sh and "scripts/publish.py" in sh
    code = "\n".join(ln for ln in sh.splitlines() if not ln.lstrip().startswith("#"))
    for flag in ("--no-site-check", "--allow-no-browser", "--use-cache"):
        assert flag not in code
        assert flag in sh or flag == "--use-cache"  # the comments warn about them
    assert "logs/" in sh and "PLAYWRIGHT_PYTHON" in sh
    import plistlib

    with (ROOT / "launchd" / "com.ftd-dd-dashboard.daily.plist").open("rb") as fh:
        plist = plistlib.load(fh)
    assert plist["Label"] == "com.ftd-dd-dashboard.daily"
    cal = plist["StartCalendarInterval"]
    assert sorted(c["Weekday"] for c in cal) == [1, 2, 3, 4, 5]
    assert all(c["Hour"] == 15 and c["Minute"] == 0 for c in cal)  # 15:00 Pacific = 18:00 ET
    assert "__PROJECT_DIR__" in " ".join(plist["ProgramArguments"])
    assert "logs" in plist["StandardOutPath"]
    installed = Path.home() / "Library" / "LaunchAgents" / "com.ftd-dd-dashboard.daily.plist"
    assert not installed.exists()  # the owner decides whether to install it
