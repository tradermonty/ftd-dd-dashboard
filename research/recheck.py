"""Independent recomputation of a run's results (publish gate).

  check_picks(run)            re-runs the selectors (A, B, C and R2) on the DESIGN rows recorded
                              in the run's own variants.csv / overlay_variants.csv and compares
                              the picks, the tie-break amendment list and the R2 tie effect with
                              what selected.json / overlay_selected.json record. Files only.
  check_results(run, hist)    recomputes EVERYTHING from the cached price history with the same
                              code the pipeline used (replay, events, outcomes, research R and
                              R2, the report) and requires the run's files to be identical,
                              ignoring only the generation timestamps. Also checks that the
                              recorded full-period contamination figures equal the data.

Both return {"name", "status", "detail"} dicts for webgen.validate.
"""

from __future__ import annotations

import csv
import json
import math
import re
import sys
import tempfile
from datetime import date
from pathlib import Path

from engine import fetch
from research import contamination, overlay, selection

ROOT = Path(__file__).resolve().parents[1]
REL_TOL = 1e-9


def _read(path: Path) -> list[dict]:
    with Path(path).open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


def _json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _f(x):
    return None if x in ("", None) else float(x)


def _i(x):
    return None if x in ("", None) else int(float(x))


# --------------------------------------------------------------------- numeric comparison


def close(a, b) -> bool:
    """Equality of two JSON-like numbers at a relative tolerance; None only equals None."""
    if a is None or b is None:
        return a is None and b is None
    if isinstance(a, bool) or isinstance(b, bool):
        return a is b
    if isinstance(a, int | float) and isinstance(b, int | float):
        if math.isnan(a) and math.isnan(b):
            return True
        return abs(a - b) <= REL_TOL * max(1.0, abs(a), abs(b))
    return a == b


def first_diff(a, b, path: str = "") -> str:
    """Path of the first difference between two JSON-like values (floats at REL_TOL)."""
    if isinstance(a, dict) and isinstance(b, dict):
        for k in sorted(set(a) | set(b)):
            if k not in a or k not in b:
                return f"{path}/{k} missing on one side"
            d = first_diff(a[k], b[k], f"{path}/{k}")
            if d:
                return d
        return ""
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}: {len(a)} items != {len(b)} items"
        for i, (x, y) in enumerate(zip(a, b, strict=True)):
            d = first_diff(x, y, f"{path}[{i}]")
            if d:
                return d
        return ""
    return "" if close(a, b) else f"{path}: {a!r} != {b!r}"


# --------------------------------------------------------------------------------- picks


def _design_rows(run: Path) -> dict[str, list[dict]]:
    """Design rows for selectors A, B, C and R2 from the run's own CSV files."""
    rows = _read(Path(run) / "research" / "variants.csv")
    a, b, c, seen = [], [], [], set()
    for r in rows:
        if r.get("period") not in ("design", ""):
            continue
        if r["family"] == "ftd":
            a.append(
                {
                    "variant_id": r["variant_id"],
                    "index": r["index"],
                    "period": "design",
                    "n_trades": _i(r["A_n_trades"]) or 0,
                    "median": _f(r["A_median"]),
                }
            )
        elif r["family"] == "dd":
            b.append(
                {
                    "variant_id": r["variant_id"],
                    "index": r["index"],
                    "period": "design",
                    "cagr": _f(r["B_cagr"]),
                    "bh_cagr": _f(r["B_bh_cagr"]),
                    "max_dd": _f(r["B_max_dd"]),
                    "n_days": _i(r["B_n_days"]) or 0,
                }
            )
            key = (r["signal_id"], r["index"])
            if key not in seen:
                seen.add(key)
                c.append(
                    {
                        "signal_id": r["signal_id"],
                        "index": r["index"],
                        "period": "design",
                        "n_elevated": _i(r["C_n_elevated"]) or 0,
                        "n_normal": _i(r["C_n_normal"]) or 0,
                        "spread": _f(r["C_spread"]),
                    }
                )
    ov = []
    for r in _read(Path(run) / "research" / "overlay_variants.csv"):
        if r["cost_model"] != overlay.MAIN_COST_ID:
            continue
        ov.append(
            {
                "variant_id": r["variant_id"],
                "index": r["index"],
                "period": "design",
                "cagr": _f(r["cagr"]),
                "max_dd": _f(r["max_dd"]),
                "bh_max_dd": _f(r["bh_max_dd"]),
            }
        )
    return {"A": a, "B": b, "C": c, "overlay": ov}


def recompute_picks(run: Path) -> dict:
    """Picks by the amended tie rule ('new') and the old one ('old'), the changed ones, and R2."""
    sel = _json(Path(run) / "research" / "selected.json")
    base = sel["baseline"]
    rows = _design_rows(run)
    new = {
        "A": selection.select_a(rows["A"], base["A"]),
        "B": selection.select_b(rows["B"], base["B"]),
        "C": selection.select_c(rows["C"], base["C"]),
    }
    old = {
        "A": selection.select_a(rows["A"], base["A"], tie=selection.TIE_OLD),
        "B": selection.select_b(rows["B"], base["B"], tie=selection.TIE_OLD),
        "C": selection.select_c(rows["C"], base["C"], tie=selection.TIE_OLD),
    }
    return {
        "new": new,
        "old": old,
        "affected": selection.affected_picks(new, old),
        "overlay": overlay.select_overlay(rows["overlay"]),
        "overlay_tie": overlay.tie_rule_effect(rows["overlay"]),
    }


def check_picks(run: Path) -> dict:
    """The recorded picks must equal a re-run of the selectors on the run's own design rows."""
    name = "picks_recomputed"
    run = Path(run)
    try:
        sel = _json(run / "research" / "selected.json")
        ov = _json(run / "research" / "overlay_selected.json")
        got = recompute_picks(run)
    except (OSError, ValueError, KeyError, TypeError, csv.Error) as exc:
        return {"name": name, "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"}
    problems: list[str] = []
    n = 0
    for ev, per_index in got["new"].items():
        for idx, info in per_index.items():
            n += 1
            rec = sel["evaluations"].get(ev, {}).get(idx, {})
            if rec.get("selected") != info["selected"]:
                problems.append(
                    f"{ev} {idx}: recorded pick {rec.get('selected')!r}, recomputed "
                    f"{info['selected']!r}"
                )
            if rec.get("fallback_to_baseline") != info["fallback_to_baseline"]:
                problems.append(f"{ev} {idx}: fallback flag differs")
    if sel.get("tie_break_affected_picks") != got["affected"]:
        problems.append(
            f"tie_break_affected_picks recorded {sel.get('tie_break_affected_picks')!r}, "
            f"recomputed {got['affected']!r}"
        )
    for idx, info in got["overlay"].items():
        n += 1
        rec = ov["indices"].get(idx, {})
        if rec.get("selected") != info["selected"]:
            problems.append(
                f"R2 {idx}: recorded pick {rec.get('selected')!r}, recomputed {info['selected']!r}"
            )
        d = first_diff(rec.get("tie_effect"), got["overlay_tie"][idx], f"R2 {idx} tie_effect")
        if d:
            problems.append(d)
    return {
        "name": name,
        "status": "FAIL" if problems else "PASS",
        "detail": "; ".join(problems[:4])
        or f"{n} picks and the tie-break amendment list equal a re-run of the selectors on the "
        f"design rows; {len(got['affected'])} pick(s) changed by the amended tie rule",
    }


# ------------------------------------------------------------------------------- results

# Files of a run that the recomputation compares byte for byte, relative to the run directory.
DATA_FILES = (
    "data/daily_status.csv",
    "data/ftd_events.csv",
    "data/distribution_days.csv",
    "data/ftd_outcomes.csv",
    "data/rally_attempts.csv",
    "data/dd_bucket_stats.csv",
    "data/risk_level_stats.csv",
    "data/data_issues.csv",
)
RESEARCH_FILES = (
    "research/variants.csv",
    "research/test_results.csv",
    "research/selected.json",
    "research/overlay_variants.csv",
    "research/overlay_test_results.csv",
    "research/overlay_selected.json",
)
REPORT_FILE = "research/report.html"
SUMMARY_FILE = "data/summary.json"
EXACT_FILES = (*DATA_FILES, *RESEARCH_FILES)
_REPORT_STAMP = re.compile(r"(生成: )\d{4}-\d\d-\d\d \d\d:\d\d UTC")


def _no_network(url: str, params: dict):
    raise fetch.FetchError("network access is disabled (recomputation uses the fetch cache)")


def cached_histories(last: str) -> tuple[dict[str, list[dict]], list[dict]]:
    """Price history of both indices up to `last` from the local fetch cache (never the
    network), and the data issues found in it."""
    histories, issues = {}, []
    for sym in fetch.DEFAULT_SYMBOLS:
        res = fetch.fetch_index(
            sym,
            end=date.fromisoformat(last),
            use_cache=True,
            http_get=_no_network,
            api_key="unused",
        )
        histories[sym] = res.rows
        issues.extend(res.issues)
    return histories, issues


def _scripts():
    """The build scripts (scripts/ is not a package)."""
    path = str(ROOT / "scripts")
    if path not in sys.path:
        sys.path.insert(0, path)
    import build_events
    import run_research

    return build_events, run_research


def regenerate(
    histories: dict[str, list[dict]],
    issues: list[dict],
    dff_path: Path,
    out_dir: Path,
) -> Path:
    """Run the whole computation again (replay, events, outcomes, research R and R2) from the
    price history, into `out_dir`, with the pipeline's own functions. `dff_path` is the FRED
    file that the run being checked used (its copy in research/); no network is touched."""
    build_events, run_research = _scripts()
    out_dir = Path(out_dir)
    build_events.build_all(histories, out_dir / "data", issues)
    run_research.run(
        histories, out_dir / "research", log=lambda m: None, dff_path=dff_path, dff_network=False
    )
    return out_dir


def _first_line_diff(a: str, b: str) -> str:
    la, lb = a.splitlines(), b.splitlines()
    for i in range(max(len(la), len(lb))):
        x = la[i] if i < len(la) else "<end>"
        y = lb[i] if i < len(lb) else "<end>"
        if x != y:
            # show the first differing cell of a CSV row when the lines look like CSV
            cx, cy = x.split(","), y.split(",")
            if len(cx) == len(cy) > 1:
                j = next((k for k in range(len(cx)) if cx[k] != cy[k]), 0)
                return (
                    f"line {i + 1}, column {j + 1}: run {cx[j][:60]!r} != recomputed {cy[j][:60]!r}"
                )
            return f"line {i + 1}: run {x[:80]!r} != recomputed {y[:80]!r}"
    return "differs"


def compare_with_run(run: Path, fresh: Path) -> list[str]:
    """Differences between a run's result files and the recomputed ones in `fresh`.

    Every CSV and JSON result file must be identical byte for byte. The only things ignored are
    the generation timestamps: `generated_at` in summary.json and the 'generated' stamp in
    report.html. Returns one line per differing file (empty = identical)."""
    run, fresh = Path(run), Path(fresh)
    problems: list[str] = []
    for rel in EXACT_FILES:
        a, b = run / rel, fresh / rel
        if not a.is_file():
            problems.append(f"{rel}: missing in the run")
        elif a.read_bytes() != b.read_bytes():
            problems.append(
                f"{rel} differs from the recomputation: "
                f"{_first_line_diff(a.read_text(encoding='utf-8'), b.read_text(encoding='utf-8'))}"
            )
    try:
        sa, sb = _json(run / SUMMARY_FILE), _json(fresh / SUMMARY_FILE)
        sa.pop("generated_at", None)
        sb.pop("generated_at", None)
        d = first_diff(sa, sb, "summary.json")
        if sa != sb:
            problems.append(f"{SUMMARY_FILE} differs from the recomputation: {d or 'values'}")
    except (OSError, ValueError) as exc:
        problems.append(f"{SUMMARY_FILE}: {type(exc).__name__}: {exc}")
    ra, rb = run / REPORT_FILE, fresh / REPORT_FILE
    if not ra.is_file():
        problems.append(f"{REPORT_FILE}: missing in the run")
    else:
        ta = _REPORT_STAMP.sub(r"\1<generated>", ra.read_text(encoding="utf-8"))
        tb = _REPORT_STAMP.sub(r"\1<generated>", rb.read_text(encoding="utf-8"))
        if ta != tb:
            problems.append(
                f"{REPORT_FILE} differs from the recomputation: {_first_line_diff(ta, tb)}"
            )
    return problems


def check_results(
    run: Path,
    histories: dict[str, list[dict]],
    issues: list[dict] | None = None,
    fresh: Path | None = None,
) -> dict:
    """The run's replay, event, outcome and research files must equal a full recomputation.

    `fresh`: a directory that already holds the recomputation (tests compute it once); without
    it the computation is run here, in a temporary directory (about 20 to 30 seconds). It is
    deterministic, so nothing but the generation timestamps may differ. The FRED file used is
    the run's own copy (research/fred_DFF.csv); its hash is part of overlay_selected.json."""
    name = "results_recomputed"
    run = Path(run)
    problems: list[str] = []
    try:
        problems += contamination.verify(run / "data" / "daily_status.csv")
        if fresh is None:
            with tempfile.TemporaryDirectory(prefix="ftd-dd-recheck-") as tmp:
                regenerate(histories, issues or [], run / "research" / "fred_DFF.csv", Path(tmp))
                problems += compare_with_run(run, Path(tmp))
        else:
            problems += compare_with_run(run, fresh)
    except (OSError, ValueError, KeyError, TypeError, csv.Error, RuntimeError) as exc:
        return {"name": name, "status": "FAIL", "detail": f"{type(exc).__name__}: {exc}"[:500]}
    return {
        "name": name,
        "status": "FAIL" if problems else "PASS",
        "detail": "; ".join(problems[:4])
        or f"{len(EXACT_FILES) + 2} result files (replay, event and outcome CSVs, summary.json, "
        "the research CSVs and JSON, report.html) equal a full recomputation from the cached "
        "price history; only the generation timestamps were ignored; the recorded "
        "contamination figures equal the data",
    }
