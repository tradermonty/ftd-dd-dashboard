# ftd-dd-dashboard

History of Follow-Through Day (FTD) and Distribution Day (DD) signals for the S&P 500
(`^GSPC`) and NASDAQ Composite (`^IXIC`), replayed session by session with the decision
logic of the `claude-trading-skills` skills (`ftd-detector`, `ibd-distribution-day-monitor`).

**Local use only. Do not publish, push, or redistribute the data or generated output.**
FMP's terms of service (sections 2.2.2 and 2.6.1) do not allow public display or
distribution of its price and volume data (or data derived from it) without written
permission. Until that is cleared, the repo holds code only; `data/raw/` and `build/`
are git-ignored. See `docs/plan.md` section 0 and risk R7.

Definitions are those of the skills, not official IBD signals.

## Status

- Implemented: vendor + loader, fetch + replay + dd_glue, events and outcomes, research R
  and R2, validation and `publish.py`, the Japanese / English site, the daily job scripts
  (`scripts/run_daily.sh`, `launchd/` template; not installed).
- The vendored skills are still the 2026-11-08 version (`vendor/VENDOR.lock`). Updating to
  claude-trading-skills `e1e33d16` (stateful replay, IBD-style market status) is ticket P2.
- Work is tracked as tickets in `tasks/` (task-runner layout; open `tasks/dashboard.html`).

## Setup

```bash
uv venv && uv pip install -e '.[dev]'
echo 'FMP_API_KEY=...' > .env        # git-ignored
python scripts/run_replay.py         # fetches FMP into data/raw/ (git-ignored)
python scripts/make_fixtures.py      # builds tests/fixtures/bars.json from data/raw/ (git-ignored)
```

Neither `data/raw/` nor the test fixture is in git: both hold FMP price data. Tests that need
the fixture fail until it is built locally.

## Commands

```bash
# Re-vendor decision modules from a claude-trading-skills checkout (never edit vendor/ by hand)
python scripts/sync_vendor.py --src ../claude-trading-skills --ref <git sha>

# Fetch (14 API calls, cached in data/raw/) and replay every session -> build/daily_status.csv
python scripts/run_replay.py            # add --use-cache to reuse data/raw/

pytest -q && ruff check .
```

Tests make no network calls. Equivalence tests compare against the original skill modules
extracted with `git archive` at the vendored ref from `$CTS_CHECKOUT` (default
`~/PycharmProjects/claude-trading-skills`); they skip with a reason if it is unavailable.
