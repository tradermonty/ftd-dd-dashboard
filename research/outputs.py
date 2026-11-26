"""Turn a pipeline result into the files of build/research/ (CSV / JSON)."""

from __future__ import annotations

import csv
from pathlib import Path

from research import contamination, freeze, selection, variants
from research.split import DESIGN_END, DESIGN_START, HORIZON, TEST_START

KEY = {"A": "variant_id", "B": "variant_id", "C": "signal_id"}
TITLES = {
    "A": "FTD as a trade (buy detected_on close; stop: close below FTD-day low; 60 sessions)",
    "B": "DD risk as exposure (100% / reduced exposure from next session, cash 0%)",
    "C": "P(>=10% drawdown within 60 sessions) by risk state",
}

A_COLS = [
    "n_events",
    "n_trades",
    "n_dropped_incomplete_window",
    "n_skipped_invalidated_at_entry",
    "win_rate",
    "median",
    "mean",
    "p10",
    "p90",
    "profit_factor",
    "n_stopped",
]
B_COLS = [
    "n_days",
    "years",
    "cagr",
    "max_dd",
    "vol",
    "avg_exposure",
    "bh_cagr",
    "bh_max_dd",
    "bh_vol",
    "em_cagr",
    "em_max_dd",
]
C_COLS = [
    "n_elevated",
    "p_elevated",
    "n_normal",
    "p_normal",
    "spread",
    "n_dropped_incomplete_window",
    *[f"{k}_{lv}" for lv in ("NORMAL", "CAUTION", "HIGH", "SEVERE") for k in ("n", "p")],
]
METRIC_COLS = {"A": A_COLS, "B": B_COLS, "C": C_COLS}


def write_csv(path: Path, columns: list[str], rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=columns, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in columns})


def variants_rows(result: dict, ftd_vars: list[dict], dd_vars: list[dict]) -> list[dict]:
    """All variants x index with their DESIGN-period metrics."""
    a = {(r["variant_id"], r["index"]): r for r in result["design"]["A"]}
    b = {(r["variant_id"], r["index"]): r for r in result["design"]["B"]}
    c = {(r["signal_id"], r["index"]): r for r in result["design"]["C"]}
    out = []
    for v in ftd_vars:
        for idx in sorted({k[1] for k in a}):
            out.append(
                {
                    "family": "ftd",
                    "variant_id": v["id"],
                    "baseline": v["baseline"],
                    "index": idx,
                    "correction_pct": v["correction_pct"],
                    "min_gain": v["min_gain"],
                    "require_vol_above_avg": v["require_vol_above_avg"],
                    "period": "design",
                }
                | {f"A_{k}": a[(v["id"], idx)].get(k) for k in A_COLS}
            )
    for v in dd_vars:
        for idx in sorted({k[1] for k in b}):
            row = {
                "family": "dd",
                "variant_id": v["id"],
                "baseline": v["baseline"],
                "index": idx,
                "signal_id": v["signal_id"],
                "min_decline_frac": v["min_decline_pct"],
                "volume_rule": v["volume_rule"],
                "risk_rule": v["risk_rule"],
                "exposure": v["exposure"],
                "period": "design",
            }
            row |= {f"B_{k}": b[(v["id"], idx)].get(k) for k in B_COLS}
            row |= {f"C_{k}": c[(v["signal_id"], idx)].get(k) for k in C_COLS}
            out.append(row)
    return out


VARIANT_COLUMNS = (
    ["family", "variant_id", "baseline", "index", "period"]
    + ["correction_pct", "min_gain", "require_vol_above_avg"]
    + ["signal_id", "min_decline_frac", "volume_rule", "risk_rule", "exposure"]
    + [f"A_{k}" for k in A_COLS]
    + [f"B_{k}" for k in B_COLS]
    + [f"C_{k}" for k in C_COLS]
)


def _find(rows: list[dict], key: str, vid: str, index: str, period: str | None = None):
    for r in rows:
        if r[key] == vid and r["index"] == index and (period is None or r["period"] == period):
            return r
    return None


def compared(result: dict) -> dict:
    """{eval: {index: {"baseline": id, "selected": id, "design": {role: row}, "test": {...}}}}"""
    out: dict = {}
    for ev in "ABC":
        key = KEY[ev]
        out[ev] = {}
        for index, ch in result["chosen"][ev].items():
            ids = {"baseline": result["baseline"][ev], "selected": ch["selected"]}
            out[ev][index] = {
                "baseline": ids["baseline"],
                "selected": ids["selected"],
                "fallback_to_baseline": ch["fallback_to_baseline"],
                "n_considered": ch["n_considered"],
                "n_qualifying": ch["n_qualifying"],
                "design": {
                    role: _find(result["design_of_compared"][ev], key, vid, index)
                    for role, vid in ids.items()
                },
                "test": {
                    role: _find(result["test"][ev], key, vid, index) for role, vid in ids.items()
                },
            }
    return out


def test_result_rows(result: dict) -> list[dict]:
    out = []
    for ev, per_index in compared(result).items():
        for index, info in per_index.items():
            roles = (
                ["baseline"] if info["baseline"] == info["selected"] else ["baseline", "selected"]
            )
            for role in roles:
                row = info["test"][role]
                out.append(
                    {
                        "evaluation": ev,
                        "index": index,
                        "role": "baseline=selected" if len(roles) == 1 else role,
                        "variant_id": info[role],
                        "period": "test",
                    }
                    | {k: row.get(k) for k in METRIC_COLS[ev]}
                )
    return out


TEST_COLUMNS = ["evaluation", "index", "role", "variant_id", "period"] + sorted(
    {c for cols in METRIC_COLS.values() for c in cols}, key=lambda c: c
)


def tie_comparison(result: dict) -> list[dict]:
    """For each pick the amended tie rule changed: design and test metrics of the pick the old
    string-order rule made (`before`) and of the pick now in force (`after`)."""
    cmp = compared(result)
    out = []
    for row in result.get("tie_rows", []):
        ev, idx = row["evaluation"], row["index"]
        keys = METRIC_COLS[ev]
        info = cmp[ev][idx]
        out.append(
            {
                "evaluation": ev,
                "index": idx,
                "before": {
                    "id": row["before"],
                    "design": {k: row["design"].get(k) for k in keys},
                    "test": {k: row["test"].get(k) for k in keys},
                },
                "after": {
                    "id": row["after"],
                    "design": {k: info["design"]["selected"].get(k) for k in keys},
                    "test": {k: info["test"]["selected"].get(k) for k in keys},
                },
            }
        )
    return out


def selected_json(
    result: dict, ftd_vars: list[dict], dd_vars: list[dict], data_range: dict
) -> dict:
    cmp = compared(result)
    return {
        "tie_break_affected_picks": list(result.get("tie_affected", [])),
        "tie_break_comparison": tie_comparison(result),
        "split": {
            "design": [DESIGN_START, DESIGN_END],
            "test": [TEST_START, result["test_end"]],
            "forward_horizon_sessions": HORIZON,
        },
        "forward_record": result["forward"],
        "test_period_contamination": contamination.CONTAMINATION_R,
        "data_range": data_range,
        "variant_counts": variants.count_summary(),
        "n_variants_tried": len(ftd_vars) + len(dd_vars),
        "criteria": selection.CRITERIA,
        "tie_rule": selection.TIE_RULE,
        "post_hoc_amendments": selection.POST_HOC_AMENDMENTS,
        "freeze": freeze.info("selection"),
        "baseline": result["baseline"],
        "evaluations": {
            ev: {
                idx: {k: v for k, v in info.items() if k != "design" and k != "test"}
                | {
                    "design": info["design"],
                    "test": info["test"],
                }
                for idx, info in per_index.items()
            }
            for ev, per_index in cmp.items()
        },
    }
