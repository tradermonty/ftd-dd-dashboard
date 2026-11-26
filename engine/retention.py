"""Retention of build/runs/ for the daily job.

Keep the newest `keep_pass` runs whose validation PASSED, plus the run build/current points to
(whatever its age). Delete older PASS runs, and FAILED runs (a validation.json with status FAIL,
or a run directory that never got one) older than `failed_days`. A run that is still being
written (no validation.json yet and younger than an hour) is never touched. The target of
build/current is never deleted.

Run names are UTC start times, YYYYmmddTHHMMSSZ; the age of a run is taken from its name.
"""

from __future__ import annotations

import json
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path

KEEP_PASS = 10
FAILED_DAYS = 7
IN_PROGRESS = timedelta(hours=1)


def run_time(name: str) -> datetime | None:
    try:
        return datetime.strptime(name, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def run_status(run: Path) -> str:
    """PASS, FAIL, or NONE when there is no readable validation.json."""
    try:
        return json.loads((Path(run) / "validation.json").read_text(encoding="utf-8"))["status"]
    except (OSError, ValueError, KeyError):
        return "NONE"


def plan(
    build_root: Path,
    now: datetime | None = None,
    keep_pass: int = KEEP_PASS,
    failed_days: int = FAILED_DAYS,
) -> list[Path]:
    """Run directories that retention would delete, oldest first."""
    now = now or datetime.now(UTC)
    runs_dir = Path(build_root) / "runs"
    if not runs_dir.is_dir():
        return []
    current = Path(build_root) / "current"
    target = current.resolve() if current.is_symlink() else None
    runs = sorted(d for d in runs_dir.iterdir() if d.is_dir() and run_time(d.name))
    passed = [d for d in runs if run_status(d) == "PASS"]
    keep = set(passed[-keep_pass:]) if keep_pass > 0 else set()
    doomed = []
    for d in runs:
        if target is not None and d.resolve() == target:
            continue
        status = run_status(d)
        age = now - run_time(d.name)
        if status == "PASS":
            if d not in keep:
                doomed.append(d)
        elif status == "NONE" and age < IN_PROGRESS:
            continue
        elif age > timedelta(days=failed_days):
            doomed.append(d)
    return doomed


def prune(
    build_root: Path,
    now: datetime | None = None,
    keep_pass: int = KEEP_PASS,
    failed_days: int = FAILED_DAYS,
) -> list[str]:
    """Delete what `plan` lists and return the names of the deleted runs."""
    doomed = plan(build_root, now, keep_pass, failed_days)
    for d in doomed:
        shutil.rmtree(d)
    return [d.name for d in doomed]


def drop_other_screens(build_root: Path, keep: Path) -> list[str]:
    """Screenshots are kept for the current run only: remove screens/ from every other run."""
    removed = []
    runs_dir = Path(build_root) / "runs"
    if not runs_dir.is_dir():
        return removed
    for d in runs_dir.iterdir():
        s = d / "screens"
        if d.is_dir() and d.resolve() != Path(keep).resolve() and s.is_dir():
            shutil.rmtree(s)
            removed.append(d.name)
    return removed
