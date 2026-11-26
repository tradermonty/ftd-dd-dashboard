"""Orchestration of phase R: signals (lazy, cached) -> design metrics -> selection -> test.

The test period is evaluated exactly once, for the baseline and the selected variant of each
evaluation and index, after selection has been made from design rows alone.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from engine import replay
from research import dd_signals, evaluate, ftd_signals, overlay, selection, variants
from research.split import DESIGN_END, DESIGN_START, TEST_END, TEST_START, day_after

INDICES = (replay.SP500, replay.NASDAQ)
Pair = tuple[str, dict]  # (index, variant)


class Signals:
    """Lazy, cached point-in-time signals over the full history (computed once)."""

    def __init__(self, histories: dict[str, list[dict]], start: str = DESIGN_START, log=None):
        self.histories = histories
        self.start = start
        self._log = log or (lambda msg: None)
        self._ftd: dict[tuple[float, float], list[dict]] = {}
        self._below: dict[str, list] = {}
        self._series: dict[tuple, dd_signals.DDSeries] = {}
        self._flags: dict[tuple, list[bool]] = {}

    def ftd_events(self, correction_pct: float, min_gain: float) -> list[dict]:
        key = (correction_pct, min_gain)
        if key not in self._ftd:
            t0 = time.time()
            self._ftd[key] = ftd_signals.ftd_event_table(
                self.histories, correction_pct, min_gain, self.start
            )
            self._log(
                f"  FTD replay depth={correction_pct:g} gain={min_gain:g} {time.time() - t0:.1f}s"
            )
        return self._ftd[key]

    def dd_series(self, index: str, decl: float, vol: str) -> dd_signals.DDSeries:
        key = (index, decl, vol)
        if key not in self._series:
            if index not in self._below:
                self._below[index] = dd_signals.market_below_ma_series(self.histories[index])
            t0 = time.time()
            self._series[key] = dd_signals.dd_series(
                self.histories[index], decl, vol, self._below[index]
            )
            self._log(f"  DD series {index} decline={decl:g} vol={vol} {time.time() - t0:.1f}s")
        return self._series[key]

    def flags(self, index: str, decl: float, vol: str, risk: str) -> list[bool]:
        key = (index, decl, vol, risk)
        if key not in self._flags:
            self._flags[key] = dd_signals.elevated_flags(self.dd_series(index, decl, vol), risk)
        return self._flags[key]


# ------------------------------------------------------------------ evaluations


def eval_a(sig: Signals, pairs: list[Pair], start: str, end: str, period: str) -> list[dict]:
    rows = []
    for index, v in pairs:
        evs = ftd_signals.apply_volume_filter(
            sig.ftd_events(v["correction_pct"], v["min_gain"]), v["require_vol_above_avg"]
        )
        evs = [e for e in evs if e["index"] == index]
        trades, meta = evaluate.trade_returns(evs, sig.histories[index], start, end)
        rows.append(
            {"variant_id": v["id"], "index": index, "period": period}
            | meta
            | evaluate.trade_stats(trades)
        )
    return rows


def eval_b(sig: Signals, pairs: list[Pair], start: str, end: str, period: str) -> list[dict]:
    rows = []
    for index, v in pairs:
        bars = sig.histories[index]
        flags = sig.flags(index, v["min_decline_pct"], v["volume_rule"], v["risk_rule"])
        m = evaluate.exposure_backtest(
            [b["date"] for b in bars], [b["close"] for b in bars], flags, v["exposure"], start, end
        )
        rows.append({"variant_id": v["id"], "index": index, "period": period} | m)
    return rows


def eval_c(sig: Signals, pairs: list[Pair], start: str, end: str, period: str) -> list[dict]:
    rows = []
    for index, v in pairs:
        bars = sig.histories[index]
        dates, closes = [b["date"] for b in bars], [b["close"] for b in bars]
        flags = sig.flags(index, v["min_decline_pct"], v["volume_rule"], v["risk_rule"])
        labels = ["elevated" if f else "normal" for f in flags]
        full = evaluate.drawdown_prob(dates, closes, labels, start, end)
        res = full["by_label"]
        el, no = res.get("elevated", {}), res.get("normal", {})
        row = {
            "signal_id": v["signal_id"],
            "index": index,
            "period": period,
            "n_elevated": el.get("n", 0),
            "p_elevated": el.get("p"),
            "n_normal": no.get("n", 0),
            "p_normal": no.get("p"),
        }
        row["spread"] = (
            row["p_elevated"] - row["p_normal"]
            if row["p_elevated"] is not None and row["p_normal"] is not None
            else None
        )
        row["n_dropped_incomplete_window"] = full["n_dropped_incomplete_window"]
        if v["risk_rule"].startswith("skill"):
            series = sig.dd_series(index, v["min_decline_pct"], v["volume_rule"])
            lv = evaluate.drawdown_prob(dates, closes, series.level, start, end)["by_label"]
            for name in dd_signals.RISK_ORDER:
                row[f"n_{name}"] = lv.get(name, {}).get("n", 0)
                row[f"p_{name}"] = lv.get(name, {}).get("p")
        rows.append(row)
    return rows


def _dedupe_signals(dd_vars: list[dict]) -> list[dict]:
    seen: dict[str, dict] = {}
    for v in dd_vars:
        seen.setdefault(v["signal_id"], v)
    return list(seen.values())


# ------------------------------------------------------------------ run


def run_pipeline(
    histories: dict[str, list[dict]],
    ftd_vars: list[dict] | None = None,
    dd_vars: list[dict] | None = None,
    design_start: str = DESIGN_START,
    design_end: str = DESIGN_END,
    test_start: str = TEST_START,
    log: Callable[[str], None] | None = None,
    test_end: str | None = None,
) -> dict:
    """`test_end`: the frozen end of the test period (research.split.TEST_END). Sessions after
    it never enter the test period; they are evaluated in the forward record only. When the
    data ends earlier, the test period ends with the data (small synthetic histories)."""
    ftd_vars = ftd_vars if ftd_vars is not None else variants.ftd_variants()
    dd_vars = dd_vars if dd_vars is not None else variants.dd_variants()
    last = histories[INDICES[0]][-1]["date"]
    test_end = min(test_end or TEST_END, last)
    sig = Signals(histories, start=design_start, log=log)
    sig_defs = _dedupe_signals(dd_vars)
    base = _baselines(ftd_vars, dd_vars, sig_defs)

    # ---- design period: all variants
    design = {
        "A": eval_a(
            sig, [(i, v) for v in ftd_vars for i in INDICES], design_start, design_end, "design"
        ),
        "B": eval_b(
            sig, [(i, v) for v in dd_vars for i in INDICES], design_start, design_end, "design"
        ),
        "C": eval_c(
            sig, [(i, v) for v in sig_defs for i in INDICES], design_start, design_end, "design"
        ),
    }
    # ---- selection from design rows only
    chosen = {
        "A": selection.select_a(design["A"], base["A"]["id"]),
        "B": selection.select_b(design["B"], base["B"]["id"]),
        "C": selection.select_c(design["C"], base["C"]["signal_id"]),
    }
    # ---- test period, once, for baseline and selected only
    lookup = {
        "A": {v["id"]: v for v in ftd_vars},
        "B": {v["id"]: v for v in dd_vars},
        "C": {v["signal_id"]: v for v in sig_defs},
    }
    base_key = {"A": base["A"]["id"], "B": base["B"]["id"], "C": base["C"]["signal_id"]}
    fns = {"A": eval_a, "B": eval_b, "C": eval_c}
    test: dict[str, list[dict]] = {}
    selected_design: dict[str, list[dict]] = {}
    for ev in ("A", "B", "C"):
        pairs: list[Pair] = []
        for index in INDICES:
            ids = {base_key[ev], chosen[ev][index]["selected"]}
            pairs += [(index, lookup[ev][i]) for i in sorted(ids)]
        test[ev] = fns[ev](sig, pairs, test_start, test_end, "test")
        selected_design[ev] = fns[ev](sig, pairs, design_start, design_end, "design")
    # ---- tie-break amendment (post hoc): which picks did the amended tie rule change? The
    # pick the old string-order rule would have made is scored on both periods ONLY to show
    # what the change did; it is never used to choose anything.
    old_chosen = {
        "A": selection.select_a(design["A"], base["A"]["id"], tie=selection.TIE_OLD),
        "B": selection.select_b(design["B"], base["B"]["id"], tie=selection.TIE_OLD),
        "C": selection.select_c(design["C"], base["C"]["signal_id"], tie=selection.TIE_OLD),
    }
    affected = selection.affected_picks(chosen, old_chosen)
    tie_rows: list[dict] = []
    for a in affected:
        ev, index = a["evaluation"], a["index"]
        pair = [(index, lookup[ev][a["before"]])]
        tie_rows.append(
            {
                **a,
                "design": fns[ev](sig, pair, design_start, design_end, "design")[0],
                "test": fns[ev](sig, pair, test_start, test_end, "test")[0],
            }
        )
    forward = forward_record(sig, chosen, base_key, lookup, test_end, last)
    return {
        "design": design,
        "chosen": chosen,
        "baseline": base_key,
        "test": test,
        "design_of_compared": selected_design,
        "forward": forward,
        "test_end": test_end,
        "last_date": last,
        "signals": sig,
        "old_chosen": old_chosen,
        "tie_affected": affected,
        "tie_rows": tie_rows,
    }


def forward_record(
    sig: Signals, chosen: dict, base_key: dict, lookup: dict, after: str, last: str
) -> dict:
    """The forward record of research R: the baseline and the picks (A, B, C) over the sessions
    after the frozen test end, computed with the same functions as the test period.

    {"after", "last", "n_sessions": {index: sessions after `after`}, "A"|"B"|"C": {index:
    {role: row}}}. A and C use the evaluation functions on [day after `after`, last]; B uses the
    return days whose previous close is on or after `after` (research.overlay.forward_record,
    the same return code as R2). With no new session every row is empty and the pages say so."""
    start = day_after(after)
    out: dict = {
        "after": after,
        "last": last,
        "n_sessions": {i: sum(1 for b in sig.histories[i] if b["date"] > after) for i in INDICES},
        "A": {},
        "B": {},
        "C": {},
    }
    for ev in ("A", "B", "C"):
        for index in INDICES:
            roles = {"baseline": base_key[ev], "selected": chosen[ev][index]["selected"]}
            if roles["baseline"] == roles["selected"]:
                roles = {"baseline": base_key[ev]}
            rows = {}
            for role, vid in roles.items():
                v = lookup[ev][vid]
                if ev == "A":
                    rows[role] = {"variant_id": vid} | eval_a(
                        sig, [(index, v)], start, last, "forward"
                    )[0]
                elif ev == "C":
                    rows[role] = {"variant_id": vid} | eval_c(
                        sig, [(index, v)], start, last, "forward"
                    )[0]
                else:
                    bars = sig.histories[index]
                    flags = sig.flags(index, v["min_decline_pct"], v["volume_rule"], v["risk_rule"])
                    exp = [v["exposure"] if f else 1.0 for f in flags]
                    rec = overlay.forward_record(
                        [b["date"] for b in bars],
                        [b["close"] for b in bars],
                        exp,
                        [0.0] * len(bars),
                        after,
                        last,
                    )
                    rec.pop("levered_days")  # R2 field: this evaluation never borrows
                    rows[role] = {"variant_id": vid} | rec
            out[ev][index] = rows
    return out


def _baselines(ftd_vars, dd_vars, sig_defs) -> dict:
    a = next(v for v in ftd_vars if v["baseline"])
    b = next(v for v in dd_vars if v["baseline"])
    c = next(v for v in sig_defs if v["signal_id"] == b["signal_id"])
    return {"A": a, "B": b, "C": c}
