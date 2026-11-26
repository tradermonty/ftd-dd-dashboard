"""Evaluations A / B / C (spec §3.6). Pure functions on bars and signals.

Every function takes an explicit [start, end] window and slices the bars to <= end FIRST, so
nothing after `end` can influence the result (design evaluation cannot see test bars).

A. FTD as a trade: buy at the close of `detected_on`; sell at the first close below the FTD
   day's low (same test as post_ftd_monitor.check_ftd_invalidation: close < ftd_low), else at
   the close HORIZON sessions after entry. The 60-session window must fit inside the period;
   otherwise the trade is dropped (counted). Trades for which a close between ftd_date and
   detected_on is already below the FTD low are skipped (FTD invalidated at detection).
B. DD risk as exposure: signal fixed at the close of day t, applied to the return of t+1.
   Cash earns 0. Compared against buy & hold over identical return days.
C. P(drawdown <= -10% of the close within the next 60 sessions) by label (risk level).
"""

from __future__ import annotations

import math
from collections.abc import Sequence

from engine import outcomes
from research.split import HORIZON

TRADING_DAYS = 252
DD_THRESHOLD = -0.10


def _slice_to(bars: list[dict], end: str) -> list[dict]:
    return [b for b in bars if b["date"] <= end]


# ------------------------------------------------------------------ A


def trade_returns(
    evs: Sequence[dict], bars: list[dict], start: str, end: str, horizon: int = HORIZON
) -> tuple[list[dict], dict]:
    """Trades for the events of ONE index with start <= detected_on <= end."""
    bars = _slice_to(bars, end)
    pos = {b["date"]: i for i, b in enumerate(bars)}
    trades: list[dict] = []
    dropped = skipped = 0
    for e in evs:
        if not (start <= e["detected_on"] <= end):
            continue
        di, fi = pos[e["detected_on"]], pos[e["ftd_date"]]
        ftd_low = bars[fi]["low"]
        if any(bars[k]["close"] < ftd_low for k in range(fi + 1, di + 1)):
            skipped += 1
            continue
        if di + horizon >= len(bars):
            dropped += 1
            continue
        exit_i, reason = di + horizon, "horizon"
        for k in range(di + 1, di + horizon + 1):
            if bars[k]["close"] < ftd_low:
                exit_i, reason = k, "stop"
                break
        entry, exit_ = bars[di]["close"], bars[exit_i]["close"]
        trades.append(
            {
                "ftd_date": e["ftd_date"],
                "entry_date": e["detected_on"],
                "exit_date": bars[exit_i]["date"],
                "exit_reason": reason,
                "ret": exit_ / entry - 1,
            }
        )
    meta = {"n_events": sum(1 for e in evs if start <= e["detected_on"] <= end)}
    meta["n_dropped_incomplete_window"] = dropped
    meta["n_skipped_invalidated_at_entry"] = skipped
    return trades, meta


def trade_stats(trades: Sequence[dict]) -> dict:
    rets = sorted(t["ret"] for t in trades)
    n = len(rets)
    if n == 0:
        return {
            "n_trades": 0,
            "win_rate": None,
            "median": None,
            "mean": None,
            "p10": None,
            "p90": None,
            "profit_factor": None,
            "n_stopped": 0,
        }
    gains = sum(r for r in rets if r > 0)
    losses = -sum(r for r in rets if r < 0)
    return {
        "n_trades": n,
        "win_rate": sum(1 for r in rets if r > 0) / n,
        "median": outcomes._percentile(rets, 0.5),
        "mean": sum(rets) / n,
        "p10": outcomes._percentile(rets, 0.10),
        "p90": outcomes._percentile(rets, 0.90),
        "profit_factor": gains / losses if losses > 0 else None,
        "n_stopped": sum(1 for t in trades if t["exit_reason"] == "stop"),
    }


# ------------------------------------------------------------------ B


def _perf(returns: Sequence[float]) -> dict:
    n = len(returns)
    if n == 0:
        return {"cagr": None, "max_dd": None, "vol": None, "years": 0.0}
    eq, peak, mdd = 1.0, 1.0, 0.0
    for r in returns:
        eq *= 1 + r
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    years = n / TRADING_DAYS
    mean = sum(returns) / n
    var = sum((r - mean) ** 2 for r in returns) / (n - 1) if n > 1 else 0.0
    return {
        "cagr": eq ** (1 / years) - 1,
        "max_dd": mdd,
        "vol": math.sqrt(var) * math.sqrt(TRADING_DAYS),
        "years": years,
    }


def exposure_backtest(
    dates: Sequence[str],
    closes: Sequence[float],
    flags: Sequence[bool],
    exposure: float,
    start: str,
    end: str,
) -> dict:
    """Return day k (start <= date[k-1], date[k] <= end) is scaled by `exposure` if the
    signal of day k-1 was on, else 100%. Cash return 0."""
    strat: list[float] = []
    bh: list[float] = []
    exp: list[float] = []
    for k in range(1, len(dates)):
        if dates[k - 1] < start or dates[k] > end:
            continue
        r = closes[k] / closes[k - 1] - 1
        e = exposure if flags[k - 1] else 1.0
        strat.append(e * r)
        bh.append(r)
        exp.append(e)
    s, b = _perf(strat), _perf(bh)
    # Exposure-matched benchmark (reporting only, never used for selection): a constant,
    # daily-rebalanced exposure equal to the strategy's average exposure, cash return 0.
    avg_exposure = sum(exp) / len(exp) if exp else None
    em = _perf([avg_exposure * r for r in bh]) if exp else _perf([])
    return {
        "em_cagr": em["cagr"],
        "em_max_dd": em["max_dd"],
        "n_days": len(strat),
        "years": s["years"],
        "cagr": s["cagr"],
        "max_dd": s["max_dd"],
        "vol": s["vol"],
        "avg_exposure": avg_exposure,
        "bh_cagr": b["cagr"],
        "bh_max_dd": b["max_dd"],
        "bh_vol": b["vol"],
    }


# ------------------------------------------------------------------ C


def drawdown_prob(
    dates: Sequence[str],
    closes: Sequence[float],
    labels: Sequence[str],
    start: str,
    end: str,
    horizon: int = HORIZON,
    threshold: float = DD_THRESHOLD,
) -> dict:
    """{"by_label": {label: {"n", "hits", "p"}}, "n_dropped_incomplete_window": int}.

    Day t counts when start <= date[t] <= end and t + horizon is still <= end."""
    last = max((i for i, d in enumerate(dates) if d <= end), default=-1)
    by: dict[str, dict] = {}
    dropped = 0
    for t in range(last + 1):
        if dates[t] < start:
            continue
        if t + horizon > last:
            dropped += 1
            continue
        hit = min(closes[t + 1 : t + horizon + 1]) / closes[t] - 1 <= threshold + 1e-12
        c = by.setdefault(labels[t], {"n": 0, "hits": 0})
        c["n"] += 1
        c["hits"] += int(hit)
    for c in by.values():
        c["p"] = c["hits"] / c["n"] if c["n"] else None
    return {"by_label": by, "n_dropped_incomplete_window": dropped}
