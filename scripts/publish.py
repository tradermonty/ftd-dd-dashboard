#!/usr/bin/env python3
"""Build everything into a NEW run directory, validate it, and only on PASS repoint the
`current` symlink (docs/plan.md section 5, local-only: no GitHub Pages / Actions).

    build/runs/<UTC timestamp>/{data,research,site,validation.json}
    build/current -> runs/<UTC timestamp>      (replaced atomically, only after PASS)

Steps: fetch (FMP, or data/raw/ with --use-cache) -> replay / events / outcomes -> research
-> data dictionary -> site -> validation -> retention. Exit code 0 = PASS, 1 = FAIL (current
untouched), 2 = the run directory already exists or the options are not allowed.

The scheduled job (scripts/run_daily.sh) must never use --no-site-check or --allow-no-browser:
with FTD_DD_SCHEDULED=1 in the environment both are refused.

Usage:
    python scripts/publish.py [--use-cache] [--refresh-fred] [--build-dir build] [--end YYYY-MM-DD]
"""

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
from collections.abc import Mapping, Sequence
from datetime import UTC, date, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

from engine import fetch, retention  # noqa: E402
from research import freeze as freeze_mod  # noqa: E402
from research import recheck  # noqa: E402
from webgen import dictionary, validate  # noqa: E402

PLAYWRIGHT_ENV = "PLAYWRIGHT_PYTHON"
PLAYWRIGHT_CANDIDATES = (
    "/usr/bin/python3",
    "/opt/homebrew/bin/python3",
    "/usr/local/bin/python3",
)
NEEDS_REVIEW = "NEEDS_REVIEW"
SCHEDULED_ENV = "FTD_DD_SCHEDULED"


def default_run_name(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")


def _no_network(url: str, params: dict):
    raise fetch.FetchError("network access is disabled (--use-cache with a cache miss)")


def generate(
    run_dir: Path,
    *,
    use_cache: bool = False,
    now: datetime | None = None,
    end=None,
    refresh_fred: bool = False,
    log=print,
) -> None:
    """Fetch, compute and write data/, research/ and site/ into `run_dir`.

    `use_cache`: no FMP call is made. The FRED rate file follows the same switch: it is
    downloaded again when stale unless `use_cache` is set, in which case a stale file stops the
    run, unless `refresh_fred` allows the FRED download on its own (FMP stays cached)."""
    import build_events
    import run_research

    from webgen import site

    t0 = time.time()
    histories, issues = {}, []
    kw = {"http_get": _no_network, "api_key": "unused"} if use_cache else {}
    for sym in fetch.DEFAULT_SYMBOLS:
        res = fetch.fetch_index(sym, end=end, use_cache=use_cache, now=now, **kw)
        histories[sym] = res.rows
        issues.extend(res.issues)
        log(f"[{time.time() - t0:6.1f}s] fetched {sym}: {len(res.rows)} bars")
    build_events.build_all(histories, run_dir / "data", issues)
    log(f"[{time.time() - t0:6.1f}s] replay / events / outcomes done")
    (run_dir / "data" / "data_dictionary.md").write_text(
        dictionary.render_markdown(), encoding="utf-8"
    )
    run_research.run(
        histories, run_dir / "research", log=log, dff_network=(not use_cache) or refresh_fred
    )
    log(f"[{time.time() - t0:6.1f}s] research done")
    site.build_site(run_dir)
    log(f"[{time.time() - t0:6.1f}s] site done")


def _repoint(build_root: Path, run_dir: Path) -> None:
    """Atomically make build/current a relative symlink to the run directory."""
    current = build_root / "current"
    if current.exists() and not current.is_symlink():
        raise RuntimeError(f"{current} exists and is not a symlink; refusing to replace it")
    tmp = build_root / f".current.{os.getpid()}"
    if tmp.is_symlink() or tmp.exists():
        tmp.unlink()
    os.symlink(os.path.relpath(run_dir, build_root), tmp)
    os.replace(tmp, current)


def _can_import_playwright(py: str) -> bool:
    try:
        probe = subprocess.run(
            [py, "-c", "import playwright"], capture_output=True, text=True, timeout=60
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return probe.returncode == 0


def resolve_playwright_python(
    environ: Mapping[str, str] | None = None, candidates: Sequence[str] | None = None
) -> tuple[str | None, str]:
    """The python interpreter that has Playwright, and how it was found.

    1. $PLAYWRIGHT_PYTHON when set (it must import playwright; otherwise there is no fallback,
       so a wrong setting is reported and not hidden),
    2. otherwise the first of `python3` on PATH and the usual system locations that can
       `import playwright`.
    Returns (python or None, explanation)."""
    env = os.environ if environ is None else environ
    explicit = env.get(PLAYWRIGHT_ENV)
    if explicit:
        if _can_import_playwright(explicit):
            return explicit, f"{PLAYWRIGHT_ENV}"
        return None, f"{PLAYWRIGHT_ENV}={explicit} cannot import playwright"
    seen: list[str] = []
    for c in (shutil.which("python3"), *(candidates or PLAYWRIGHT_CANDIDATES)):
        if c and c not in seen:
            seen.append(c)
            if _can_import_playwright(c):
                return c, "auto-detected"
    return None, f"playwright is not importable by any of {seen}; set {PLAYWRIGHT_ENV}"


def run_browser_check(site_dir: Path, out_dir: Path, python: str | None = None) -> dict:
    """Run scripts/check_site.py (headless Chromium via Playwright) on the built site.

    Playwright lives in a system python3, not in the project venv, so the check runs in a
    subprocess. The interpreter is `python` when given, else resolved by
    resolve_playwright_python (PLAYWRIGHT_PYTHON, then auto-detection). Returns
    {"status": PASS|FAIL|SKIP, "detail": ...}; SKIP when no interpreter can import Playwright
    (the reason is recorded; publish() turns a SKIP into a FAIL unless --allow-no-browser)."""
    if python is not None:
        py, how = (python, "given") if _can_import_playwright(python) else (None, "")
        if py is None:
            return {
                "status": "SKIP",
                "detail": f"playwright is not importable by {python}; browser check not run",
            }
    else:
        py, how = resolve_playwright_python()
        if py is None:
            return {"status": "SKIP", "detail": f"{how}; browser check not run"}
    try:
        proc = subprocess.run(
            [py, str(ROOT / "scripts" / "check_site.py"), str(site_dir), "--out", str(out_dir)],
            capture_output=True,
            text=True,
            timeout=900,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return {"status": "FAIL", "detail": f"check_site.py did not finish: {exc}"}
    last = (proc.stdout.strip().splitlines() or [""])[-1]
    if proc.returncode == 0:
        return {"status": "PASS", "detail": f"{last or 'check_site.py passed'} (python: {how})"}
    return {
        "status": "FAIL",
        "detail": (last or proc.stderr.strip()[-300:] or "check_site.py failed"),
    }


def run_recompute_check(run_dir: Path) -> dict:
    """Gate check: recompute EVERYTHING (replay, events, outcomes, research R and R2, the
    report) from the price history in the local fetch cache (no network) and compare with the
    run's files; only the generation timestamps may differ."""
    run = Path(run_dir)
    histories, issues = recheck.cached_histories(validate.last_session_date(run))
    return recheck.check_results(run, histories, issues)


def _history_review(result: dict) -> str | None:
    """A message when the history diff was compared and exceeded the limit (or could not be
    made), else None."""
    hd = next((c for c in result["checks"] if c["name"] == "history_diff"), None)
    if hd is None or hd["status"] != "FAIL":
        return None
    diff = result.get("diff", {})
    lines = [f"run {result.get('run_dir')}: {hd['detail']}"]
    lines += [f"  {s}" for s in diff.get("samples", [])[:10]]
    return "\n".join(lines)


def publish(
    build_root: Path,
    *,
    generate_fn=None,
    run_name: str | None = None,
    now: datetime | None = None,
    use_cache: bool = False,
    end=None,
    max_historical_changes: int = validate.MAX_HISTORICAL_CHANGES,
    check_site: bool = True,
    vendor_root: Path = ROOT,
    freeze_path: Path | None = None,
    allow_grid_change: bool = False,
    browser_check=None,
    recompute_check=None,
    key_facts_check: bool = True,
    refresh_fred: bool = False,
    allow_no_browser: bool = False,
    keep_screens: bool = False,
    prune_runs: bool = True,
    upstream_root: Path | None = validate.DEFAULT_UPSTREAM,
    logs_dir: Path | None = None,
    log=print,
) -> int:
    """`freeze_path`: the repository file holding the frozen variant-grid hashes (created on
    the first publish, enforced afterwards). `browser_check(site_dir, out_dir)`: returns
    {"status", "detail"}; it runs right after generation into a temporary directory. A SKIP
    result is turned into a FAIL unless `allow_no_browser`. The screenshots are copied into
    the run (screens/) only with `keep_screens` or when the check failed; this keeps a run
    near 6 MB. `recompute_check(run_dir)`: the {"name", "status", "detail"} check of the result
    files against a full recomputation (see run_recompute_check). `prune_runs`: apply the
    retention rules of engine.retention afterwards. `logs_dir`: where the NEEDS_REVIEW message
    is also written."""
    build_root = Path(build_root)
    run_dir = build_root / "runs" / (run_name or default_run_name())
    run_dir.parent.mkdir(parents=True, exist_ok=True)
    run_dir.mkdir()  # FileExistsError: a run directory is never reused

    current = build_root / "current"
    # A dangling `current` still means a previous run existed: the gate then FAILs on it
    # instead of silently treating this as a first run.
    previous = current.resolve() if current.is_symlink() else None

    freeze_created = False
    if freeze_path is not None and not Path(freeze_path).exists():
        freeze_mod.write(Path(freeze_path), runs_dir=run_dir.parent)
        freeze_created = True
        log(f"created {freeze_path} (variant grids frozen)")

    gen_error = None
    try:
        (generate_fn or generate)(
            run_dir, use_cache=use_cache, now=now, end=end, refresh_fred=refresh_fred, log=log
        )
    except Exception as exc:  # any failure to build is a gate failure, never a half-publish
        gen_error = f"{type(exc).__name__}: {exc}"
        log(traceback.format_exc())

    extra: list[dict] = []
    if check_site and gen_error is None:
        if browser_check is None:
            res = {"status": "SKIP", "detail": "no browser check configured"}
            shots = None
        else:
            shots = Path(tempfile.mkdtemp(prefix="ftd-dd-screens-"))
            res = browser_check(run_dir / "site", shots)
        status, detail = res["status"], res["detail"]
        kept = keep_screens or status == "FAIL"
        if not kept:  # the temporary screenshot folder is deleted below: do not point at it
            detail = re.sub(r"; screenshots in \S+", "; screenshots not kept", detail)
        if status == "SKIP" and not allow_no_browser:
            status = "FAIL"
            detail = f"the browser check did not run ({detail}); --allow-no-browser accepts this"
        extra.append({"name": "browser_check", "status": status, "detail": detail[:500]})
        if shots is not None:
            if kept and any(shots.iterdir()):
                shutil.copytree(shots, run_dir / "screens")
            shutil.rmtree(shots, ignore_errors=True)
    elif check_site:
        extra.append(
            {
                "name": "browser_check",
                "status": "SKIP",
                "detail": "generation failed; site not built",
            }
        )
    if gen_error is None:
        if recompute_check is None:
            extra.append(
                {
                    "name": "results_recomputed",
                    "status": "SKIP",
                    "detail": "no recomputation configured",
                }
            )
        else:
            try:
                extra.append(recompute_check(run_dir))
            except Exception as exc:  # a check that cannot run is a failure, never a skip
                extra.append(
                    {
                        "name": "results_recomputed",
                        "status": "FAIL",
                        "detail": f"{type(exc).__name__}: {exc}"[:500],
                    }
                )

    result = validate.validate_run(
        run_dir,
        now=now,
        previous_dir=previous,
        vendor_root=vendor_root,
        max_historical_changes=max_historical_changes,
        check_site=check_site,
        freeze_path=freeze_path,
        allow_grid_change=allow_grid_change,
        extra_checks=extra,
        key_facts=key_facts_check,
        upstream_root=upstream_root,
    )
    if freeze_created and "grid_freeze" in result:
        result["grid_freeze"]["created_on_this_run"] = True
    if gen_error:
        result["checks"].insert(
            0, {"name": "generation", "status": "FAIL", "detail": gen_error[:500]}
        )
        result["status"] = "FAIL"
    if result["status"] == "PASS":
        try:
            _repoint_check(build_root)
        except RuntimeError as exc:
            result["checks"].append({"name": "repoint", "status": "FAIL", "detail": str(exc)})
            result["status"] = "FAIL"
    if (
        result["status"] == "PASS"
        and allow_grid_change
        and freeze_path is not None
        and any(c["name"] == "grid_freeze" and c["status"] == "WARN" for c in result["checks"])
    ):
        # the change was explicitly allowed: freeze the new grids, keep the old entry as history
        freeze_mod.write(
            Path(freeze_path),
            previous=freeze_mod.load(Path(freeze_path)),
            note=f"re-frozen with --allow-grid-change by run {run_dir.name}",
            runs_dir=run_dir.parent,
        )
        result["grid_freeze"]["refrozen"] = True
    (run_dir / "validation.json").write_text(
        json.dumps(result, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    log(f"validation: {result['status']} ({run_dir})")
    for c in result["checks"]:
        log(f"  {c['status']:4} {c['name']}: {c['detail']}")

    review = _history_review(result)
    marker = build_root / NEEDS_REVIEW
    if review:
        text = (
            f"NEEDS REVIEW {datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ')}\n"
            "The history of already-detected events changed more than the limit since the last "
            "published run, so build/current was NOT replaced. Look at the samples, decide "
            "whether the change is real (a data fix at the provider), and run publish again "
            "with a higher --max-changes if it is. This file is removed by the next run that "
            "passes.\n" + review + "\n"
        )
        marker.write_text(text, encoding="utf-8")
        log("NEEDS REVIEW: " + review)
        if logs_dir is not None:
            Path(logs_dir).mkdir(parents=True, exist_ok=True)
            with (Path(logs_dir) / "needs_review.log").open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")
    elif result["status"] == "PASS" and marker.exists():
        marker.unlink()
        log(f"removed {marker.name} (this run passed with the history compared)")

    if result["status"] == "PASS":
        _repoint(build_root, run_dir)
        log(f"build/current -> {os.readlink(current)}")
        if keep_screens:
            dropped = retention.drop_other_screens(build_root, run_dir)
            if dropped:
                log(f"screens removed from {len(dropped)} older run(s)")
    if prune_runs:
        deleted = retention.prune(build_root, now=now)
        if deleted:
            log(f"retention: deleted {len(deleted)} run(s): {', '.join(deleted)}")
    return 0 if result["status"] == "PASS" else 1


def _repoint_check(build_root: Path) -> None:
    current = build_root / "current"
    if current.exists() and not current.is_symlink():
        raise RuntimeError(f"{current} exists and is not a symlink; refusing to replace it")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--build-dir", type=Path, default=ROOT / "build")
    ap.add_argument("--use-cache", action="store_true", help="reuse data/raw/ chunks, no FMP call")
    ap.add_argument(
        "--refresh-fred",
        action="store_true",
        help="with --use-cache: still allow downloading a stale FRED rate file",
    )
    ap.add_argument("--end", default=None, help="last date (default: latest final XNYS session)")
    ap.add_argument("--max-changes", type=int, default=validate.MAX_HISTORICAL_CHANGES)
    ap.add_argument(
        "--no-site-check",
        action="store_true",
        help="skip the site file, key-fact and browser checks (recorded as WARN)",
    )
    ap.add_argument(
        "--allow-no-browser",
        action="store_true",
        help="accept a browser check that cannot run (otherwise a SKIP fails the run)",
    )
    ap.add_argument(
        "--keep-screens",
        action="store_true",
        help="keep the screenshots (~50 MB) in this run; older runs lose theirs",
    )
    ap.add_argument("--no-prune", action="store_true", help="do not apply the retention rules")
    ap.add_argument(
        "--allow-grid-change",
        action="store_true",
        help="accept a changed variant grid (recorded as WARN) and re-freeze it",
    )
    ap.add_argument(
        "--upstream",
        type=Path,
        default=validate.DEFAULT_UPSTREAM,
        help="claude-trading-skills checkout the vendored files are compared with "
        "(default: $CTS_CHECKOUT or ~/PycharmProjects/claude-trading-skills)",
    )
    args = ap.parse_args(argv)

    if os.environ.get(SCHEDULED_ENV) and (args.no_site_check or args.allow_no_browser):
        print(
            f"refusing --no-site-check / --allow-no-browser: {SCHEDULED_ENV} is set",
            file=sys.stderr,
        )
        return 2

    try:
        return publish(
            args.build_dir,
            use_cache=args.use_cache,
            refresh_fred=args.refresh_fred,
            end=date.fromisoformat(args.end) if args.end else None,
            max_historical_changes=args.max_changes,
            check_site=not args.no_site_check,
            freeze_path=freeze_mod.FREEZE_PATH,
            allow_grid_change=args.allow_grid_change,
            browser_check=run_browser_check,
            recompute_check=run_recompute_check,
            allow_no_browser=args.allow_no_browser,
            keep_screens=args.keep_screens,
            prune_runs=not args.no_prune,
            upstream_root=args.upstream,
            logs_dir=ROOT / "logs",
        )
    except FileExistsError as exc:
        print(f"refusing to reuse an existing run directory: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
