"""Preview generator: risk-band merging and page contents. No network."""

from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))

import build_preview  # noqa: E402


def _df(levels):
    dates = pd.bdate_range("2024-01-01", periods=len(levels))
    return pd.DataFrame({"date": dates, "risk_level": levels})


def test_bands_merge_consecutive_and_skip_normal():
    df = _df(["NORMAL", "CAUTION", "CAUTION", "HIGH", "NORMAL", "NORMAL", "HIGH", "SEVERE"])
    bands = build_preview.risk_bands(df)
    assert [b["level"] for b in bands] == ["CAUTION", "HIGH", "HIGH", "SEVERE"]
    d = df["date"].dt.strftime("%Y-%m-%d").tolist()
    # a band runs from its first day to the first day after its last day (contiguous bands touch)
    assert bands[0]["x0"] == d[1] and bands[0]["x1"] == d[3]
    assert bands[1]["x0"] == d[3] and bands[1]["x1"] == d[4]
    assert bands[2]["x0"] == d[6] and bands[2]["x1"] == d[7]
    # the final run ends on the last day (no following row)
    assert bands[3]["x0"] == d[7] and bands[3]["x1"] == d[7]


def test_bands_all_normal_is_empty():
    assert build_preview.risk_bands(_df(["NORMAL"] * 5)) == []


def test_build_html_has_legend_note_and_pinned_plotly(tmp_path):
    csv = tmp_path / "daily_status.csv"
    rows = []
    for sym in ("^GSPC", "^IXIC"):
        for k, d in enumerate(pd.bdate_range("2024-01-01", periods=10)):
            rows.append(
                {
                    "date": d.strftime("%Y-%m-%d"),
                    "index": sym,
                    "close": 100 + k,
                    "is_dd": k == 3,
                    "d5": 1,
                    "d15": 2,
                    "d25": 3,
                    "ftd_state": "NO_SIGNAL",
                    "ftd_date": "",
                    "rally_day1_date": "",
                    "risk_level": "CAUTION" if k > 4 else "NORMAL",
                }
            )
    pd.DataFrame(rows).to_csv(csv, index=False)
    out = tmp_path / "preview.html"
    build_preview.build(csv, out)
    html = out.read_text(encoding="utf-8")
    assert "plotly.js-dist-min@2.35.2" in html
    assert 'integrity="sha384-' in html and 'crossorigin="anonymous"' in html
    for word in ("CAUTION", "HIGH", "SEVERE", "ibd-distribution-day-monitor"):
        assert word in html
    assert "__DATA__" not in html


def _ftd_frame():
    """Two indices; ^GSPC reports an FTD dated BEFORE the first replay row (2023-12-20)."""
    dates = pd.bdate_range("2024-01-01", periods=10)
    rows = []
    for sym in ("^GSPC", "^IXIC"):
        for k, d in enumerate(dates):
            ftd = ""
            if sym == "^GSPC":
                ftd = "2023-12-20" if k < 5 else dates[3].strftime("%Y-%m-%d")
            elif k >= 6:
                ftd = dates[5].strftime("%Y-%m-%d")
            rows.append(
                {
                    "date": d,
                    "index": sym,
                    "close": 100 + k,
                    "is_dd": False,
                    "d5": 0,
                    "d15": 0,
                    "d25": 0,
                    "ftd_state": "NO_SIGNAL",
                    "ftd_date": ftd,
                    "rally_day1_date": "",
                    "risk_level": "NORMAL",
                }
            )
    return pd.DataFrame(rows)


def test_n_ftd_excludes_ftd_dated_before_replay_start():
    p = build_preview._payload(_ftd_frame())
    assert p["^GSPC"]["n_ftd"] == 1  # 2023-12-20 predates the replay: not counted
    assert p["^IXIC"]["n_ftd"] == 1


def test_n_ftd_uses_events_csv_when_given(tmp_path):
    ev = pd.DataFrame({"index": ["^GSPC", "^GSPC", "^IXIC"], "ftd_date": ["a", "b", "c"]})
    p = build_preview._payload(_ftd_frame(), events=ev)
    assert p["^GSPC"]["n_ftd"] == 2 and p["^IXIC"]["n_ftd"] == 1


def test_build_reads_sibling_ftd_events_csv(tmp_path):
    _ftd_frame().to_csv(tmp_path / "daily_status.csv", index=False)
    pd.DataFrame({"index": ["^GSPC"] * 3, "ftd_date": ["x", "y", "z"]}).to_csv(
        tmp_path / "ftd_events.csv", index=False
    )
    payload = build_preview.build(tmp_path / "daily_status.csv", tmp_path / "p.html")
    assert payload["^GSPC"]["n_ftd"] == 3
    assert payload["^IXIC"]["n_ftd"] == 0  # events csv is authoritative when present
