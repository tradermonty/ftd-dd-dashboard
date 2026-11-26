"""Distribution-Day signal variants (spec §3.6), point-in-time.

Detection variants (decline threshold x volume rule) are detected here, per session of the
full chronological series; for every session t the records inside the production 85-bar
window are rebuilt as vendored `DDRecord`s and handed to the VENDORED `enrich_records` and
`count_active_in_window`. With the baseline configuration (-0.2%, prev_day) the records are
identical to those of the vendored `detect_distribution_days` (tested).

Volume rules (a DD at position j needs a valid close/volume pair at j and j-1 as in vendor):
  prev_day: volume[j] > volume[j-1]                 (vendor rule)
  avg50:    volume[j] > mean(positive volume[j-50..j-1]); not flagged with < 50 prior bars
  both:     both conditions

Risk rules:
  skill_high / skill_severe: vendored classify_risk with the skill thresholds (incl. MA
      filter); trigger = level >= HIGH / level == SEVERE.
  pctl20 / pctl10 / pctl5: elevated at t when at most 20% / 10% / 5% of the d25 values of
      the trailing 1260 sessions strictly BEFORE t are >= d25[t]; needs >= 252 prior values.
"""

from __future__ import annotations

from dataclasses import dataclass

from engine import dd_glue, loader, replay
from research import variants

EPSILON = 1e-12  # same constant as the vendored tracker
RISK_ORDER = ("NORMAL", "CAUTION", "HIGH", "SEVERE")


@dataclass
class DDSeries:
    dates: list[str]
    d5: list[int]
    d15: list[int]
    d25: list[int]
    is_dd: list[bool]
    level: list[str]


def _positive(v) -> bool:
    return v is not None and v > 0


def detection_protos(
    chrono: list[dict], min_decline_pct: float, volume_rule: str
) -> list[dict | None]:
    """Per position j: record fields of a DD ending at j, or None. Needs j >= 1."""
    if volume_rule not in variants.VOLUME_RULES:
        raise ValueError(f"unknown volume rule {volume_rule!r}")
    n = len(chrono)
    protos: list[dict | None] = [None] * n
    for j in range(1, n):
        today, yest = chrono[j], chrono[j - 1]
        if not (_positive(today["close"]) and _positive(yest["close"])):
            continue
        if not (_positive(today["volume"]) and _positive(yest["volume"])):
            continue
        pct = today["close"] / yest["close"] - 1
        if not pct <= min_decline_pct + EPSILON:
            continue
        vol_prev = today["volume"] > yest["volume"]
        vol_avg = False
        if volume_rule != "prev_day" and j >= 50:
            prior = [b["volume"] for b in chrono[j - 50 : j] if _positive(b["volume"])]
            vol_avg = bool(prior) and today["volume"] > sum(prior) / len(prior)
        ok = {"prev_day": vol_prev, "avg50": vol_avg, "both": vol_prev and vol_avg}[volume_rule]
        if ok:
            protos[j] = {
                "date": today["date"],
                "close": today["close"],
                "pct_change": pct,
                "volume": today["volume"],
                "prev_volume": yest["volume"],
                "volume_change_pct": today["volume"] / yest["volume"] - 1,
            }
    return protos


def records_at(protos: list[dict | None], t: int):
    """Fresh DDRecords for the 85-bar window ending at t (newest first, like production)."""
    models = loader.load_dd_modules().models
    lo = max(0, t - replay.DD_WINDOW + 1)
    out = []
    for j in range(t, lo, -1):  # j = lo is never "today" (no yesterday in window)
        p = protos[j]
        if p is None:
            continue
        age = t - j
        out.append(models.DDRecord(dd_index=age, age_sessions=age, **p))
    return out


def market_below_ma_series(chrono: list[dict]) -> list[bool | None]:
    """Vendored-equivalent MA filter per day on the production 85-bar window."""
    out = []
    for t in range(len(chrono)):
        lo = max(0, t - replay.DD_WINDOW + 1)
        out.append(dd_glue.compute_market_below_ma(chrono[lo : t + 1][::-1]))
    return out


def dd_series(
    chrono: list[dict],
    min_decline_pct: float,
    volume_rule: str,
    below: list[bool | None] | None = None,
) -> DDSeries:
    dd = loader.load_dd_modules()
    rule = dd_glue.build_rule({**dd_glue.DEFAULT_RULE_CONFIG, "min_decline_pct": min_decline_pct})
    thresholds = dd_glue.build_thresholds()
    protos = detection_protos(chrono, min_decline_pct, volume_rule)
    if below is None:
        below = market_below_ma_series(chrono)
    s = DDSeries([], [], [], [], [], [])
    for t in range(len(chrono)):
        lo = max(0, t - replay.DD_WINDOW + 1)
        window = chrono[lo : t + 1][::-1]
        recs = dd.tracker.enrich_records(records_at(protos, t), window, rule)
        d5 = dd.tracker.count_active_in_window(recs, 5)
        d15 = dd.tracker.count_active_in_window(recs, 15)
        d25 = dd.tracker.count_active_in_window(recs, 25)
        s.dates.append(chrono[t]["date"])
        s.d5.append(d5)
        s.d15.append(d15)
        s.d25.append(d25)
        s.is_dd.append(any(r.dd_index == 0 and r.status == "active" for r in recs))
        s.level.append(dd.risk_classifier.classify_risk(d5, d15, d25, below[t], thresholds))
    return s


def percentile_elevated(
    d25: list[int],
    tail: float,
    lookback: int = variants.PCTL_LOOKBACK,
    min_obs: int = variants.PCTL_MIN_OBS,
) -> list[bool]:
    """Elevated at t iff P(d25 >= d25[t]) over d25[t-lookback : t] (t excluded) <= tail."""
    out: list[bool] = []
    size = (max(d25) if d25 else 0) + 2
    hist = [0] * size
    for t, v in enumerate(d25):
        if t - lookback - 1 >= 0:  # drop the value that leaves the window
            hist[d25[t - lookback - 1]] -= 1
        n_prior = min(t, lookback)
        if n_prior >= min_obs:
            ge = sum(hist[v:])
            out.append(ge / n_prior <= tail + EPSILON)
        else:
            out.append(False)
        hist[v] += 1
    return out


def elevated_flags(series: DDSeries, risk_rule: str) -> list[bool]:
    if risk_rule == "skill_high":
        return [RISK_ORDER.index(lv) >= RISK_ORDER.index("HIGH") for lv in series.level]
    if risk_rule == "skill_severe":
        return [lv == "SEVERE" for lv in series.level]
    if risk_rule in variants.PCTL_TAIL:
        return percentile_elevated(series.d25, variants.PCTL_TAIL[risk_rule])
    raise ValueError(f"unknown risk rule {risk_rule!r}")
