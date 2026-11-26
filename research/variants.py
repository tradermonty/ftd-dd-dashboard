"""Variant definitions as data (spec §3.6). The skill definition is the baseline.

FTD variants: correction depth x min gain x "require volume above 50d average".
DD variants:  decline threshold x volume rule x risk rule x exposure (evaluation B).

Spec ambiguity resolved here: "risk rule = skill thresholds" gives four levels, while
evaluation B needs a binary "risk at or above the reference" trigger. The skill rule is
therefore expanded into two triggers (>= HIGH, and SEVERE only). Exposure when triggered
(50% or 0%) is an evaluation-B parameter and is counted as a variant dimension.
"""

from __future__ import annotations

CORRECTION_PCTS = (3.0, 5.0, 8.0, 10.0)
MIN_GAINS = (1.25, 1.5, 2.0)
VOL_FILTERS = (False, True)

DECLINES = (-0.002, -0.005, -0.01)
VOLUME_RULES = ("prev_day", "avg50", "both")
RISK_RULES = ("skill_high", "skill_severe", "pctl20", "pctl10", "pctl5")
EXPOSURES = (0.5, 0.0)

PCTL_TAIL = {"pctl20": 0.20, "pctl10": 0.10, "pctl5": 0.05}
PCTL_LOOKBACK = 1260  # ~5 trading years
PCTL_MIN_OBS = 252  # no signal until a year of prior d25 values exists

BASELINE_FTD = {"correction_pct": 3.0, "min_gain": 1.25, "require_vol_above_avg": False}
BASELINE_DD = {
    "min_decline_pct": -0.002,
    "volume_rule": "prev_day",
    "risk_rule": "skill_high",
    "exposure": 0.5,
}


def ftd_variant_id(correction_pct: float, min_gain: float, require_vol: bool) -> str:
    return f"ftd_c{correction_pct:g}_g{min_gain:g}_v{int(require_vol)}"


def dd_signal_id(min_decline_pct: float, volume_rule: str, risk_rule: str) -> str:
    return f"dd_d{round(min_decline_pct * 100, 6):g}_{volume_rule}_{risk_rule}"


def dd_variant_id(min_decline_pct: float, volume_rule: str, risk_rule: str, exposure: float) -> str:
    return f"{dd_signal_id(min_decline_pct, volume_rule, risk_rule)}_e{round(exposure * 100)}"


def ftd_variants() -> list[dict]:
    out = []
    for c in CORRECTION_PCTS:
        for g in MIN_GAINS:
            for v in VOL_FILTERS:
                out.append(
                    {
                        "id": ftd_variant_id(c, g, v),
                        "family": "ftd",
                        "correction_pct": c,
                        "min_gain": g,
                        "require_vol_above_avg": v,
                        "baseline": (c, g, v) == tuple(BASELINE_FTD.values()),
                    }
                )
    return out


def dd_variants() -> list[dict]:
    out = []
    for d in DECLINES:
        for vr in VOLUME_RULES:
            for rr in RISK_RULES:
                for e in EXPOSURES:
                    out.append(
                        {
                            "id": dd_variant_id(d, vr, rr, e),
                            "signal_id": dd_signal_id(d, vr, rr),
                            "family": "dd",
                            "min_decline_pct": d,
                            "volume_rule": vr,
                            "risk_rule": rr,
                            "exposure": e,
                            "baseline": (d, vr, rr, e) == tuple(BASELINE_DD.values()),
                        }
                    )
    return out


def all_variants() -> list[dict]:
    return ftd_variants() + dd_variants()


def baseline_ids() -> dict[str, str]:
    """Baseline variant id per family."""
    return {v["family"]: v["id"] for v in all_variants() if v["baseline"]}


def grid_definition() -> dict:
    """The pre-registered grid (hashed by research.freeze). Variant ids pin the baselines."""
    return {
        "correction_pcts": list(CORRECTION_PCTS),
        "min_gains": list(MIN_GAINS),
        "vol_filters": list(VOL_FILTERS),
        "declines": list(DECLINES),
        "volume_rules": list(VOLUME_RULES),
        "risk_rules": list(RISK_RULES),
        "exposures": list(EXPOSURES),
        "pctl_tail": dict(PCTL_TAIL),
        "pctl_lookback": PCTL_LOOKBACK,
        "pctl_min_obs": PCTL_MIN_OBS,
        "baseline_ftd": dict(BASELINE_FTD),
        "baseline_dd": dict(BASELINE_DD),
        "variant_ids": [v["id"] for v in all_variants()],
    }


def count_summary() -> dict:
    f, d = ftd_variants(), dd_variants()
    return {
        "ftd_variants": len(f),
        "dd_variants": len(d),
        "total_variants": len(f) + len(d),
        "distinct_ftd_detector_configs": len(CORRECTION_PCTS) * len(MIN_GAINS),
        "distinct_dd_signal_definitions": len({v["signal_id"] for v in d}),
    }
