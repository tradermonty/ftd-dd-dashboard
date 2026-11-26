"""Test-period contamination of research R (the third exception).

Before R was designed, the "background" paragraph of docs/plan.md section 3.6 (2026-11-07) was
written from statistics computed on the CSV files of build/ over the WHOLE of 2000 to 2026,
which includes the test period 2015 to 2026. Those figures (the share of DD days, the 60-session
drawdown frequency by risk level, the FTD forward returns) motivated what R tests. R's
test-period results are therefore not a clean out-of-sample check, in the same way as R2's
(research.overlay.TEST_PERIOD_CONTAMINATION is the second exception).

The figures below are the ones the plan states. Nothing in the run is built from them;
`verify` recomputes them from the run's own data/daily_status.csv so that the record cannot
drift from the data (the publish gate calls it).
"""

from __future__ import annotations

import csv
from pathlib import Path

from research import evaluate

CONTAMINATION_R = {
    "date": "2026-11-07",
    "description": (
        "Before R was designed, docs/plan.md section 3.6 ('background', 2026-11-07) was written "
        "from statistics computed on the whole of 2000 to 2026, which includes the test period "
        "2015 to 2026: the share of days that were Distribution Days, the 60-session drawdown "
        "frequency by risk level, and the 60-session returns after an FTD."
    ),
    "period": ["2000-01-03", "2026-11-06"],
    "figures": {
        "dd_day_share": {"^GSPC": 0.198, "^IXIC": 0.205},
        "d25_at_or_above_severe_share": "about three tenths of the days",
        "p_drawdown_60d": {
            "^GSPC": {"NORMAL": 0.120, "SEVERE": 0.197},
            "^IXIC": {"NORMAL": 0.264, "SEVERE": 0.235},
        },
        "ftd_forward_60d": (
            "the median after an FTD was no different from the median over all days, and the "
            "spread was wide"
        ),
    },
    "consequence": (
        "what R tests was decided after these full-period statistics, which include the test "
        "period, had been seen, so the test period was not untouched when R was designed"
    ),
    "clean_out_of_sample": False,
}
# The risk levels compared in the record; the d25 share is only checked to a coarse bound.
TOLERANCE = 0.0006  # the figures are recorded to 0.1 percentage point
SEVERE_D25 = 6


def recompute(daily_status: Path) -> dict:
    """The recorded figures, computed from a run's daily_status.csv over the recorded period.

    Sessions after the period end are left out: the figures describe what was seen on
    2026-11-07, and every new daily session would otherwise move them and fail the gate."""
    start, end = CONTAMINATION_R["period"]
    with Path(daily_status).open(newline="", encoding="utf-8") as f:
        rows = [x for x in csv.DictReader(f) if start <= x["date"] <= end]
    out: dict = {"dd_day_share": {}, "d25_at_or_above_severe_share": {}, "p_drawdown_60d": {}}
    for sym in ("^GSPC", "^IXIC"):
        r = [x for x in rows if x["index"] == sym]
        dates, closes = [x["date"] for x in r], [float(x["close"]) for x in r]
        out["dd_day_share"][sym] = sum(1 for x in r if x["is_dd"] == "True") / len(r)
        out["d25_at_or_above_severe_share"][sym] = sum(
            1 for x in r if int(x["d25"]) >= SEVERE_D25
        ) / len(r)
        res = evaluate.drawdown_prob(
            dates, closes, [x["risk_level"] for x in r], dates[0], dates[-1]
        )["by_label"]
        out["p_drawdown_60d"][sym] = {k: res[k]["p"] for k in ("NORMAL", "SEVERE")}
    return out


def verify(daily_status: Path) -> list[str]:
    """Problems found when the recorded figures are compared with the data (empty = they agree)."""
    got = recompute(daily_status)
    want = CONTAMINATION_R["figures"]
    problems = []
    for sym, share in want["dd_day_share"].items():
        if abs(got["dd_day_share"][sym] - share) > TOLERANCE:
            problems.append(
                f"DD share {sym}: recorded {share}, data {got['dd_day_share'][sym]:.4f}"
            )
    for sym, levels in want["p_drawdown_60d"].items():
        for lv, p in levels.items():
            if abs(got["p_drawdown_60d"][sym][lv] - p) > TOLERANCE:
                problems.append(
                    f"P(drawdown) {sym} {lv}: recorded {p}, "
                    f"data {got['p_drawdown_60d'][sym][lv]:.4f}"
                )
    for sym, share in got["d25_at_or_above_severe_share"].items():
        if not 0.25 <= share <= 0.35:
            problems.append(f"d25>={SEVERE_D25} share {sym} {share:.3f} is not about three tenths")
    return problems
