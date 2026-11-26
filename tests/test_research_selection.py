"""Selection criteria and design/test split enforcement. No network."""

from __future__ import annotations

import copy
import random
from datetime import date, timedelta

import pytest

from research import pipeline, selection, split, variants


def _a(vid, idx, n, med, period="design"):
    return {"variant_id": vid, "index": idx, "n_trades": n, "median": med, "period": period}


def test_split_constants_are_hard_coded():
    assert split.DESIGN_START == "2000-01-03"
    assert split.DESIGN_END == "2014-12-31"
    assert split.TEST_START == "2015-01-02"


def test_selectors_reject_non_design_rows():
    with pytest.raises(ValueError):
        selection.select_a([_a("x", "I", 20, 0.1, period="test")], "base")
    with pytest.raises(ValueError):
        selection.select_b([{"period": "test", "index": "I"}], "base")
    with pytest.raises(ValueError):
        selection.select_c([{"period": "other", "index": "I"}], "base")
    with pytest.raises(ValueError):  # a mixture is rejected too
        selection.select_a([_a("x", "I", 20, 0.1), _a("y", "I", 20, 0.9, "test")], "base")


def test_select_a_min_n_best_median_and_tiebreak():
    rows = [
        _a("base", "I", 40, 0.01),
        _a("small", "I", 14, 0.50),  # excluded: n < 15
        _a("good", "I", 15, 0.03),
        _a("tie", "I", 30, 0.03),
        _a("other_idx", "J", 40, 0.02),
    ]
    out = selection.select_a(rows, "base")
    assert out["I"]["selected"] == "tie"  # tie on median -> more trades first (30 > 15)
    assert out["I"]["n_qualifying"] == 3 and not out["I"]["fallback_to_baseline"]
    assert out["J"]["selected"] == "other_idx"


def test_select_a_fallback_to_baseline():
    out = selection.select_a([_a("v", "I", 3, 0.5)], "base")
    assert out["I"] == {
        "selected": "base",
        "fallback_to_baseline": True,
        "n_considered": 1,
        "n_qualifying": 0,
        "criterion": selection.CRITERIA["A"],
    }


def _b(vid, cagr, mdd, bh=0.08):
    return {
        "variant_id": vid,
        "index": "I",
        "period": "design",
        "cagr": cagr,
        "max_dd": mdd,
        "bh_cagr": bh,
    }


def test_select_b_cagr_tolerance_then_smallest_drawdown():
    rows = [
        _b("hold", 0.08, -0.55),
        _b("cash", 0.01, -0.05),  # best drawdown but CAGR < 8% - 2pp -> excluded
        _b("edge", 0.06, -0.30),  # exactly bh - 2pp -> allowed (tolerance floating point safe?)
        _b("mid", 0.07, -0.30),  # same drawdown, higher CAGR wins the tie
    ]
    out = selection.select_b(rows, "hold")
    assert out["I"]["selected"] == "mid"


def test_select_c_spread_and_minimum_days():
    def c(sid, ne, nn, spread):
        return {
            "signal_id": sid,
            "index": "I",
            "period": "design",
            "n_elevated": ne,
            "n_normal": nn,
            "spread": spread,
        }

    rows = [c("base", 400, 3000, 0.05), c("thin", 50, 3000, 0.40), c("best", 250, 3000, 0.09)]
    assert selection.select_c(rows, "base")["I"]["selected"] == "best"
    assert selection.select_c([c("thin", 5, 3000, 0.4)], "base")["I"]["fallback_to_baseline"]


# ---------------------------------------------------------------- pipeline split invariance


def _synthetic(seed, n=900):
    rnd = random.Random(seed)
    d = date(2010, 1, 4)
    price, bars = 1000.0, []
    while len(bars) < n:
        if d.weekday() < 5:
            ret = rnd.gauss(0.0004, 0.011)
            o = price
            price *= 1 + ret
            hi = max(o, price) * (1 + abs(rnd.gauss(0, 0.003)))
            lo = min(o, price) * (1 - abs(rnd.gauss(0, 0.003)))
            bars.append(
                {
                    "date": d.isoformat(),
                    "open": o,
                    "high": hi,
                    "low": lo,
                    "close": price,
                    "volume": int(1e9 * (1 + rnd.random())),
                }
            )
        d += timedelta(days=1)
    return bars


def _subset():
    f = [v for v in variants.ftd_variants() if v["baseline"] or v["id"] == "ftd_c5_g1.5_v1"]
    d = [
        v for v in variants.dd_variants() if v["baseline"] or v["id"] == "dd_d-0.5_avg50_pctl10_e0"
    ]
    return f, d


def test_pipeline_design_results_ignore_test_period_bars():
    h = {"^GSPC": _synthetic(1), "^IXIC": _synthetic(2)}
    design_end = h["^GSPC"][600]["date"]
    test_start = h["^GSPC"][601]["date"]
    start = h["^GSPC"][5]["date"]
    f, d = _subset()
    kw = dict(
        ftd_vars=f, dd_vars=d, design_start=start, design_end=design_end, test_start=test_start
    )
    a = pipeline.run_pipeline(h, **kw)

    alt = copy.deepcopy(h)
    for sym in alt:
        for k, b in enumerate(alt[sym]):
            if k > 600:
                f_ = 0.5 if k % 2 else 1.7
                for key in ("open", "high", "low", "close"):
                    b[key] *= f_
                b["volume"] = b["volume"] * 3
    b_ = pipeline.run_pipeline(alt, **kw)

    assert a["design"] == b_["design"]
    assert a["chosen"] == b_["chosen"]
    assert a["design_of_compared"] == b_["design_of_compared"]
    assert a["test"]["B"] != b_["test"]["B"]  # the test period does see its own bars
    for ev in "ABC":
        assert all(r["period"] == "test" for r in a["test"][ev])
        assert all(r["period"] == "design" for r in a["design"][ev])
    # test rows only for baseline + selected
    ids_b = {r["variant_id"] for r in a["test"]["B"]}
    assert ids_b <= {a["baseline"]["B"]} | {c["selected"] for c in a["chosen"]["B"].values()}


def test_select_b_ignores_exposure_matched_columns():
    rows = [_b("hold", 0.08, -0.55), _b("mid", 0.07, -0.30), _b("low", 0.06, -0.40)]
    base = selection.select_b(rows, "hold")
    for r in rows:
        r["em_cagr"], r["em_max_dd"] = 9.9, -0.0001  # absurdly good benchmark must not matter
    assert selection.select_b(rows, "hold") == base
