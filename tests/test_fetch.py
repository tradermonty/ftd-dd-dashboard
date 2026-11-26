"""fetch.py behaviour with fake HTTP responses (no network)."""

from __future__ import annotations

import json
import os
from datetime import date, datetime
from zoneinfo import ZoneInfo

import pytest

from engine import fetch

KEY = "SECRET-KEY-123"


def _sessions(start: str, end: str) -> list[str]:
    cal = fetch.get_xnys()
    return [d.strftime("%Y-%m-%d") for d in cal.sessions_in_range(start, end)]


def _row(d: str, close: float = 100.0, volume=1_000_000):
    return {
        "symbol": "^GSPC",
        "date": d,
        "open": close,
        "high": close + 1,
        "low": close - 1,
        "close": close,
        "volume": volume,
    }


class FakeHttp:
    """Serves rows from a dict date->row for any (from, to) window, newest first."""

    def __init__(self, rows: dict[str, dict]):
        self.rows = rows
        self.calls: list[dict] = []

    def __call__(self, url: str, params: dict):
        self.calls.append(dict(params))
        lo, hi = params["from"], params["to"]
        out = [r for d, r in self.rows.items() if lo <= d <= hi]
        return sorted(out, key=lambda r: r["date"], reverse=True)


def _full(start: str, end: str, **overrides) -> dict[str, dict]:
    rows = {}
    for i, d in enumerate(_sessions(start, end)):
        rows[d] = _row(d, close=100 + i * 0.1, volume=1_000_000 + i * 1000)
    rows.update(overrides)
    return rows


def test_make_chunks_seven_for_production_range():
    chunks = fetch.make_chunks(date(1999, 6, 1), date(2026, 11, 6))
    assert len(chunks) == 7
    assert chunks[0][0] == date(1999, 6, 1)
    assert chunks[-1][1] == date(2026, 11, 6)
    for (_, e1), (s2, _) in zip(chunks, chunks[1:], strict=False):
        assert (s2 - e1).days == 1


def test_fetch_ok_merges_and_sorts(tmp_path):
    rows = _full("2020-01-01", "2021-12-31")
    http = FakeHttp(rows)
    res = fetch.fetch_index(
        "^GSPC",
        date(2020, 1, 1),
        date(2021, 12, 31),
        http_get=http,
        api_key=KEY,
        chunk_years=1,
        cache_dir=tmp_path,
    )
    assert [r["date"] for r in res.rows] == _sessions("2020-01-01", "2021-12-31")
    assert len(http.calls) == 2
    assert all(c["symbol"] == "^GSPC" for c in http.calls)
    assert res.issues == []


def test_truncation_5000_rows_fails():
    rows = [_row(f"2000-01-{i:02d}") for i in range(1, 2)] * 5000
    with pytest.raises(fetch.FetchError, match="truncat"):
        fetch.check_chunk_size("^GSPC", rows, date(2000, 1, 1), date(2003, 12, 31))


def test_truncation_via_fetch_index(monkeypatch, tmp_path):
    monkeypatch.setattr(fetch, "TRUNCATION_ROWS", 20)
    rows = _full("2020-01-01", "2020-03-31")  # ~63 rows in one chunk -> >= 20
    with pytest.raises(fetch.FetchError, match="truncat"):
        fetch.fetch_index(
            "^GSPC",
            date(2020, 1, 1),
            date(2020, 3, 31),
            http_get=FakeHttp(rows),
            api_key=KEY,
            cache_dir=tmp_path,
        )


def test_missing_session_detected(tmp_path):
    rows = _full("2020-01-01", "2020-03-31")
    missing = "2020-02-12"
    del rows[missing]
    with pytest.raises(fetch.FetchError, match=missing):
        fetch.fetch_index(
            "^GSPC",
            date(2020, 1, 1),
            date(2020, 3, 31),
            http_get=FakeHttp(rows),
            api_key=KEY,
            cache_dir=tmp_path,
        )


def test_non_session_row_detected(tmp_path):
    rows = _full("2020-01-01", "2020-03-31")
    rows["2020-02-15"] = _row("2020-02-15")  # a Saturday
    with pytest.raises(fetch.FetchError, match="2020-02-15"):
        fetch.fetch_index(
            "^GSPC",
            date(2020, 1, 1),
            date(2020, 3, 31),
            http_get=FakeHttp(rows),
            api_key=KEY,
            cache_dir=tmp_path,
        )


def test_duplicate_dates_detected():
    rows = [_row("2020-01-02"), _row("2020-01-02"), _row("2020-01-03")]
    with pytest.raises(fetch.FetchError, match="duplicate"):
        fetch.merge_chunks([rows])


def test_duplicate_across_chunks_detected():
    with pytest.raises(fetch.FetchError, match="duplicate"):
        fetch.merge_chunks([[_row("2020-01-02")], [_row("2020-01-02"), _row("2020-01-03")]])


def test_data_issues_flagged_not_dropped(tmp_path):
    sess = _sessions("2020-01-01", "2020-02-28")
    rows = _full("2020-01-01", "2020-02-28")
    rows[sess[5]]["volume"] = 0
    rows[sess[8]]["volume"] = None
    rows[sess[10]]["volume"] = rows[sess[9]]["volume"]  # same as previous day
    res = fetch.fetch_index(
        "^GSPC",
        date(2020, 1, 1),
        date(2020, 2, 28),
        http_get=FakeHttp(rows),
        api_key=KEY,
        cache_dir=tmp_path,
    )
    assert len(res.rows) == len(sess)  # nothing dropped
    by_date = {(i["date"], i["issue"]) for i in res.issues}
    assert (sess[5], "zero_volume") in by_date
    assert (sess[8], "missing_volume") in by_date
    assert (sess[10], "volume_equals_previous_day") in by_date
    assert all(i["symbol"] == "^GSPC" for i in res.issues)


def test_error_payload_raises_without_leaking_key(tmp_path):
    def http(url, params):
        return {"Error Message": "Invalid API KEY."}

    with pytest.raises(fetch.FetchError) as ei:
        fetch.fetch_index(
            "^GSPC",
            date(2020, 1, 1),
            date(2020, 3, 31),
            http_get=http,
            api_key=KEY,
            cache_dir=tmp_path,
        )
    assert KEY not in str(ei.value)


def test_default_http_redacts_key(monkeypatch):
    import requests

    def boom(url, params=None, timeout=None):
        raise requests.ConnectionError(f"failed for {url}?apikey={params['apikey']}")

    monkeypatch.setattr(requests, "get", boom)
    with pytest.raises(fetch.FetchError) as ei:
        fetch.default_http_get("https://example.invalid/x", {"apikey": KEY, "symbol": "X"})
    assert KEY not in str(ei.value)
    assert KEY not in repr(ei.value)


def test_cache_written_and_reused(tmp_path):
    rows = _full("2020-01-01", "2020-03-31")
    http = FakeHttp(rows)
    kw = dict(api_key=KEY, cache_dir=tmp_path)
    fetch.fetch_index("^GSPC", date(2020, 1, 1), date(2020, 3, 31), http_get=http, **kw)
    n = len(http.calls)
    files = list(tmp_path.glob("*.json"))
    assert files and KEY not in files[0].read_text()
    assert KEY not in "".join(f.name for f in files)
    res = fetch.fetch_index(
        "^GSPC", date(2020, 1, 1), date(2020, 3, 31), http_get=http, use_cache=True, **kw
    )
    assert len(http.calls) == n  # served from cache
    assert len(res.rows) == len(_sessions("2020-01-01", "2020-03-31"))
    json.loads(files[0].read_text())


def test_calendar_starts_before_2006():
    cal = fetch.get_xnys()
    assert len(_sessions("1999-06-01", "2003-05-30")) == 1005
    assert cal.is_session("2000-01-03")


def test_normalised_rows_have_numeric_ohlcv():
    row = {"date": "2020-01-02", "open": "1", "high": 2, "low": 0.5, "close": 1.5, "volume": 10}
    n = fetch.normalise_row(row)
    assert n == {
        "date": "2020-01-02",
        "open": 1.0,
        "high": 2.0,
        "low": 0.5,
        "close": 1.5,
        "volume": 10,
    }


# ------------------------------------------------------------- final-bar / cache freshness

ET = ZoneInfo("America/New_York")


def _et(y, m, d, hh, mm=0):
    return datetime(y, m, d, hh, mm, tzinfo=ET)


@pytest.mark.parametrize(
    ("now", "expected"),
    [
        (_et(2026, 11, 6, 15, 0), date(2026, 11, 5)),  # Fri, before the final bar
        (_et(2026, 11, 6, 16, 29), date(2026, 11, 5)),
        (_et(2026, 11, 6, 16, 30), date(2026, 11, 6)),  # Fri, bar is final
        (_et(2026, 11, 7, 12, 0), date(2026, 11, 6)),  # Saturday
        (_et(2026, 11, 9, 9, 0), date(2026, 11, 6)),  # Monday pre-close -> Friday
        (datetime(2026, 11, 6, 21, 45, tzinfo=ZoneInfo("UTC")), date(2026, 11, 6)),  # 16:45 ET
        (datetime(2026, 11, 6, 20, 0, tzinfo=ZoneInfo("UTC")), date(2026, 11, 5)),  # 15:00 ET
    ],
)
def test_latest_final_session(now, expected):
    assert fetch.latest_final_session(now) == expected


def test_default_end_excludes_unfinished_bar(tmp_path):
    rows = _full("2026-10-01", "2026-11-06")
    http = FakeHttp(rows)
    res = fetch.fetch_index(
        "^GSPC",
        date(2026, 10, 1),
        http_get=http,
        api_key=KEY,
        cache_dir=tmp_path,
        now=_et(2026, 11, 6, 15, 0),
    )
    assert res.rows[-1]["date"] == "2026-11-05"
    assert http.calls[-1]["to"] == "2026-11-05"
    res2 = fetch.fetch_index(
        "^GSPC",
        date(2026, 10, 1),
        http_get=http,
        api_key=KEY,
        cache_dir=tmp_path,
        now=_et(2026, 11, 6, 17, 0),
    )
    assert res2.rows[-1]["date"] == "2026-11-06"


def _prime_cache(tmp_path, mtime_et):
    rows = _full("2026-10-01", "2026-11-06")
    http = FakeHttp(rows)
    fetch.fetch_index(
        "^GSPC",
        date(2026, 10, 1),
        date(2026, 11, 6),
        http_get=http,
        api_key=KEY,
        cache_dir=tmp_path,
        now=_et(2026, 11, 9, 9, 0),
    )
    for f in tmp_path.glob("*.json"):
        ts = mtime_et.timestamp()
        os.utime(f, (ts, ts))
    return http


def _refetch(http, tmp_path, now, end=None):
    fetch.fetch_index(
        "^GSPC",
        date(2026, 10, 1),
        end,
        http_get=http,
        api_key=KEY,
        cache_dir=tmp_path,
        use_cache=True,
        now=now,
    )


def test_cache_of_latest_chunk_written_before_final_bar_is_not_reused(tmp_path):
    http = _prime_cache(tmp_path, _et(2026, 11, 6, 15, 0))  # cached before the 16:30 final bar
    n = len(http.calls)
    _refetch(http, tmp_path, _et(2026, 11, 9, 9, 0))
    assert len(http.calls) == n + 1  # latest chunk refetched


def test_cache_of_latest_chunk_written_after_final_bar_is_reused(tmp_path):
    http = _prime_cache(tmp_path, _et(2026, 11, 6, 17, 0))
    n = len(http.calls)
    _refetch(http, tmp_path, _et(2026, 11, 9, 9, 0))
    assert len(http.calls) == n


def test_chunk_before_latest_session_is_reused_regardless_of_mtime(tmp_path):
    http = _prime_cache(tmp_path, _et(2026, 11, 6, 15, 0))
    n = len(http.calls)
    _refetch(http, tmp_path, _et(2026, 12, 15, 18, 0), end=date(2026, 11, 6))
    assert len(http.calls) == n  # end 11-06 is not the latest session on 12-15
