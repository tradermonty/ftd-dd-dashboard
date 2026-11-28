# ftd-dd-dashboard

History of Follow-Through Day (FTD) and Distribution Day (DD) signals for the S&P 500
(`^GSPC`) and NASDAQ Composite (`^IXIC`), replayed session by session with the decision
logic of the `claude-trading-skills` skills (`ftd-detector`, `ibd-distribution-day-monitor`).

**Dashboard: https://tradermonty.github.io/ftd-dd-dashboard/** (English / 日本語, updated
after each US close)

The `main` branch holds code only: FMP price data stays out of git (`data/raw/` and `build/`
are git-ignored). The dashboard on the `gh-pages` branch is built by
`scripts/publish_pages.sh`. See `docs/plan.md` section 0 and risk R7 for FMP's terms.

Definitions are those of the skills, not official IBD signals.

## Status

- Implemented: vendor + loader, fetch + replay + dd_glue, events and outcomes, research R
  and R2, validation and `publish.py`, the Japanese / English site, the daily job scripts
  (`scripts/run_daily.sh`, `launchd/` template).
- Public dashboard: `pages/` (replay, data builder, page check, the mobile pages) and
  `scripts/publish_pages.sh`, run on weekdays by GitHub Actions (`.github/workflows/daily-pages.yml`).
  It judges FTDs with the ftd-detector scripts of claude-trading-skills `58108806`
  (`pages/skill/`); distribution days come from the research build.
- The research build still vendors the 2026-11-08 skills (`vendor/VENDOR.lock`); updating it
  is open work.

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
