"""Variant definitions (data) and the loader's FTD_GAIN_MINIMUM override. No network."""

from __future__ import annotations

import pytest

from engine import loader
from research import variants


def test_counts_and_baselines():
    f, d = variants.ftd_variants(), variants.dd_variants()
    assert len(f) == 4 * 3 * 2 == 24
    assert len(d) == 3 * 3 * 5 * 2 == 90
    assert variants.count_summary()["total_variants"] == 114
    assert variants.count_summary()["distinct_dd_signal_definitions"] == 45
    assert len({v["id"] for v in f + d}) == 114  # unique ids
    assert sum(v["baseline"] for v in f) == 1 and sum(v["baseline"] for v in d) == 1
    b_f = next(v for v in f if v["baseline"])
    assert (b_f["correction_pct"], b_f["min_gain"], b_f["require_vol_above_avg"]) == (
        3.0,
        1.25,
        False,
    )
    b_d = next(v for v in d if v["baseline"])
    assert (b_d["min_decline_pct"], b_d["volume_rule"], b_d["risk_rule"]) == (
        -0.002,
        "prev_day",
        "skill_high",
    )


def test_ids_are_stable_and_readable():
    assert variants.dd_signal_id(-0.005, "avg50", "pctl10") == "dd_d-0.5_avg50_pctl10"
    assert variants.dd_variant_id(-0.01, "both", "pctl5", 0.0) == "dd_d-1_both_pctl5_e0"
    assert variants.ftd_variant_id(8.0, 1.5, True) == "ftd_c8_g1.5_v1"


def test_gain_override_is_independent():
    default = loader.load_rally_tracker()
    over = loader.load_rally_tracker(ftd_gain_minimum=2.0)
    both = loader.load_rally_tracker(min_correction_pct=8, ftd_gain_minimum=1.5)
    assert over is not default and both is not over
    assert (default.FTD_GAIN_MINIMUM, default.MIN_CORRECTION_PCT) == (1.25, 3.0)
    assert (over.FTD_GAIN_MINIMUM, over.MIN_CORRECTION_PCT) == (2.0, 3.0)
    assert (both.FTD_GAIN_MINIMUM, both.MIN_CORRECTION_PCT) == (1.5, 8.0)
    assert loader.load_ftd_modules().rally_tracker.FTD_GAIN_MINIMUM == 1.25
    assert loader.load_rally_tracker(min_correction_pct=5).FTD_GAIN_MINIMUM == 1.25


@pytest.mark.parametrize("bad", [0, -1.0, True, "1.5"])
def test_gain_override_validates(bad):
    with pytest.raises((TypeError, ValueError)):
        loader.load_rally_tracker(ftd_gain_minimum=bad)
