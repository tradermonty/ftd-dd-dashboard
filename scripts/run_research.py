#!/usr/bin/env python3
"""Run research phase R (docs/plan.md §3.6) on cached FMP data. No FMP API calls.

Writes build/research/: variants.csv, selected.json, test_results.csv, report.html and the R2
files overlay_variants.csv, overlay_selected.json, overlay_test_results.csv. The FRED DFF rate
is kept in data/raw/fred_DFF.csv. It is refreshed when its last observation is earlier than the
second-to-last session of the price data, or when the file is more than 3 days old, but only
when the network is allowed (--refresh-fred); without it a stale file stops the run with a clear
message.

Usage:
    python scripts/run_research.py [--out-dir DIR] [--end YYYY-MM-DD] [--refresh-fred]
"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from engine import fetch  # noqa: E402
from research import freeze, outputs, overlay, pipeline, report, variants  # noqa: E402
from research.split import DESIGN_END, DESIGN_START, TEST_END, TEST_START  # noqa: E402

DFF_PATH = ROOT / "data" / "raw" / "fred_DFF.csv"


def run_overlay_phase(
    histories: dict,
    result: dict,
    od: Path,
    dff_path: Path,
    log=print,
    dff_network: bool = False,
    dff_fetch: Callable[[str], str] | None = None,
) -> dict:
    """Research R2 (docs/plan.md section 3.7) on the point-in-time baseline signals.

    The FRED file used is copied into the research folder (research/fred_DFF.csv) so the run
    can be recomputed from its own files. `dff_network`: whether a stale FRED cache may be
    downloaded again (never by default); `dff_fetch` is the downloader (tests inject one)."""
    sig = result["signals"]
    base, dd = variants.BASELINE_FTD, variants.BASELINE_DD
    events = sig.ftd_events(base["correction_pct"], base["min_gain"])
    is_dd = {
        sym: sig.dd_series(sym, dd["min_decline_pct"], dd["volume_rule"]).is_dd for sym in histories
    }
    dates = [b["date"] for b in next(iter(histories.values()))]
    dff = overlay.load_or_fetch_dff(
        dff_path, dff_fetch, dates=dates, allow_network=dff_network, now=None
    )
    res = overlay.run_overlay(
        histories, events, is_dd, dff, DESIGN_START, DESIGN_END, TEST_START, log=log
    )
    od = Path(od)
    od.mkdir(parents=True, exist_ok=True)
    kept = od / overlay.DFF_COPY_NAME
    shutil.copyfile(dff_path, kept)
    return overlay.write_outputs(
        res,
        od,
        provenance=(freeze.stored_provenance() or {}).get("files", {}).get("overlay"),
        dff_source=overlay.dff_source(kept),
    )


def run(
    histories: dict,
    od: Path,
    log=print,
    dff_path: Path | None = None,
    dff_network: bool = False,
    dff_fetch: Callable[[str], str] | None = None,
) -> dict:
    """Run phase R on already-fetched histories and write the research files into `od`.

    Everything written is a deterministic function of the histories, the DFF file and the code
    (report.html carries a generation time, the one thing the gate masks), so the publish gate
    can compute the same files again and require them to be identical."""
    ftd_vars, dd_vars = variants.ftd_variants(), variants.dd_variants()
    log(f"data loaded; {len(ftd_vars)} FTD + {len(dd_vars)} DD variants")
    result = pipeline.run_pipeline(histories, ftd_vars, dd_vars, log=log)
    data_range = {
        "first": next(iter(histories.values()))[0]["date"],
        "last": result["last_date"],
    }
    od = Path(od)
    outputs.write_csv(
        od / "variants.csv",
        outputs.VARIANT_COLUMNS,
        outputs.variants_rows(result, ftd_vars, dd_vars),
    )
    outputs.write_csv(
        od / "test_results.csv", outputs.TEST_COLUMNS, outputs.test_result_rows(result)
    )
    selected = outputs.selected_json(result, ftd_vars, dd_vars, data_range)
    selected["freeze_provenance"] = (
        (freeze.stored_provenance() or {}).get("files", {}).get("selection")
    )
    (od / "selected.json").write_text(
        json.dumps(selected, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    ov = run_overlay_phase(
        histories,
        result,
        od,
        dff_path or DFF_PATH,
        log=log,
        dff_network=dff_network,
        dff_fetch=dff_fetch,
    )
    log(f"overlay (R2) done: {ov['n_variants']} variants")
    (od / "report.html").write_text(report.render(result, selected, overlay=ov), encoding="utf-8")
    return selected


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--out-dir", type=Path, default=ROOT / "build" / "research")
    ap.add_argument("--end", default=None)
    ap.add_argument(
        "--refresh-fred",
        action="store_true",
        help="allow downloading the FRED DFF file when the cache is stale",
    )
    args = ap.parse_args(argv)

    t0 = time.time()
    end = date.fromisoformat(args.end) if args.end else None
    # use_cache=True: cached chunks are read; a missing chunk would trigger a fetch, so make
    # the "zero API calls" guarantee explicit by failing instead.
    histories = {}
    for sym in fetch.DEFAULT_SYMBOLS:
        histories[sym] = fetch.fetch_index(
            sym, end=end, use_cache=True, http_get=_no_network, api_key="unused"
        ).rows

    def log(msg: str) -> None:
        print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)

    run(histories, args.out_dir, log=log, dff_network=args.refresh_fred)
    log(f"done -> {args.out_dir} (test period ends {TEST_END})")
    return 0


def _no_network(url: str, params: dict):
    raise fetch.FetchError("network access is disabled for research runs (cache miss)")


if __name__ == "__main__":
    sys.exit(main())
