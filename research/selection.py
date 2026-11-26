"""Variant selection (spec §3.6). Criteria are fixed here BEFORE any result is computed.

Structural split enforcement: every selector receives only metric rows tagged
period == "design" and raises if it is handed anything else. Test-period metrics are
computed by the caller afterwards, only for the baseline and the selected variant.

Criteria (per index, evaluated separately):
  A. Among FTD variants with n_trades >= 15 on the design period: highest median trade return.
  B. Among DD variants whose design CAGR >= buy&hold design CAGR - 2 percentage points:
     smallest design max drawdown (closest to 0); ties -> higher CAGR.
  C. Among DD signal definitions with >= 200 elevated days and >= 200 normal days on the
     design period: largest P(>=10% drawdown within 60 sessions | elevated) minus
     P(... | normal).
If no variant qualifies for an index, the baseline is kept and the fallback is recorded.

Tie rule (POST-HOC AMENDMENT, 2026-11-07): the original criteria did not say what happens on
an exact tie of the primary metric; the code silently fell back to string order of the id
("ftd_c10" < "ftd_c3"). A review found an exact tie on ^GSPC evaluation A (three variants with
the same design median; the one with 15 trades won). The rule below was defined AFTER the test
results had been seen, so it is recorded as an amendment in selected.json / overlay_selected.json
and shown on the research page. See TIE_RULE.
"""

from __future__ import annotations

from collections.abc import Sequence
from functools import cache

from research import variants

MIN_TRADES_A = 15
CAGR_TOLERANCE_B = 0.02
MIN_DAYS_C = 200

TIE_RULE = (
    "Exact ties on the primary metric are broken by: more design trades / observations first, "
    "then simpler (closer to the baseline) parameters: smaller correction %, then smaller "
    "minimum gain, then volume filter off (FTD); smaller decline threshold, then the baseline "
    "volume rule and risk rule order, then the baseline exposure first (DD); lower leverage, "
    "then smaller k, then invalidation exit on (R2); and finally the variant id."
)
POST_HOC_AMENDMENTS = [
    {
        "date": "2026-11-07",
        "what": "tie-break rule defined (was implicit string order of the variant id)",
        "reason": (
            "review found an exact tie on the design median (^GSPC, evaluation A, three "
            "variants); the rule was written after the test results had been seen"
        ),
    }
]

CRITERIA = {
    "A": (
        f"design only; variants with n_trades >= {MIN_TRADES_A}; maximise median trade return "
        "(entry: detected_on close; exit: close below FTD-day low or after 60 sessions); "
        "ties: see tie rule"
    ),
    "B": (
        f"design only; variants with CAGR >= buy&hold CAGR - {CAGR_TOLERANCE_B * 100:g}pp; "
        "minimise max drawdown (closest to 0), ties -> higher CAGR, then see tie rule"
    ),
    "C": (
        f"design only; signal definitions with >= {MIN_DAYS_C} elevated and >= {MIN_DAYS_C} "
        "normal days; maximise P(dd>=10% in 60d | elevated) - P(... | normal); ties: see tie rule"
    ),
}


@cache
def _params() -> dict[str, tuple]:
    """variant id / signal id -> simplicity key (smaller = simpler = closer to the baseline)."""
    out: dict[str, tuple] = {}
    for v in variants.ftd_variants():
        out[v["id"]] = (v["correction_pct"], v["min_gain"], int(v["require_vol_above_avg"]))
    for v in variants.dd_variants():
        sig = (
            abs(v["min_decline_pct"]),
            variants.VOLUME_RULES.index(v["volume_rule"]),
            variants.RISK_RULES.index(v["risk_rule"]),
        )
        out.setdefault(v["signal_id"], sig)
        out[v["id"]] = (*sig, variants.EXPOSURES.index(v["exposure"]))
    return out


def simplicity(variant_id: str) -> tuple:
    """Parameter key used only for ties; unknown ids sort as equal (then by id)."""
    return _params().get(variant_id, ())


def _check_design(rows: Sequence[dict]) -> None:
    bad = [r for r in rows if r.get("period") != "design"]
    if bad:
        raise ValueError(
            f"selection accepts design-period rows only; got period={bad[0].get('period')!r}"
        )


TIE_NEW = "rule"  # the documented tie rule (TIE_RULE)
TIE_OLD = "string_order"  # what the code did before the amendment: the variant id as a string


def _select(rows, key_id, qualifies, sort_key, baseline_id, evaluation, tie=TIE_NEW):
    """`sort_key(r)` returns (primary, observations): the pre-registered ordering and the
    "more observations" part that only the amended tie rule uses. `tie` TIE_OLD reproduces the
    selection as it was before the amendment (primary key, then the id as a string)."""
    _check_design(rows)
    if tie not in (TIE_NEW, TIE_OLD):
        raise ValueError(f"unknown tie rule {tie!r}")
    indices = sorted({r["index"] for r in rows})
    out: dict[str, dict] = {}

    def order(r):
        primary, obs = sort_key(r)
        if tie == TIE_OLD:
            return (*primary, r[key_id])
        return (*primary, *obs, simplicity(r[key_id]), r[key_id])

    for idx in indices:
        mine = [r for r in rows if r["index"] == idx]
        cands = sorted((r for r in mine if qualifies(r)), key=order)
        if cands:
            out[idx] = {
                "selected": cands[0][key_id],
                "fallback_to_baseline": False,
                "n_considered": len(mine),
                "n_qualifying": len(cands),
            }
        else:
            out[idx] = {
                "selected": baseline_id,
                "fallback_to_baseline": True,
                "n_considered": len(mine),
                "n_qualifying": 0,
            }
        out[idx]["criterion"] = CRITERIA[evaluation]
    return out


def select_a(rows: Sequence[dict], baseline_id: str, tie: str = TIE_NEW) -> dict[str, dict]:
    return _select(
        rows,
        "variant_id",
        lambda r: r["n_trades"] >= MIN_TRADES_A and r["median"] is not None,
        lambda r: ((-r["median"],), (-r["n_trades"],)),
        baseline_id,
        "A",
        tie,
    )


def select_b(rows: Sequence[dict], baseline_id: str, tie: str = TIE_NEW) -> dict[str, dict]:
    return _select(
        rows,
        "variant_id",
        lambda r: r["cagr"] is not None and r["cagr"] >= r["bh_cagr"] - CAGR_TOLERANCE_B,
        lambda r: ((-r["max_dd"], -r["cagr"]), (-(r.get("n_days") or 0),)),
        baseline_id,
        "B",
        tie,
    )


def select_c(rows: Sequence[dict], baseline_signal_id: str, tie: str = TIE_NEW) -> dict[str, dict]:
    return _select(
        rows,
        "signal_id",
        lambda r: (
            r["n_elevated"] >= MIN_DAYS_C
            and r["n_normal"] >= MIN_DAYS_C
            and r["spread"] is not None
        ),
        lambda r: ((-r["spread"],), (-(r["n_elevated"] + r["n_normal"]),)),
        baseline_signal_id,
        "C",
        tie,
    )


def affected_picks(new: dict[str, dict[str, dict]], old: dict[str, dict[str, dict]]) -> list[dict]:
    """Picks that the amended tie rule changed: [{evaluation, index, before, after}].
    `new` / `old` map evaluation -> index -> selector output."""
    out = []
    for ev in sorted(new):
        for idx in sorted(new[ev]):
            before, after = old[ev][idx]["selected"], new[ev][idx]["selected"]
            if before != after:
                out.append({"evaluation": ev, "index": idx, "before": before, "after": after})
    return out
