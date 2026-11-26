"""Synthetic run directory used by the publish-gate and site tests. No network, no FMP data."""

from __future__ import annotations

import csv
import json
import shutil
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from engine import fetch
from webgen import dictionary

ROOT = Path(__file__).resolve().parents[1]
ET = ZoneInfo("America/New_York")
INDICES = ("^GSPC", "^IXIC")
FIRST, LAST = "2024-01-02", "2024-03-28"  # Good Friday 03-29 is closed: LAST is a session
NOW = datetime(2024, 3, 28, 17, 0, tzinfo=ET)  # after the 16:30 ET bar is final


def sessions(through: str | None = None) -> list[str]:
    last = through or LAST
    return [d.strftime("%Y-%m-%d") for d in fetch.get_xnys().sessions_in_range(FIRST, last)]


def write_csv(path: Path, cols: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)


def ftd_rows() -> list[dict]:
    return [
        {
            "index": sym,
            "rally_day1_date": "2024-01-05",
            "swing_low_date": "2024-01-04",
            "ftd_date": f"2024-01-1{k}",
            "detected_on": f"2024-01-1{k}",
            "lag_sessions": 0,
            "ftd_day_number": 5,
            "gain_pct": 1.8,
            "volume_vs_prev_pct": 10.0,
            "volume_above_avg": True,
            "invalidated_at_detection": False,
            "invalidation_date": "",
            "invalidated_within_60": "",
        }
        for sym in INDICES
        for k in (2, 3)
    ]


def dd_rows(n: int = 12) -> list[dict]:
    s = sessions()
    return [
        {
            "index": sym,
            "date": s[5 + k],
            "pct_change": -0.5,
            "volume_change_pct": 5.0,
            "expires_on": s[5 + k + 26] if 5 + k + 26 < len(s) else "",
            "invalidated_on": "",
            "removal_reason": "",
        }
        for sym in INDICES
        for k in range(n // 2)
    ]


def write_fred(path: Path, through: str, first: str = "2023-12-01", rate: float = 5.33) -> None:
    """Synthetic FRED DFF file with an observation on every calendar day first..through."""
    from datetime import date, timedelta

    d, end, rows = date.fromisoformat(first), date.fromisoformat(through), []
    while d <= end:
        rows.append({"observation_date": d.isoformat(), "DFF": rate})
        d += timedelta(days=1)
    write_csv(path, ["observation_date", "DFF"], rows)


FTD_IDS = ("ftd_c3_g1.25_v0", "ftd_c5_g1.25_v0")
DD_ID, DD_SIGNAL = "dd_d-0.2_prev_day_skill_high_e50", "dd_d-0.2_prev_day_skill_high"
FIXTURE_FREEZE = {"grid_sha256": "a" * 64, "pre_registered": "2026-11-07"}


def build_research(run: Path, last: str) -> None:
    """Consistent research/ files: 3 variants x 2 indices, R2 with 12 variants x 4 costs."""
    from research import outputs, overlay

    r = run / "research"
    r.mkdir(exist_ok=True)
    rows = []
    for idx in INDICES:
        for vid in FTD_IDS:
            rows.append({"family": "ftd", "variant_id": vid, "index": idx, "period": "design"})
        rows.append(
            {
                "family": "dd",
                "variant_id": DD_ID,
                "signal_id": DD_SIGNAL,
                "index": idx,
                "period": "design",
            }
        )
    write_csv(r / "variants.csv", outputs.VARIANT_COLUMNS, rows)
    evals = {
        "A": {i: {"baseline": FTD_IDS[0], "selected": FTD_IDS[1]} for i in INDICES},
        "B": {i: {"baseline": DD_ID, "selected": DD_ID} for i in INDICES},
        "C": {i: {"baseline": DD_SIGNAL, "selected": DD_SIGNAL} for i in INDICES},
    }
    trows = [
        {"evaluation": "A", "index": i, "role": "selected", "variant_id": FTD_IDS[1]}
        for i in INDICES
    ]
    write_csv(r / "test_results.csv", outputs.TEST_COLUMNS, trows)
    sel = {
        "split": {"design": ["2000-01-03", "2014-12-31"], "test": ["2015-01-02", last]},
        "data_range": {"first": FIRST, "last": last},
        "n_variants_tried": 3,
        "evaluations": evals,
        "freeze": dict(FIXTURE_FREEZE),
        "post_hoc_amendments": [],
    }
    (r / "selected.json").write_text(json.dumps(sel, indent=2))
    write_fred(r / "fred_DFF.csv", last)
    ovrows = [
        {"variant_id": v["id"], "index": i, "period": "design", "cost_model": c["id"]}
        for i in INDICES
        for v in overlay.variants()
        for c in overlay.COST_MODELS
    ]
    write_csv(r / "overlay_variants.csv", overlay.VARIANT_COLUMNS, ovrows)
    base = {"role": "baseline", "variant_id": "buy_hold", "period": "test"}
    write_csv(
        r / "overlay_test_results.csv",
        overlay.TEST_COLUMNS,
        [{"index": i, **base} for i in INDICES],
    )
    ov = {
        "split": {"design": ["2000-01-03", "2014-12-31"], "test": ["2015-01-02", last]},
        "n_variants": len(overlay.variants()),
        "cost_models": overlay.COST_MODELS,
        "indices": {
            i: {"selected": overlay.variants()[0]["id"], "fallback_to_baseline": False}
            for i in INDICES
        },
        "freeze": dict(FIXTURE_FREEZE),
        "post_hoc_amendments": [],
    }
    (r / "overlay_selected.json").write_text(json.dumps(ov, indent=2))


def build_run(
    run: Path,
    *,
    ftd=None,
    dd=None,
    vendor_ref: str | None = None,
    last: str | None = None,
    through: str | None = None,
) -> Path:
    """Write a consistent run directory (data/, research/) and return it.

    `through`: last session of the run (default LAST); `last` only overrides the date written
    into summary.json."""
    ftd = ftd_rows() if ftd is None else ftd
    dd = dd_rows() if dd is None else dd
    lock = json.loads((ROOT / "vendor" / "VENDOR.lock").read_text())
    s = sessions(through)
    last = last or through or LAST
    status = []
    for sym in INDICES:
        for k, d in enumerate(s):
            status.append(
                {
                    "date": d,
                    "index": sym,
                    "close": 4000 + k,
                    "pct_change": 0.001,
                    "volume_change": 0.01,
                    "is_dd": False,
                    "d5": 1,
                    "d15": 2,
                    "d25": 3,
                    "ftd_state": "NO_SIGNAL",
                    "ftd_date": "",
                    "rally_day1_date": "",
                    "swing_low_date": "",
                    "combined_state": "NO_SIGNAL",
                    "risk_level": "CAUTION" if k > 30 else "NORMAL",
                    "market_below_ma": False,
                }
            )
    data = run / "data"
    for path, cols in dictionary.files().items():
        name = [c for c, _ in cols]
        write_csv(run / path, name, [])
    write_csv(data / "daily_status.csv", dictionary.columns("data/daily_status.csv"), status)
    write_csv(data / "ftd_events.csv", dictionary.columns("data/ftd_events.csv"), ftd)
    write_csv(data / "distribution_days.csv", dictionary.columns("data/distribution_days.csv"), dd)
    summary = {
        "generated_at": "2024-03-28T22:00:00Z",
        "vendor_ref": vendor_ref or lock["ref"],
        "data_range": {"fetched_first": "2023-06-01", "replay_first": FIRST, "last": last or LAST},
        "counts": {
            "ftd_events": len(ftd),
            "rally_attempts": 0,
            "distribution_days": len(dd),
            "replay_rows": len(status),
        },
        "per_index": {
            sym: {
                "ftd_events": sum(1 for r in ftd if r["index"] == sym),
                "distribution_days": sum(1 for r in dd if r["index"] == sym),
                "rally_attempts": 0,
            }
            for sym in INDICES
        },
    }
    (data / "summary.json").write_text(json.dumps(summary, indent=2))
    (data / "data_dictionary.md").write_text(dictionary.render_markdown(), encoding="utf-8")
    build_research(run, last)
    return run


def copy_vendor(dest: Path) -> Path:
    shutil.copytree(ROOT / "vendor", dest)
    return dest
