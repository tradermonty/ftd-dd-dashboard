"""FMP historical EOD fetch with truncation / gap / duplicate guards.

FMP returns at most 5,000 rows per call and silently keeps the NEWEST 5,000, so the
requested range is split into 4-year chunks and any chunk with >= 5,000 rows is a
hard failure. After merging, dates must equal the XNYS session set exactly.

Rows are never dropped. Zero / missing volume and volume equal to the previous day are
reported as data issues; whether they matter is left to the vendored rules.

The API key is read from FMP_API_KEY (a local .env is loaded if present) and is never
logged, cached, or included in error messages.
"""

from __future__ import annotations

import json
import os
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from functools import cache
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import requests

ET = ZoneInfo("America/New_York")
FINAL_BAR_TIME = time(16, 30)  # a session's EOD bar is treated as final from 16:30 ET

BASE_URL = "https://financialmodelingprep.com/stable/historical-price-eod/full"
TRUNCATION_ROWS = 5000
DEFAULT_START = date(1999, 6, 1)
DEFAULT_SYMBOLS = ("^GSPC", "^IXIC")
ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CACHE_DIR = ROOT / "data" / "raw"

HttpGet = Callable[[str, dict], Any]


class FetchError(RuntimeError):
    """Fetch or validation failure. Messages never contain the API key."""


@dataclass
class FetchResult:
    symbol: str
    rows: list[dict]  # chronological (oldest first), normalised
    issues: list[dict] = field(default_factory=list)


@cache
def get_xnys():
    import exchange_calendars as xcals

    # The default calendar starts in 2006; an explicit start is mandatory.
    return xcals.get_calendar("XNYS", start="1999-01-01")


def load_api_key() -> str:
    try:
        from dotenv import load_dotenv

        load_dotenv(ROOT / ".env")
    except ImportError:  # pragma: no cover
        pass
    key = os.environ.get("FMP_API_KEY")
    if not key:
        raise FetchError("FMP_API_KEY is not set (env or .env)")
    return key


def _add_years(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:  # Feb 29
        return d.replace(year=d.year + years, day=28)


def make_chunks(start: date, end: date, years: int = 4) -> list[tuple[date, date]]:
    chunks = []
    cur = start
    while cur <= end:
        nxt = _add_years(cur, years) - timedelta(days=1)
        chunks.append((cur, min(nxt, end)))
        cur = nxt + timedelta(days=1)
    return chunks


def default_http_get(url: str, params: dict) -> Any:
    key = params.get("apikey", "")
    try:
        resp = requests.get(url, params=params, timeout=60)
        resp.raise_for_status()
        return resp.json()
    except requests.RequestException as exc:
        msg = str(exc)
        if key:
            msg = msg.replace(key, "***")
        raise FetchError(
            f"HTTP error for {params.get('symbol')}: {type(exc).__name__}: {msg}"
        ) from None
    except ValueError:
        raise FetchError(f"non-JSON response for {params.get('symbol')}") from None


def check_chunk_size(symbol: str, rows: list, start: date, end: date) -> None:
    if len(rows) >= TRUNCATION_ROWS:
        raise FetchError(
            f"{symbol} {start}..{end}: {len(rows)} rows >= {TRUNCATION_ROWS}; "
            "response is probably truncated (FMP keeps the newest rows)"
        )


def _cache_path(cache_dir: Path, symbol: str, start: date, end: date) -> Path:
    safe = symbol.replace("^", "").replace("/", "_")
    return cache_dir / f"{safe}_{start}_{end}.json"


def fetch_chunk(
    symbol: str,
    start: date,
    end: date,
    *,
    http_get: HttpGet,
    api_key: str,
    cache_dir: Path | None,
    use_cache: bool,
    min_cache_mtime: datetime | None = None,
) -> list[dict]:
    """Fetch one chunk. A cache file is reused only if `use_cache` and, when `min_cache_mtime`
    is given (chunk contains the latest session), it was written at or after that time."""
    path = _cache_path(cache_dir, symbol, start, end) if cache_dir else None
    cache_ok = use_cache and path is not None and path.exists()
    if cache_ok and min_cache_mtime is not None:
        cache_ok = path.stat().st_mtime >= min_cache_mtime.timestamp()
    if cache_ok:
        rows = json.loads(path.read_text())
    else:
        params = {
            "symbol": symbol,
            "from": start.isoformat(),
            "to": end.isoformat(),
            "apikey": api_key,
        }
        rows = http_get(BASE_URL, params)
        if not isinstance(rows, list):
            detail = rows.get("Error Message") if isinstance(rows, dict) else type(rows).__name__
            raise FetchError(f"{symbol} {start}..{end}: unexpected response ({detail})")
        if path is not None:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(rows))
    if not rows:
        raise FetchError(f"{symbol} {start}..{end}: empty response")
    check_chunk_size(symbol, rows, start, end)
    return rows


def merge_chunks(chunks: list[list[dict]]) -> list[dict]:
    """Concatenate raw chunks, reject duplicate dates, return oldest-first."""
    merged = [r for chunk in chunks for r in chunk]
    counts = Counter(r["date"] for r in merged)
    dups = sorted(d for d, n in counts.items() if n > 1)
    if dups:
        raise FetchError(f"duplicate dates: {dups[:5]}{' ...' if len(dups) > 5 else ''}")
    return sorted(merged, key=lambda r: r["date"])


def check_sessions(symbol: str, rows: list[dict], start: date, end: date) -> None:
    expected = {d.strftime("%Y-%m-%d") for d in get_xnys().sessions_in_range(start, end)}
    got = {r["date"] for r in rows}
    missing = sorted(expected - got)
    extra = sorted(got - expected)
    if missing or extra:
        raise FetchError(
            f"{symbol}: dates differ from XNYS sessions "
            f"(missing {len(missing)}: {missing[:5]}; extra {len(extra)}: {extra[:5]})"
        )


def find_data_issues(symbol: str, rows: list[dict]) -> list[dict]:
    """Flag zero/missing volume and volume identical to the previous day. rows: oldest first."""
    issues = []
    prev_vol = None
    for r in rows:
        vol = r.get("volume")
        if vol is None or vol == "":
            issues.append({"symbol": symbol, "date": r["date"], "issue": "missing_volume"})
        elif vol == 0:
            issues.append({"symbol": symbol, "date": r["date"], "issue": "zero_volume"})
        elif prev_vol is not None and vol == prev_vol:
            issues.append(
                {"symbol": symbol, "date": r["date"], "issue": "volume_equals_previous_day"}
            )
        prev_vol = vol if vol not in (None, "") else prev_vol
    return issues


def normalise_row(r: dict) -> dict:
    vol = r.get("volume")
    return {
        "date": r["date"],
        "open": float(r["open"]),
        "high": float(r["high"]),
        "low": float(r["low"]),
        "close": float(r["close"]),
        "volume": 0 if vol in (None, "") else vol,
    }


def fetch_index(
    symbol: str,
    start: date = DEFAULT_START,
    end: date | None = None,
    *,
    http_get: HttpGet = default_http_get,
    api_key: str | None = None,
    chunk_years: int = 4,
    cache_dir: Path | None = DEFAULT_CACHE_DIR,
    use_cache: bool = False,
    now: datetime | None = None,
) -> FetchResult:
    """`now` is an injectable clock (tz-aware). The default end is the latest session whose
    bar is final (see `latest_final_session`). A cached chunk that contains the latest final
    session is reused only if the cache file was written at/after that session's 16:30 ET."""
    latest_final = latest_final_session(now)
    if end is None:
        end = latest_final
    key = api_key if api_key is not None else load_api_key()
    fresh_after = datetime.combine(latest_final, FINAL_BAR_TIME, tzinfo=ET)
    chunks = [
        fetch_chunk(
            symbol,
            s,
            e,
            http_get=http_get,
            api_key=key,
            cache_dir=cache_dir,
            use_cache=use_cache,
            min_cache_mtime=fresh_after if e >= latest_final else None,
        )
        for s, e in make_chunks(start, end, chunk_years)
    ]
    raw = merge_chunks(chunks)
    check_sessions(symbol, raw, start, end)
    issues = find_data_issues(symbol, raw)
    return FetchResult(symbol=symbol, rows=[normalise_row(r) for r in raw], issues=issues)


def latest_final_session(now: datetime | None = None) -> date:
    """Latest XNYS session whose EOD bar is final at `now` (default: current time).

    Evaluated in America/New_York: today counts only if it is a session and the clock is at
    or past 16:30 ET; otherwise the previous session.
    """
    import pandas as pd

    now_et = (now if now is not None else datetime.now(ET)).astimezone(ET)
    today = now_et.date()
    cal = get_xnys()
    if cal.is_session(pd.Timestamp(today)) and now_et.time() >= FINAL_BAR_TIME:
        return today
    return latest_session_on_or_before(today - timedelta(days=1))


def latest_session_on_or_before(d: date) -> date:
    cal = get_xnys()
    import pandas as pd

    return cal.date_to_session(pd.Timestamp(d), direction="previous").date()
