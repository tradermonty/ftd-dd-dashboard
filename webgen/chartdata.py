"""Chart payload shared by scripts/build_preview.py and the site chart page."""

from __future__ import annotations

import pandas as pd

NAMES = {"^GSPC": "S&P 500", "^IXIC": "Nasdaq Composite"}
SHADED = ("CAUTION", "HIGH", "SEVERE")  # NORMAL is left unshaded

# Pinned Plotly build. The sha384 was computed from the exact bytes served by jsDelivr, whose
# file metadata (data.jsdelivr.com, sha256 bSEmbOG9...k1gM=) matched the downloaded file.
PLOTLY_SRC = "https://cdn.jsdelivr.net/npm/plotly.js-dist-min@2.35.2/plotly.min.js"
PLOTLY_SRI = "sha384-cCVCZkAjYNxaYKbM8lsArLznDF/SvMFr1jcZrvOpSTCa0W40ZAdLzHCEulnUa5i7"


def risk_bands(g: pd.DataFrame) -> list[dict]:
    """Merge consecutive same-level days (date-sorted frame) into bands, skipping NORMAL.

    A band spans from its first day to the first day of the following row, so adjacent bands
    touch; the final run ends on the last day.
    """
    dates = g["date"].dt.strftime("%Y-%m-%d").tolist()
    levels = g["risk_level"].tolist()
    bands: list[dict] = []
    i = 0
    while i < len(levels):
        j = i
        while j + 1 < len(levels) and levels[j + 1] == levels[i]:
            j += 1
        if levels[i] in SHADED:
            bands.append(
                {
                    "level": levels[i],
                    "x0": dates[i],
                    "x1": dates[j + 1] if j + 1 < len(dates) else dates[j],
                }
            )
        i = j + 1
    return bands


def payload(df: pd.DataFrame, events: pd.DataFrame | None = None) -> dict:
    """Per-index chart data.

    `n_ftd` counts FTDs the way ftd_events does: an FTD dated before the first replay row is
    excluded. With `events` (build/ftd_events.csv) its rows per index are counted directly.
    """
    out = {}
    for idx, g in df.groupby("index"):
        g = g.sort_values("date")
        dd = g[g["is_dd"]]
        replay_start = g["date"].min().strftime("%Y-%m-%d")
        ftd_dates = sorted(d for d in set(g["ftd_date"].dropna()) if d >= replay_start)
        ftd = g[g["date"].dt.strftime("%Y-%m-%d").isin(ftd_dates)]
        last = g.iloc[-1]
        day1 = last["rally_day1_date"]
        out[idx] = {
            "name": NAMES.get(idx, idx),
            "date": g["date"].dt.strftime("%Y-%m-%d").tolist(),
            "close": g["close"].round(2).tolist(),
            "d5": g["d5"].astype(int).tolist(),
            "d15": g["d15"].astype(int).tolist(),
            "d25": g["d25"].astype(int).tolist(),
            "risk": g["risk_level"].tolist(),
            "bands": risk_bands(g),
            "dd_date": dd["date"].dt.strftime("%Y-%m-%d").tolist(),
            "dd_close": dd["close"].round(2).tolist(),
            "ftd_date": ftd["date"].dt.strftime("%Y-%m-%d").tolist(),
            "ftd_close": ftd["close"].round(2).tolist(),
            "last": {
                "date": last["date"].strftime("%Y-%m-%d"),
                "close": round(float(last["close"]), 2),
                "state": last["ftd_state"],
                "d5": int(last["d5"]),
                "d15": int(last["d15"]),
                "d25": int(last["d25"]),
                "risk": last["risk_level"],
                "day1": day1 if isinstance(day1, str) and day1 else "-",
            },
            "n_dd": int(len(dd)),
            "n_ftd": (
                int((events["index"] == idx).sum()) if events is not None else len(ftd_dates)
            ),
        }
    return out


def site_payload(df: pd.DataFrame, events: pd.DataFrame, overlay: dict | None = None) -> dict:
    """Chart payload for the site: FTD stars come from ftd_events.csv (the authoritative list),
    placed at the close of each `ftd_date`. With `overlay` (overlay_selected.json, research R2)
    each index also gets `lev`: the levered periods of the picked variant, as bands."""
    out = payload(df, events)
    if overlay:
        for idx, info in overlay["indices"].items():
            if idx in out and info["bands"]:
                out[idx]["lev"] = {
                    "label": info["selected"],
                    "bands": [{"x0": b["x0"], "x1": b["x1"]} for b in info["bands"]],
                }
    for idx, g in df.groupby("index"):
        close = dict(zip(g["date"].dt.strftime("%Y-%m-%d"), g["close"].round(2), strict=True))
        ev = events[events["index"] == idx].sort_values("ftd_date")
        keep = ev[ev["ftd_date"].isin(close)]
        out[idx]["ftd_date"] = keep["ftd_date"].tolist()
        out[idx]["ftd_close"] = [close[d] for d in keep["ftd_date"]]
        out[idx]["ftd_detected"] = keep["detected_on"].tolist()
        out[idx]["ftd_gain"] = keep["gain_pct"].round(2).tolist()
    return out
