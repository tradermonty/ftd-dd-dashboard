"""Counting glue replicated from ibd_monitor.analyze_index (not vendored).

Source (claude-trading-skills, pinned in vendor/VENDOR.lock):
    skills/ibd-distribution-day-monitor/scripts/ibd_monitor.py
      - _build_rule()                      -> build_rule()
      - _build_thresholds()                -> build_thresholds()
      - _compute_ma_filters() ~line 125    -> compute_market_below_ma()
      - analyze_index() lines ~229-246     -> analyze()

The detection / enrichment itself is the vendored tracker; only the way the production
script turns records into d5/d15/d25 and "today is a DD" is copied here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from engine import loader

# Values of skills/ibd-distribution-day-monitor/config/default.yaml::distribution_day_rule
DEFAULT_RULE_CONFIG = {
    "min_decline_pct": -0.002,
    "expiration_sessions": 25,
    "invalidation_gain_pct": 0.05,
    "invalidation_price_source": "high",
}


# Values of config/default.yaml::risk_thresholds (nested form, as ibd_monitor reads it)
DEFAULT_RISK_CONFIG = {
    "caution": {"d25_count": 3},
    "high": {"d25_count": 5, "d15_count": 3, "d5_count": 2},
    "severe": {"d25_count": 6, "d15_count": 4, "severe_ma_d25": 5},
}

# Values of config/default.yaml::moving_average_filters
DEFAULT_MA_CONFIG = {"enabled": True, "ema_periods": [21], "sma_periods": [50]}


@dataclass
class DDDay:
    d5: int
    d15: int
    d25: int
    today_is_dd: bool
    records: list[Any] = field(default_factory=list)
    skipped: list[dict] = field(default_factory=list)
    risk_level: str = "NORMAL"
    market_below_ma: bool | None = None


def build_thresholds(risk_cfg: dict | None = None):
    """Mirror of ibd_monitor._build_thresholds (same keys, same fallbacks)."""
    t_cfg = risk_cfg if risk_cfg is not None else DEFAULT_RISK_CONFIG
    high = t_cfg.get("high") or {}
    severe = t_cfg.get("severe") or {}
    models = loader.load_dd_modules().models
    return models.RiskThresholds(
        caution_d25=(t_cfg.get("caution") or {}).get("d25_count", 3),
        high_d25=high.get("d25_count", 5),
        high_d15=high.get("d15_count", 3),
        high_d5=high.get("d5_count", 2),
        severe_d25=severe.get("d25_count", 6),
        severe_d15=severe.get("d15_count", 4),
        severe_ma_d25=severe.get("severe_ma_d25", 5),
    )


def compute_market_below_ma(effective_history: list[dict], ma_cfg: dict | None = None):
    """Copy of the `market_below_21ema_or_50ma` logic of ibd_monitor._compute_ma_filters
    (ibd_monitor.py ~lines 125-165 at the pinned ref).

    `effective_history` is newest-first; the EMA/SMA are computed over exactly the bars
    supplied (EMA value depends on the number of bars). True only when the close is below
    BOTH the 21EMA and the 50SMA; None when either MA cannot be computed or the filter is
    disabled / history is empty.
    """
    ma_cfg = ma_cfg if ma_cfg is not None else DEFAULT_MA_CONFIG
    if not ma_cfg.get("enabled", True) or not effective_history:
        return None
    mu = loader.load_dd_modules().math_utils
    closes = [row["close"] for row in effective_history]
    today_close = closes[0]

    ema_period = (ma_cfg.get("ema_periods") or [21])[0]
    sma_period = (ma_cfg.get("sma_periods") or [50])[0]

    def _ma_or_none(fn, period):
        if len(closes) < period:
            return None
        try:
            return fn(closes, period)
        except ValueError:
            return None

    ema_val = _ma_or_none(mu.calc_ema, ema_period)
    sma_val = _ma_or_none(mu.calc_sma, sma_period)
    close_above_ema = (today_close > ema_val) if ema_val is not None else None
    close_above_sma = (today_close > sma_val) if sma_val is not None else None
    if close_above_ema is None or close_above_sma is None:
        return None
    return (not close_above_ema) and (not close_above_sma)


def build_rule(rule_cfg: dict | None = None):
    """Mirror of ibd_monitor._build_rule (same keys, same fallbacks)."""
    cfg = rule_cfg if rule_cfg is not None else DEFAULT_RULE_CONFIG
    models = loader.load_dd_modules().models
    return models.DistributionDayRule(
        min_decline_pct=cfg.get("min_decline_pct", -0.002),
        expiration_sessions=cfg.get("expiration_sessions", 25),
        invalidation_gain_pct=cfg.get("invalidation_gain_pct", 0.05),
        invalidation_price_source=cfg.get("invalidation_price_source", "high"),
    )


def analyze(history_newest_first: list[dict], rule=None, thresholds=None) -> DDDay:
    """detect -> enrich -> count -> classify, as ibd_monitor.analyze_index does (as_of=None).

    `enrich_records` mutates its records; they are created fresh here on every call.
    """
    dd = loader.load_dd_modules()
    rule = rule if rule is not None else build_rule()
    thresholds = thresholds if thresholds is not None else build_thresholds()
    raw_records, skipped = dd.tracker.detect_distribution_days(history_newest_first, rule)
    records = dd.tracker.enrich_records(raw_records, history_newest_first, rule)

    d5 = dd.tracker.count_active_in_window(records, 5)
    d15 = dd.tracker.count_active_in_window(records, 15)
    d25 = dd.tracker.count_active_in_window(records, 25)
    today_is_dd = any(r.dd_index == 0 and r.status == "active" for r in records)
    below = compute_market_below_ma(history_newest_first)
    risk = dd.risk_classifier.classify_risk(d5, d15, d25, below, thresholds)
    return DDDay(
        d5=d5,
        d15=d15,
        d25=d25,
        today_is_dd=today_is_dd,
        records=records,
        skipped=skipped,
        risk_level=risk,
        market_below_ma=below,
    )
