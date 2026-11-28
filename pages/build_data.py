"""Public site data from pages/replay.py output (FTD and DD judgments, dd_days.csv next to it)
and the FMP cache in data/raw (prices).

Writes <out>/mock_data.js (window.MOCK, last 130 sessions, for the top page) and
<out>/history_data.js (window.HIST, all sessions, for the charts / look-back page).
Usage: python pages/build_data.py <replay csv> <out dir>
"""

import ast
import csv
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PR, OUT = Path(sys.argv[1]), Path(sys.argv[2])
KEYS = {"^GSPC": ("sp500", "GSPC"), "^IXIC": ("nasdaq", "IXIC")}
STATES = [
    "NO_SIGNAL",
    "CORRECTION",
    "RALLY_FAILED",
    "FTD_INVALIDATED",
    "RALLY_ATTEMPT",
    "FTD_WINDOW",
    "FTD_CONFIRMED",
    "UPTREND_RESUMED",
]
RISKS = ["NORMAL", "CAUTION", "HIGH", "SEVERE"]
MS = {"market_in_correction": "0", "uptrend_under_pressure": "1", "confirmed_uptrend": "2"}
REASON_BIT = {"distribution": 1, "below_50sma": 2, "ftd_low_close_breach": 4}

pr = list(csv.DictReader(PR.open(encoding="utf-8")))
pr_by_date = {r["date"]: r for r in pr}
dds = list(csv.DictReader(PR.with_name("dd_days.csv").open(encoding="utf-8")))


def raw(prefix):
    out = {}
    for p in sorted((ROOT / "data/raw").glob(f"{prefix}_*.json")):
        for b in json.loads(p.read_text()):
            out[b["date"]] = b
    return out


def n_active(s):
    try:
        v = ast.literal_eval(s) if s else []
    except (ValueError, SyntaxError):
        return 0
    return len(v) if isinstance(v, list) else int(v or 0)


hist = {"states": STATES, "risks": RISKS, "indices": {}}
mock = {"as_of": None, "indices": {}}
for sym, (k, pre) in KEYS.items():
    bars = raw(pre)
    d, c, ddf, d25, d5, d15, risk, st, ms, day1, day, ref, rsn, adds = ([] for _ in range(14))
    for p in pr:
        d.append(p["date"])
        c.append(round(float(bars[p["date"]]["close"]), 2))
        ddf.append("1" if p[f"{k}_is_dd"] == "True" else "0")
        d25.append(int(p[f"{k}_d25"]))
        d5.append(int(p[f"{k}_d5"]))
        d15.append(int(p[f"{k}_d15"]))
        risk.append(str(RISKS.index(p[f"{k}_risk"])))
        st.append(str(STATES.index(p[f"{k}_state"])))
        ms.append(MS.get(p[f"{k}_status"], "9"))
        day1.append(p[f"{k}_day1"] or "")
        day.append(int(p[f"{k}_day"]) if p[f"{k}_day"] else 0)
        ref.append(p[f"{k}_ref_low_date"] or "")
        bits = 0
        for x in (p[f"{k}_pressure"] or "").split("|"):
            bits |= REASON_BIT.get(x, 0)
        rsn.append(str(bits))
        adds.append(n_active(p[f"{k}_active_dd"]))
    def vref(j, k=k, d=d):
        """The session the skill compared volume with: the previous day, or after an early
        close the last full session."""
        return pr[j][f"{k}_vref"] or d[j - 1]

    # FTD events: day1, reference low date, ftd date, end date, end kind (inv | corr | "")
    ftds, i = [], 0
    pos = {x: j for j, x in enumerate(d)}
    seen = set()
    for x in d:
        f = pr_by_date[x][f"{k}_ftd"]
        if f and f not in seen and f in pos:
            seen.add(f)
            fi = pos[f]
            end, kind = "", ""
            for m in range(fi + 1, len(d)):
                if ms[m] == "0":
                    end, kind = d[m], ("inv" if st[m] == "3" else "corr")
                    break
            gain = (bars[f]["close"] / bars[d[fi - 1]]["close"] - 1) * 100
            vol = (bars[f]["volume"] / bars[vref(fi)]["volume"] - 1) * 100
            dn = day[fi]
            ftds.append([day1[fi], ref[fi], f, end, kind, round(gain, 2), round(vol, 1), dn])
    resumed = [d[j] for j in range(1, len(d)) if st[j] == "7" and st[j - 1] != "7"]
    breach = []  # first day of each FTD-day-low close breach run
    for j in range(len(d)):
        if int(rsn[j]) & 4 and not (j and int(rsn[j - 1]) & 4):
            breach.append(d[j])
    ddinv = {
        x["date"]: x["invalidated_on"] for x in dds if x["index"] == sym and x["invalidated_on"]
    }
    hist["indices"][sym] = {
        "d": d,
        "c": c,
        "dd": "".join(ddf),
        "d25": d25,
        "d5": d5,
        "d15": d15,
        "risk": "".join(risk),
        "st": "".join(st),
        "ms": "".join(ms),
        "day1": day1,
        "day": day,
        "ref": ref,
        "rsn": "".join(rsn),
        "adds": adds,
        "ftd": ftds,
        "breach": breach,
        "resumed": resumed,
        "ddinv": ddinv,
        "low": {x: bars[x]["low"] for x in sorted(set(ref)) if x},
    }
    # last 130 sessions for the top page
    series = []
    for j in range(len(d) - 130, len(d)):
        b = bars[d[j]]
        series.append(
            {
                "d": d[j],
                "o": b["open"],
                "h": b["high"],
                "l": b["low"],
                "c": b["close"],
                "v": b["volume"],
                "pct": round((c[j] / c[j - 1] - 1) * 100, 2),
                "vchg": round((b["volume"] / bars[vref(j)]["volume"] - 1) * 100, 1),
                "dd": ddf[j] == "1",
                "d5": d5[j],
                "d15": d15[j],
                "d25": d25[j],
                "risk": RISKS[int(risk[j])],
                "state": STATES[int(st[j])],
                "ms": ms[j],
                "rsn": int(rsn[j]),
                "adds": adds[j],
                "day1": day1[j],
                "day": day[j],
                "low": ref[j],
            }
        )
    last = ftds[-1]
    lo_i = d.index(last[1]) if last[1] in d else d.index(last[2])
    end_i = d.index(last[3]) + 3 if last[3] else d.index(last[2]) + 8
    story = []
    for j in range(max(1, lo_i - 14), min(len(d), max(end_i, d.index(last[2]) + 8) + 1)):
        b = bars[d[j]]
        story.append(
            {
                "d": d[j],
                "o": b["open"],
                "h": b["high"],
                "l": b["low"],
                "c": b["close"],
                "v": b["volume"],
                "dd": ddf[j] == "1",
                "day1": day1[j],
                "day": day[j],
                "low": ref[j],
            }
        )
    mock["indices"][sym] = {
        "series": series,
        "story_series": story,
        "last_ftd": {
            "rally_day1_date": last[0],
            "swing_low_date": last[1],
            "ftd_date": last[2],
            "ftd_day_number": last[7],
            "gain_pct": last[5],
            "volume_vs_prev_pct": last[6],
            "invalidation_date": last[3] if last[4] == "inv" else "",
            "end_date": last[3],
            "end_kind": last[4],
        },
        "dd_days": [
            {
                "date": x["date"],
                "expires_on": x["expires_on"],
                "invalidated_on": x["invalidated_on"],
            }
            for x in dds
            if x["index"] == sym and x["date"] >= series[0]["d"]
        ],
    }
    mock["as_of"] = d[-1]
hist["combined"] = "".join(MS.get(r["combined_status"], "9") for r in pr if r["date"] in pos)
OUT.mkdir(parents=True, exist_ok=True)
(OUT / "history_data.js").write_text(
    "window.HIST=" + json.dumps(hist, separators=(",", ":")) + ";\n"
)
(OUT / "mock_data.js").write_text("window.MOCK=" + json.dumps(mock, separators=(",", ":")) + ";\n")
for sym in KEYS:
    h = hist["indices"][sym]
    print(
        sym,
        "sessions",
        len(h["d"]),
        "FTDs",
        len(h["ftd"]),
        "last FTD",
        h["ftd"][-1],
        "breach runs",
        len(h["breach"]),
    )
print("as of", mock["as_of"])
