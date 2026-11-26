"""Validation gate for a generated run directory (docs/plan.md section 5).

`validate_run` only reads files: it never fetches anything. It returns a JSON-serialisable
dict (the content of validation.json). Checks:

  latest_date          newest replay date == latest FINAL XNYS session at `now`
  indices_sessions     both indices present, same sessions, equal to the XNYS calendar
  dictionary_headers   every CSV header == data_dictionary.md == code constants
  counts_consistent    CSV row counts == summary.json counts (per file and per index)
  vendor_lock          vendor files hash to VENDOR.lock; summary vendor_ref == lock ref
  vendor_upstream      each vendored file equals `git show <ref>:<source>` of the upstream
                       checkout byte for byte (SKIP with the reason when it is not available)
  research_files       research files present; row counts and ids consistent with the variants;
                       the test period ends on the frozen end (research.split.TEST_END)
  rate_data            the FRED rate needed for the last return day is in the run's copy of the
                       file (WARN up to 3 business days carried forward, FAIL beyond)
  grid_freeze         the variant grids of the code and of the run equal the frozen hashes
                       (WARN, recorded, with --allow-grid-change)
  history_diff         added/removed/changed events older than min(previous last date, new
                       last date) vs the previous build; fail above `max_historical_changes`
                       and fail (never skip) when a previous build exists but is unreadable
  site_files           every page, asset and llms.txt exists (WARN when --no-site-check)
  key_facts            the facts behind the key points equal a recomputation and the pages
                       show exactly the sentences rendered from them
  site_render          every generated page, the data files of the site and the assets equal a
                       re-render from the run's own files (only the timestamp is ignored)
  picks_recomputed     the selectors re-run on the run's design rows give the recorded picks,
                       the tie-break amendment list and the R2 tie effect
  results_recomputed   (added by scripts/publish.py) the replay, event, outcome and research
                       files and report.html equal a FULL recomputation from the cached price
                       history (only generation timestamps are ignored)
  browser_check        (added by scripts/publish.py) headless-browser check of the site
"""

from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from engine import fetch
from research import freeze as freeze_mod
from research import overlay, recheck
from research.split import TEST_END
from webgen import dictionary, facts, layout, pages

ROOT = Path(__file__).resolve().parents[1]
MAX_HISTORICAL_CHANGES = 5  # plan Q3: start with 5 and revisit while operating
# The claude-trading-skills checkout that the vendored files come from (CTS_CHECKOUT overrides).
DEFAULT_UPSTREAM = Path(
    os.environ.get("CTS_CHECKOUT", Path.home() / "PycharmProjects" / "claude-trading-skills")
)
# more business days of carried-forward FRED rate is a FAIL; the same limit the loader uses
RATE_FILL_FAIL_BUSINESS_DAYS = overlay.RATE_FILL_MAX_BUSINESS_DAYS

# Event identity and the columns that are fixed once an event exists. Columns that evolve
# with time (invalidation dates / flags, removal reasons, a DD's expires_on, which stays blank
# until its 26th session arrives) are deliberately not compared: they change legitimately
# every day and would trip the gate for no reason.
FTD_KEY, FTD_STABLE = (
    ("index", "ftd_date"),
    (
        "rally_day1_date",
        "swing_low_date",
        "detected_on",
        "lag_sessions",
        "ftd_day_number",
        "gain_pct",
        "volume_vs_prev_pct",
        "volume_above_avg",
        "invalidated_at_detection",
    ),
)
DD_KEY, DD_STABLE = (("index", "date"), ("pct_change", "volume_change_pct"))
# Dates that must all lie before the cutoff for an event to be compared. An FTD that was
# designated on an old date but first detected after the cutoff did not exist in the previous
# run, so it is new information, not a change.
FTD_DATES, DD_DATES = ("ftd_date", "detected_on"), ("date",)


def _read_csv(path: Path) -> tuple[list[str], list[dict]]:
    with path.open(newline="", encoding="utf-8") as f:
        r = csv.DictReader(f)
        rows = list(r)
        return list(r.fieldnames or []), rows


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def hash_files(run_dir: Path) -> dict[str, str]:
    return {
        p.relative_to(run_dir).as_posix(): sha256_file(p)
        for p in sorted(run_dir.rglob("*"))
        if p.is_file() and p.relative_to(run_dir).as_posix() != "validation.json"
    }


def _check(name: str, ok: bool, detail: str, status: str | None = None) -> dict:
    return {"name": name, "status": status or ("PASS" if ok else "FAIL"), "detail": detail}


# ------------------------------------------------------------------------------ checks


def _check_latest(run: Path, now: datetime, market: dict) -> dict:
    expected = fetch.latest_final_session(now).isoformat()
    market["expected_latest_final_session"] = expected
    got = market.get("last_date")
    return _check(
        "latest_date",
        got == expected,
        f"newest replay date {got}; latest final XNYS session {expected}",
    )


def _check_sessions(status_rows: list[dict], market: dict) -> dict:
    by_idx: dict[str, list[str]] = {}
    for r in status_rows:
        by_idx.setdefault(r["index"], []).append(r["date"])
    problems = []
    for sym in fetch.DEFAULT_SYMBOLS:
        if sym not in by_idx:
            problems.append(f"{sym} missing")
    extra = set(by_idx) - set(fetch.DEFAULT_SYMBOLS)
    if extra:
        problems.append(f"unexpected index {sorted(extra)}")
    if not problems:
        first = min(min(v) for v in by_idx.values())
        last = max(max(v) for v in by_idx.values())
        expected = [d.strftime("%Y-%m-%d") for d in fetch.get_xnys().sessions_in_range(first, last)]
        for sym, dates in by_idx.items():
            if len(dates) != len(set(dates)):
                problems.append(f"{sym} has duplicate dates")
            elif sorted(dates) != expected:
                miss = sorted(set(expected) - set(dates))
                oth = sorted(set(dates) - set(expected))
                problems.append(
                    f"{sym} differs from XNYS sessions (missing {miss[:3]}, extra {oth[:3]})"
                )
        market["sessions"] = len(expected)
    return _check(
        "indices_sessions",
        not problems,
        "; ".join(problems) or f"both indices match {market['sessions']} XNYS sessions",
    )


def _check_headers(run: Path) -> dict:
    md = run / "data" / "data_dictionary.md"
    if not md.exists():
        return _check("dictionary_headers", False, "data/data_dictionary.md missing")
    documented = dictionary.parse_markdown(md.read_text(encoding="utf-8"))
    problems = []
    for path, cols in dictionary.files().items():
        want = [c for c, _ in cols]
        if documented.get(path) != want:
            problems.append(f"{path}: data_dictionary.md differs from the code definition")
        f = run / path
        if not f.exists():
            problems.append(f"{path}: file missing")
            continue
        with f.open(newline="", encoding="utf-8") as fh:
            header = next(csv.reader(fh), [])
        if header != want:
            problems.append(f"{path}: header {header[:4]}... != dictionary")
    return _check(
        "dictionary_headers",
        not problems,
        "; ".join(problems) or f"{len(dictionary.files())} CSV headers match the dictionary",
    )


def _check_counts(run: Path, summary: dict) -> dict:
    problems = []
    try:
        counts = summary["counts"]
        per = summary["per_index"]
    except KeyError as exc:
        return _check("counts_consistent", False, f"summary.json lacks {exc}")
    spec = {
        "ftd_events": "data/ftd_events.csv",
        "distribution_days": "data/distribution_days.csv",
        "rally_attempts": "data/rally_attempts.csv",
        "replay_rows": "data/daily_status.csv",
    }
    rows_by_file = {}
    for key, path in spec.items():
        _, rows = _read_csv(run / path)
        rows_by_file[key] = rows
        if counts.get(key) != len(rows):
            problems.append(f"{key}: summary {counts.get(key)} != csv {len(rows)}")
    for sym, info in per.items():
        for key in ("ftd_events", "distribution_days", "rally_attempts"):
            n = sum(1 for r in rows_by_file[key] if r["index"] == sym)
            if info.get(key) != n:
                problems.append(f"{sym} {key}: summary {info.get(key)} != csv {n}")
    return _check(
        "counts_consistent",
        not problems,
        "; ".join(problems) or "CSV row counts equal summary.json counts (total and per index)",
    )


def _check_vendor(summary: dict, vendor_root: Path, market: dict) -> dict:
    lock_path = vendor_root / "vendor" / "VENDOR.lock"
    try:
        lock = json.loads(lock_path.read_text())
    except (OSError, ValueError) as exc:
        return _check("vendor_lock", False, f"cannot read VENDOR.lock: {exc}")
    market["vendor_ref"] = lock.get("ref")
    problems = []
    for f in lock["files"]:
        p = vendor_root / f["dest"]
        if not p.exists():
            problems.append(f"{f['dest']} missing")
        elif sha256_file(p) != f["sha256"]:
            problems.append(f"{f['dest']} hash differs from VENDOR.lock")
    if summary.get("vendor_ref") != lock.get("ref"):
        problems.append(f"summary vendor_ref {summary.get('vendor_ref')} != lock {lock.get('ref')}")
    return _check(
        "vendor_lock",
        not problems,
        "; ".join(problems) or f"{len(lock['files'])} vendor files match VENDOR.lock",
    )


def _same(a: str, b: str) -> bool:
    if a == b:
        return True
    try:
        return abs(float(a) - float(b)) <= 1e-9 * max(1.0, abs(float(a)))
    except ValueError:
        return False


def _events(
    path: Path, key: tuple[str, ...], stable: tuple[str, ...], dates: tuple[str, ...], cutoff: str
) -> dict:
    _, rows = _read_csv(path)
    out = {}
    for r in rows:
        k = tuple(r[c] for c in key)
        if all(r[c] < cutoff for c in dates):
            out[k] = {c: r[c] for c in stable}
    return out


def last_session_date(run: Path) -> str:
    """Newest date of a run's daily_status.csv (raises when unreadable or empty)."""
    _, rows = _read_csv(Path(run) / "data" / "daily_status.csv")
    dates = [r["date"] for r in rows]
    if not dates:
        raise ValueError(f"{run}: daily_status.csv has no rows")
    return max(dates)


def diff_history(prev: Path, new: Path, cutoff: str) -> dict:
    """Events dated before `cutoff` that were added, removed or changed since `prev`.

    The caller passes cutoff = min(previous run's last date, new run's last date): events that
    only exist because more sessions arrived since the previous run are not changes."""
    out = {"cutoff": cutoff, "files": {}}
    added = removed = changed = 0
    samples: list[str] = []
    for name, key, stable, dates in (
        ("ftd_events.csv", FTD_KEY, FTD_STABLE, FTD_DATES),
        ("distribution_days.csv", DD_KEY, DD_STABLE, DD_DATES),
    ):
        a = _events(prev / "data" / name, key, stable, dates, cutoff)
        b = _events(new / "data" / name, key, stable, dates, cutoff)
        add = sorted(set(b) - set(a))
        rem = sorted(set(a) - set(b))
        chg = sorted(k for k in set(a) & set(b) if not all(_same(a[k][c], b[k][c]) for c in stable))
        out["files"][name] = {"added": len(add), "removed": len(rem), "changed": len(chg)}
        added, removed, changed = added + len(add), removed + len(rem), changed + len(chg)
        samples += [f"{name} added {k}" for k in add[:5]]
        samples += [f"{name} removed {k}" for k in rem[:5]]
        samples += [f"{name} changed {k}" for k in chg[:5]]
    out |= {
        "status": "compared",
        "added": added,
        "removed": removed,
        "changed": changed,
        "total_changes": added + removed + changed,
        "samples": samples[:20],
    }
    return out


def _check_site(run: Path) -> dict:
    missing = [f for f in layout.required_site_files() if not (run / f).is_file()]
    return _check(
        "site_files",
        not missing,
        f"missing: {missing[:5]}" if missing else f"{len(layout.required_site_files())} site files",
    )


def _first_diff(a, b, path: str = "") -> str:
    """Path of the first difference between two JSON-like values."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}/{k} missing on one side"
            d = _first_diff(a[k], b[k], f"{path}/{k}")
            if d:
                return d
        return ""
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            d = _first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return ""
    return "" if a == b else f"{path}: {a!r} != {b!r}"


def _check_key_facts(run: Path) -> dict:
    """The facts behind the key-point sentences must equal a recomputation from the run's own
    data files, and the sentences in the built pages must be exactly what those facts render.
    A number typed into a sentence, a stale page or an edited facts file all fail here."""
    name = "key_facts"
    try:
        saved = json.loads((run / "site" / "key_facts.json").read_text(encoding="utf-8"))
        f = facts.load_facts(run)
        kf = pages.key_facts(f)
    except (OSError, ValueError, KeyError) as exc:
        return _check(name, False, f"{type(exc).__name__}: {exc}")
    problems = []
    diff = _first_diff(saved, kf)
    if diff:
        problems.append(f"site/key_facts.json differs from recomputation at {diff}")
    n_sent = 0
    for lang in layout.LANGS:
        sentences = [pages.esc(s) for s in pages.render_key_points(pages.Ctx(lang, f), kf)]
        for page in ("research", "index"):
            p = run / "site" / lang / f"{page}.html"
            try:
                html = p.read_text(encoding="utf-8")
            except OSError as exc:
                problems.append(f"{lang}/{page}.html unreadable: {exc}")
                continue
            for s in sentences:
                n_sent += 1
                if f"<li>{s}</li>" not in html:
                    problems.append(f"{lang}/{page}.html: key point not produced from the data")
                    break
    return _check(
        name,
        not problems,
        "; ".join(problems[:4])
        or f"key_facts.json equals the recomputation; {n_sent} renderings match the pages",
    )


def _first_line_diff(a: str, b: str) -> str:
    """Short description of where two texts first differ (line number and the two lines)."""
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<end>"
        y = lb[i] if i < len(lb) else "<end>"
        if x != y:
            return f"line {i + 1}: disk {x[:90]!r} != render {y[:90]!r}"
    return "differs"


def _check_site_render(run: Path) -> dict:
    """Render every page again from the run's own files in memory and compare with the disk.

    The only thing ignored is the generation timestamp. A number changed in a sentence, a table
    cell, an extra list item, a deleted disclaimer or a stale page all differ from the render
    and fail here; so does an edited static asset or an unexpected extra page."""
    name = "site_render"
    from webgen import site as site_mod

    try:
        gen = facts.load_facts(run).summary.get("generated_at", "")
        rendered = site_mod.render_site(run)
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _check(name, False, f"{type(exc).__name__}: {exc}")

    def mask(text: str) -> str:
        """Hide the generation timestamp (the only thing allowed to differ): the footer words
        followed by a UTC timestamp, in either language, and the value from summary.json."""
        text = re.sub(r"(生成 )\d{4}-\d\d-\d\dT[\d:]+Z", r"\1<generated>", text)
        text = re.sub(r"(Generated )\d{4}-\d\d-\d\dT[\d:]+Z", r"\1<generated>", text)
        return text.replace(gen, "<generated>") if gen else text

    problems = []
    for rel, want in rendered.items():
        p = run / "site" / rel
        if not p.is_file():
            problems.append(f"site/{rel} is missing")
        elif mask(p.read_text(encoding="utf-8")) != mask(want):
            problems.append(
                f"site/{rel} differs from the render: "
                f"{_first_line_diff(mask(p.read_text(encoding='utf-8')), mask(want))}"
            )
    for asset in layout.ASSETS:
        p = run / "site" / "assets" / asset
        src = site_mod.ASSET_DIR / asset
        if not p.is_file() or p.read_bytes() != src.read_bytes():
            problems.append(f"site/assets/{asset} differs from the template asset")
    for lang in layout.LANGS:
        have = {q.name for q in (run / "site" / lang).glob("*.html")}
        extra = have - {f"{n}.html" for n in layout.PAGES}
        if extra:
            problems.append(f"unexpected pages in site/{lang}: {sorted(extra)}")
    return _check(
        name,
        not problems,
        "; ".join(problems[:4])
        or f"{len(rendered)} generated files and {len(layout.ASSETS)} assets equal a re-render "
        "from the run's own files",
    )


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


RESEARCH_FILES = (
    "variants.csv",
    "test_results.csv",
    "selected.json",
    "overlay_variants.csv",
    "overlay_test_results.csv",
    "overlay_selected.json",
)


def _check_research(run: Path, last_date: str | None) -> dict:
    """Research files exist and agree with each other and with the data: row counts follow
    the variant counts, every selected / baseline id exists in variants.csv, and the test
    period ends on the data's last session."""
    name = "research_files"
    r = run / "research"
    missing = [n for n in RESEARCH_FILES if not (r / n).is_file()]
    if missing:
        return _check(name, False, f"missing: {missing}")
    problems: list[str] = []
    try:
        sel, ov = _read_json(r / "selected.json"), _read_json(r / "overlay_selected.json")
        vrows = _read_csv(r / "variants.csv")[1]
        ovrows = _read_csv(r / "overlay_variants.csv")[1]
        trows = _read_csv(r / "test_results.csv")[1]
        otrows = _read_csv(r / "overlay_test_results.csv")[1]
        evals = sel["evaluations"]
        idxs = list(evals["A"])
        ids = {x["variant_id"] for x in vrows}
        sigs = {x["signal_id"] for x in vrows if x.get("signal_id")}
        want = sel["n_variants_tried"] * len(idxs)
        if len(vrows) != want:
            problems.append(f"variants.csv has {len(vrows)} rows, expected {want}")
        for ev, pool in (("A", ids), ("B", ids), ("C", sigs)):
            for idx, info in evals[ev].items():
                for role in ("baseline", "selected"):
                    if info[role] not in pool:
                        problems.append(f"{ev} {idx} {role} {info[role]!r} not in variants.csv")
        bad = [x["variant_id"] for x in trows if x["variant_id"] not in ids | sigs]
        if bad:
            problems.append(f"test_results.csv ids not in variants.csv: {bad[:3]}")
        want_ov = ov["n_variants"] * len(ov["cost_models"]) * len(ov["indices"])
        if len(ovrows) != want_ov:
            problems.append(f"overlay_variants.csv has {len(ovrows)} rows, expected {want_ov}")
        ov_ids = {x["variant_id"] for x in ovrows} | {"buy_hold"}
        for idx, info in ov["indices"].items():
            if info["selected"] not in ov_ids:
                problems.append(f"overlay {idx} selected {info['selected']!r} not in variants")
        bad = [x["variant_id"] for x in otrows if x["variant_id"] not in ov_ids]
        if bad:
            problems.append(f"overlay_test_results.csv ids not in variants: {bad[:3]}")
        # The test period ends on the FROZEN end (research.split.TEST_END), not on the newest
        # session; it only ends with the data when the data is shorter (synthetic histories).
        frozen_end = min(TEST_END, last_date) if last_date else TEST_END
        for label, got, want in (
            ("selected.json test end", sel["split"]["test"][1], frozen_end),
            ("selected.json data_range.last", sel.get("data_range", {}).get("last"), last_date),
            ("overlay_selected.json test end", ov["split"]["test"][1], frozen_end),
        ):
            if got != want:
                problems.append(f"{label} {got} != {want}")
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as exc:
        problems.append(f"{type(exc).__name__}: {exc}")
    return _check(
        name,
        not problems,
        "; ".join(problems[:5])
        or f"{len(RESEARCH_FILES)} files present; ids and row counts consistent; "
        f"the test period ends on the frozen end {min(TEST_END, last_date or TEST_END)} "
        f"(newest session {last_date})",
    )


def _check_grid_freeze(
    run: Path, freeze_path: Path | None, allow_change: bool
) -> tuple[dict, dict | None]:
    """The variant grids of the code and of the run must equal the frozen (pre-registered)
    hashes. `allow_change` turns a mismatch into a recorded WARN."""
    name = "grid_freeze"
    if freeze_path is None:
        return _check(name, True, "no freeze file configured", status="SKIP"), None
    try:
        frozen = freeze_mod.load(freeze_path)
        if frozen is None:
            return _check(name, False, f"freeze file {Path(freeze_path).name} is missing"), None
        recorded = {
            "selection": _read_json(run / "research" / "selected.json")["freeze"]["grid_sha256"],
            "overlay": _read_json(run / "research" / "overlay_selected.json")["freeze"][
                "grid_sha256"
            ],
        }
        want = frozen["grid_sha256"]
    except (OSError, ValueError, KeyError, TypeError) as exc:
        return _check(name, False, f"{type(exc).__name__}: {exc}"), None
    current = freeze_mod.current_hashes()
    problems = []
    for kind in freeze_mod.KINDS:
        if current[kind] != want.get(kind):
            problems.append(f"{kind}: grid in the code differs from the frozen grid")
        if recorded[kind] != want.get(kind):
            problems.append(f"{kind}: the run recorded a different grid hash")
    info = {
        "allow_grid_change": allow_change,
        "frozen": want,
        "current": current,
        "recorded": recorded,
        "pre_registered": frozen.get("pre_registered"),
    }
    if problems and allow_change:
        return (
            _check(
                name,
                True,
                "--allow-grid-change given; grid differs from the frozen one: "
                + "; ".join(problems),
                status="WARN",
            ),
            info,
        )
    return (
        _check(
            name,
            not problems,
            "; ".join(problems)
            or f"both variant grids equal the grids frozen on {frozen.get('frozen_on')}",
        ),
        info,
    )


# The loader (research.overlay.dff_staleness) counts the carried-forward days the same way.
_weekdays_between = overlay.weekdays_between


def _check_rate_data(run: Path) -> dict:
    """The FRED rate that the last return day of the price data needs must be in the run's
    copy of the file. The rate for the return of day k is the latest observation dated on or
    before the third-to-last session when k is the last session (research.overlay.rate_known).
    If the file ends before that date, the rate is carried forward: up to
    RATE_FILL_FAIL_BUSINESS_DAYS business days is a WARN (the publication lag of the series),
    more, or no observation at all, is a FAIL."""
    from research import overlay

    name = "rate_data"
    try:
        _, rows = _read_csv(run / "data" / "daily_status.csv")
        dates = sorted({r["date"] for r in rows})
        obs = overlay.load_dff(run / "research" / overlay.DFF_COPY_NAME)
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        return _check(name, False, f"cannot read the rate data: {type(exc).__name__}: {exc}")
    if len(dates) < 3 or not obs:
        return _check(name, False, "too few sessions or no FRED observations to check the rate")
    need = dates[-3]
    usable = [d for d, _ in obs if d <= need]
    if not usable:
        return _check(name, False, f"no FRED observation on or before {need}")
    gap = _weekdays_between(usable[-1], need)
    base = (
        f"the last return day needs the FRED rate dated {need}; the newest observation on or "
        f"before it is {usable[-1]}"
    )
    if gap == 0:
        return _check(name, True, base + "; no rate is carried forward")
    detail = f"{base}; the rate is carried forward over {gap} business day(s)"
    if gap > RATE_FILL_FAIL_BUSINESS_DAYS:
        return _check(name, False, detail + f" (more than {RATE_FILL_FAIL_BUSINESS_DAYS})")
    return _check(name, True, detail + " (within the publication lag)", status="WARN")


def _check_vendor_upstream(vendor_root: Path, upstream_root: Path | None) -> dict:
    """Each vendored file must equal `git show <ref>:<source>` of the upstream checkout byte
    for byte. SKIP (with the reason) when the checkout or the ref is not available."""
    name = "vendor_upstream"
    if upstream_root is None:
        return _check(name, True, "no upstream checkout configured", status="SKIP")
    upstream = Path(upstream_root)
    try:
        lock = json.loads((vendor_root / "vendor" / "VENDOR.lock").read_text())
    except (OSError, ValueError) as exc:
        return _check(name, False, f"cannot read VENDOR.lock: {exc}")
    ref = lock.get("ref")
    if not (upstream / ".git").exists():
        return _check(name, True, f"upstream checkout not found at {upstream}", status="SKIP")
    try:
        have = subprocess.run(
            ["git", "-C", str(upstream), "cat-file", "-e", f"{ref}^{{commit}}"],
            capture_output=True,
            timeout=60,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return _check(name, True, f"git is not usable: {exc}", status="SKIP")
    if have.returncode != 0:
        return _check(name, True, f"ref {ref} is not in {upstream}", status="SKIP")
    problems = []
    for f in lock["files"]:
        proc = subprocess.run(
            ["git", "-C", str(upstream), "show", f"{ref}:{f['source']}"],
            capture_output=True,
            timeout=60,
        )
        mine = vendor_root / f["dest"]
        if proc.returncode != 0:
            problems.append(f"{f['source']} is not in upstream {ref[:10]}")
        elif not mine.is_file() or mine.read_bytes() != proc.stdout:
            problems.append(f"{f['dest']} differs from upstream {f['source']}")
    return _check(
        name,
        not problems,
        "; ".join(problems[:4])
        or f"{len(lock['files'])} vendored files equal git show {ref[:10]}:<source> byte for byte",
    )


# ----------------------------------------------------------------------------- entry point


def validate_run(
    run_dir: Path,
    *,
    now: datetime | None = None,
    previous_dir: Path | None = None,
    vendor_root: Path = ROOT,
    max_historical_changes: int = MAX_HISTORICAL_CHANGES,
    check_site: bool = True,
    freeze_path: Path | None = None,
    allow_grid_change: bool = False,
    extra_checks: list[dict] | None = None,
    key_facts: bool = True,
    upstream_root: Path | None = DEFAULT_UPSTREAM,
) -> dict:
    """`extra_checks`: checks computed before validation (the browser check of the built
    site) so that files they write, such as screens/, are hashed below. `upstream_root`: the
    claude-trading-skills checkout the vendored files are compared with (None = SKIP)."""
    run = Path(run_dir)
    checks: list[dict] = []
    market: dict = {}
    summary: dict = {}
    result: dict = {
        "generated_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "run_dir": run.name,
        "market": market,
        "max_historical_changes": max_historical_changes,
    }
    try:
        summary = json.loads((run / "data" / "summary.json").read_text())
        _, status_rows = _read_csv(run / "data" / "daily_status.csv")
        dates = [r["date"] for r in status_rows]
        market["first_date"] = min(dates) if dates else None
        market["last_date"] = max(dates) if dates else None
        market["summary_last"] = summary.get("data_range", {}).get("last")
        checks.append(_check_latest(run, now or datetime.now(UTC), market))
        if market["summary_last"] != market["last_date"]:
            checks[-1] = _check(
                "latest_date",
                False,
                f"summary.json last {market['summary_last']} != daily_status {market['last_date']}",
            )
        checks.append(_check_sessions(status_rows, market))
        checks.append(_check_headers(run))
        checks.append(_check_counts(run, summary))
    except (OSError, ValueError, KeyError, csv.Error) as exc:
        checks.append(_check("inputs_readable", False, f"{type(exc).__name__}: {exc}"))
    checks.append(_check_vendor(summary, vendor_root, market))
    checks.append(_check_vendor_upstream(vendor_root, upstream_root))
    result["vendor_ref"] = market.get("vendor_ref")
    checks.append(_check_research(run, market.get("last_date")))
    checks.append(_check_rate_data(run))
    chk, info = _check_grid_freeze(run, freeze_path, allow_grid_change)
    checks.append(chk)
    if info is not None:
        result["grid_freeze"] = info

    if previous_dir is None:
        result["diff"] = {
            "status": "skipped",
            "reason": "no previous build/current to compare with (first run)",
        }
        checks.append(_check("history_diff", True, result["diff"]["reason"], status="SKIP"))
    else:
        # A previous run exists: if it cannot be read, the comparison cannot be made, which is a
        # failure and never a silent skip.
        try:
            prev = Path(previous_dir)
            prev_last = last_session_date(prev)
            new_last = market.get("last_date") or last_session_date(run)
            cutoff = min(prev_last, new_last)
            d = diff_history(prev, run, cutoff)
            d.update(previous=prev.name, previous_last=prev_last, new_last=new_last)
            result["diff"] = d
            checks.append(
                _check(
                    "history_diff",
                    d["total_changes"] <= max_historical_changes,
                    f"compared events before {cutoff} with {prev.name}: {d['total_changes']} "
                    f"historical changes (added {d['added']}, removed {d['removed']}, changed "
                    f"{d['changed']}); limit {max_historical_changes}",
                )
            )
        except (OSError, ValueError, KeyError, csv.Error) as exc:
            result["diff"] = {
                "status": "error",
                "reason": f"previous run {Path(previous_dir).name} unreadable: {exc}",
            }
            checks.append(
                _check(
                    "history_diff",
                    False,
                    f"previous run {Path(previous_dir).name} unreadable: "
                    f"{type(exc).__name__}: {exc}",
                )
            )
    if check_site:
        checks.append(_check_site(run))
        if key_facts:
            checks.append(_check_key_facts(run))
            checks.append(_check_site_render(run))
            checks.append(recheck.check_picks(run))
    else:
        checks.append(
            _check(
                "site_files",
                True,
                "--no-site-check: the site files, key-point facts and browser check were NOT run",
                status="WARN",
            )
        )
    checks.extend(extra_checks or [])
    if not check_site:
        checks.append(
            _check("browser_check", True, "--no-site-check: browser check not run", status="WARN")
        )

    result["checks"] = checks
    result["status"] = "FAIL" if any(c["status"] == "FAIL" for c in checks) else "PASS"
    result["files"] = hash_files(run)
    return result
