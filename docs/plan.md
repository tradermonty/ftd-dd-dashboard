# ftd-dd-dashboard implementation plan

Written: 2026-11-07 (Sat)
Revision: 3rd. The 2nd revision took in the 13 findings of an independent review. The 3rd
(2026-11-28) translates the plan into English and brings sections 0, 2, 5 and 6 up to date with
the public dashboard. The Japanese original, including the wording that the research pages
quote as "written before the computations", is in git history (`git show ff8fbe9:docs/plan.md`).
Repository: `tradermonty/ftd-dd-dashboard` (public)
Site: `https://tradermonty.github.io/ftd-dd-dashboard/`

## 0. Decisions

| Item | Decision |
|---|---|
| Indexes | `^GSPC` (S&P 500) and `^IXIC` (NASDAQ Composite), as IBD uses |
| Period | Published from 2000-01-03. Fetched from 1999-06-01 so the judgment has a run-up |
| Languages | Japanese and English. Pages under `/ja/` and `/en/`; CSVs and column names are English for both |
| Updates | Once a day after the US close, weekdays only |
| Publication | **Public since 2026-11-28.** FMP's terms 2.2.2 and 2.6.1 require written permission to display or redistribute its data publicly (checked in the terms on 2026-11-07). FMP had not answered by 2026-11-28, and the owner decided to publish anyway so the dashboard can be used and evaluated daily, accepting that risk (R7). The repository is public and `main` holds code only: FMP prices and the CSVs derived from them never go into `main` (`data/raw/`, `build/`, `tests/fixtures/bars.json` are git-ignored). The site is the `gh-pages` branch, rebuilt every weekday by GitHub Actions (section 5.2) |
| Name | ftd-dd-dashboard. Site heading "Follow-Through Day & Distribution Day Dashboard" |

## 1. Facts checked first

Checked 2026-11-07 16:25-16:55 by reading the source, querying FMP, and a trial replay with the
real `rally_tracker.py`. Line numbers refer to the claude-trading-skills ref vendored then.

### 1.1 FTD judgment (`skills/ftd-detector/scripts/`)

- `rally_tracker.get_market_state(sp500_history, nasdaq_history)` (line 506) takes newest-first
  histories, sorts them oldest-first internally (lines 523-524) and calls `analyze_single_index()`.
- The production skill fetches 80 bars (`ftd_detector.py` lines 72 and 83), but
  `analyze_single_index()` judges only the latest **60** (`rally_tracker.py` lines 384-386). The
  post-FTD monitor `post_ftd_monitor.assess_post_ftd_health()` uses all 80 (`ftd_detector.py`
  lines 148-151).
- The high that anchors a swing low is searched at most 40 bars back (`rally_tracker.py` line
  58). When the window moves, the swing low can move, so an FTD can appear late (section 3.3).
- FTD rule: day 4-10 of a rally, a gain of +1.25% or more (`FTD_GAIN_MINIMUM`, line 42), and
  volume above the previous day. The 50-day average volume is computed inside the window only
  (line 325), so an FTD near the start of the window averages fewer than 50 bars.
- A correction is a decline of 3% or more over 3 or more down days (lines 35-37). This differs
  from IBD and yields far more FTDs than IBD: 98 for `^GSPC` in 2006-2026, about 5 a year.
- The invalidation date is not in the output of `analyze_single_index()`; it comes from
  `post_ftd_monitor.check_ftd_invalidation(history, ftd_idx)` (line 74).
- The production skill uses `QQQ` instead of the NASDAQ index (line 83) and has no `--as-of`.
  The quote is display-only and is not appended to the history.
- `rally_tracker.py` and `post_ftd_monitor.py` import only the standard library.

### 1.2 Distribution-day judgment (`skills/ibd-distribution-day-monitor/scripts/`)

- `distribution_day_tracker.detect_distribution_days()` (line 21) and `enrich_records()` (line
  58). Input is newest-first (file header). `enrich_records()` mutates the records it is given.
- Rule: -0.2% or worse from the previous close (`models.py` line 16) with volume above the
  previous day. Expires when `age > 25` (`models.py` line 17, tracker line 87). The DD day counts
  as 0, so it expires on the 26th session. Excluding the DD day, it is invalidated if the high
  rises 5% before it expires.
- Production counts active DDs with `count_active_in_window(records, 25)` (`ibd_monitor.py`
  line 237) and decides whether today is a DD with `dd_index == 0 and status == "active"` (line
  246).
- Production fetches `lookback + 5` = 85 bars (`ibd_monitor.py` lines 352 and 363). Default
  symbols are `QQQ,SPY`, `lookback_days` 80. A DD judgment needs about 27 bars, with no look-ahead.
- `distribution_day_tracker.py` does `from models import ...` (line 16). `ibd_monitor.py`
  rewrites `sys.path` on import (line 31) and imports `yaml` and a `report_generator` with the
  same name as the FTD side's.

### 1.3 Data (FMP)

- `GET /stable/historical-price-eod/full?symbol=...&from=...&to=...` returns `^GSPC` and `^IXIC`
  with volume from 1990-02-01. `QQQ` is empty before 1999.
- **One response is cut at 5,000 rows and returns the newest 5,000.** Asking for 1999-06-01 to
  2026-11-07 returned only 2006-12-21 to 2026-11-06. A 4-year range is about 1,005 rows.
- In 1999-2003 neither index has a zero or empty volume.
- Some rows repeat the previous day's volume exactly: 10 places for `^GSPC`, e.g. 2011-03-25,
  04-04, 04-18 and 05-04, all `4223740000`. An equal value makes "volume above the previous day"
  false and so affects the judgment.
- How well old index volumes agree with other sources is unchecked (R1).
- Whether FMP's terms allow redistributing prices and volumes was not checked at this point (R7).

### 1.4 Trading calendar

- `exchange-calendars>=4.5,<5` XNYS, as market_breadth uses.
- By default it holds sessions only from 2006-11-07 (checked on 4.13.2). Pass the start:
  `get_calendar("XNYS", start="1999-01-01")`. With it, 1999-06-01 to 2003-05-30 is 1,005
  sessions, matching FMP's row count.

### 1.5 How market_breadth publishes

The approach of `~/PycharmProjects/market_breadth/.github/workflows/daily-market-breadth.yml` and
`docs/publication_gate.md` is to be reused:

- Every run writes a fresh directory and uploads it only if validation passes. On failure the
  deploy job does not run and the previous version stays up.
- `validation.json` records pass/fail, the market date, the source commit and file hashes.
- A keepalive job adds an empty commit after 30 days without commits; `concurrency: group: pages`
  is carried over too.

As built (section 5.2), the dashboard workflow keeps the "push only after the checks pass" rule
but has no keepalive job, and its concurrency group is `daily-pages`.

## 2. Layout

```
ftd-dd-dashboard/
├─ vendor/                   research build: skills at the ref in VENDOR.lock
│  ├─ ftd_detector/          rally_tracker.py, post_ftd_monitor.py
│  ├─ ibd_dd/                distribution_day_tracker.py, models.py, history_utils.py,
│  │                         risk_classifier.py, math_utils.py
│  └─ VENDOR.lock            source commit SHA and SHA-256 of each file
├─ engine/
│  ├─ loader.py              loads the vendored modules
│  ├─ fetch.py               fetches FMP in ranges, joins and checks them
│  ├─ replay.py              re-judges one session at a time
│  ├─ dd_glue.py             copy of the DD-counting part of production ibd_monitor.analyze_index
│  ├─ events.py              extracts events from the judgments
│  ├─ outcomes.py            returns after events and their statistics
│  └─ retention.py           keeps or deletes old runs (section 5.1)
├─ research/                 research phases R and R2 (sections 3.6, 3.7)
├─ webgen/, site/templates/  the research site (pages, Japanese/English wording) and its checks
├─ pages/                    the public dashboard (section 5.2)
│  ├─ skill/                 ftd-detector scripts at the ref in SOURCE.json (dashboard only)
│  ├─ skill_dd/              ibd-distribution-day-monitor at the same ref (dashboard only)
│  ├─ replay.py              judges every session since 2000 with both
│  ├─ build_data.py          mock_data.js (top page) and history_data.js (look-back page)
│  ├─ check.py               opens the 4 pages at phone width
│  └─ site/                  the mobile pages (/, /ja/, /en/)
├─ scripts/
│  ├─ sync_vendor.py         vendors from claude-trading-skills and updates VENDOR.lock
│  ├─ run_replay.py, build_events.py, run_research.py
│  ├─ publish.py             build and validate the research build (section 5)
│  ├─ run_daily.sh           the validated daily build
│  └─ publish_pages.sh       run_daily.sh, then the dashboard, then the push to gh-pages
├─ launchd/                  Mac templates (section 5.1)
├─ tests/
└─ .github/workflows/
   └─ daily-pages.yml        weekday build and push to gh-pages (section 5.2)
```

### 2.1 How the judgment logic is kept

- The rules are not rewritten; the skill files are copied, so the site and the skill cannot
  disagree.
- `scripts/sync_vendor.py --ref <commit>` vendors them and records the commit and hashes in
  `VENDOR.lock`. Tests check the copies against the lock.
- Only the files in section 2 are copied; `ibd_monitor.py`, `fmp_client.py` and
  `report_generator.py` are not.
- Loading:
  - FTD side: read straight from the file with `importlib.util.spec_from_file_location`.
  - DD side has `from models import ...`, so `vendor.ibd_dd.models` is registered as
    `sys.modules["models"]` before `distribution_day_tracker` is loaded.
- `dd_glue.py` copies production `analyze_index`'s counting, so a test checks that it matches
  production `analyze_index` on the same data.
- The public dashboard judges with a newer ref, claude-trading-skills `58108806`: FTDs and rally
  attempts with the ftd-detector scripts (`pages/skill/`), distribution days, d5/d15/d25 and
  risk levels with production `ibd_monitor.analyze_index` of ibd-distribution-day-monitor
  (`pages/skill_dd/`, 85 bars newest-first, `as_of=None`). That ref compares volume with the
  last full session around an early close (#892), so 12 DDs per index since 2000 that the
  vendored ref finds on the session after an early close are not DDs on the dashboard. Bringing
  `vendor/` up to the newer skills is open work.

## 3. Processing

### 3.1 Fetch (fetch.py)

- 1999-06-01 to the run date in 7 ranges of 4 years: 14 requests for the two indexes.
- Each range must return fewer than 5,000 rows; exactly 5,000 is taken as truncated and fails.
- After joining: no duplicate dates, and the dates match XNYS sessions exactly.
- No rows are dropped. Dropping one turns "the previous day" into two days back and changes the
  rally day count. Zero or empty volumes and volumes equal to the previous day are only recorded
  in `data_issues.csv`; whether to exclude them is left to how the production skill treats them.

### 3.2 Replay (replay.py)

For each session t, only data up to t is used.

- **FTD:** the latest 80 bars ending at t, newest-first, into `get_market_state()`, then
  `assess_post_ftd_health()` on the same 80 oldest-first. Same order as production
  `ftd_detector.py`.
- **Distribution days:** the latest 85 bars ending at t through `detect_distribution_days()` ->
  `enrich_records()` -> `dd_glue`. `enrich_records()` mutates its input, so each day gets fresh
  records.

Each day's state is one row of `daily_status.csv`. A review trial showed 5,000 days replay in
seconds.

### 3.3 Events (events.py)

**FTD**
- An event is the pair (index, `ftd_date`).
- Two date columns:
  - `ftd_date`: the day judged to be the FTD.
  - `detected_on`: the first day in the replay on which that FTD appeared.
- `lag_sessions` is the number of sessions from `ftd_date` to `detected_on`.
  - The review trial found a few lags, all just after the start of the data, where the 80-bar
    window was not yet full.
  - In the full replay fetched from 1999-06 (P3) there were no lags in 2000-2026.
  - FTDs dated before the replay start cannot be detected point-in-time and are dropped.
- FTDs already `FTD_INVALIDATED` on `detected_on` are flagged `invalidated_at_detection = true`.
  The full P3 replay had none.
- A state change caused by the FTD leaving the 60-bar window is not an event.

**FTD invalidation**
- Not taken from state changes, because of late detection and window movement.
- `check_ftd_invalidation(full_chrono, idx_of(ftd_date))` on the full history gives whether and
  when the FTD was invalidated within 60 sessions.

**Distribution days**
- A DD is a day whose own judgment has `dd_index == 0`.
- Expiry and invalidation dates come from the judgments of later days.

### 3.4 Outcomes (outcomes.py)

- Measured **from the close of `detected_on`**. Measuring from `ftd_date` would use information
  not yet visible. The `ftd_date`-based values are also output, under other column names.
- Per FTD: returns after 5, 10, 20 and 60 sessions, the maximum drawdown within 60 sessions, and
  whether it was invalidated within 60 sessions.
- Returns after 20 and 60 sessions grouped by active-DD count 0-3, 4, 5, 6+.
- Statistics:
  - Count, median and interquartile range; never the mean alone.
  - Per index. S&P 500 and NASDAQ FTDs overlap in time and are not pooled.
  - Daily groupings overlap, so both a thinned non-overlapping sample and a block method are shown.

### 3.4.1 Distribution-day risk level (added after P3, before P5)

- Production gives each index one of NORMAL, CAUTION, HIGH, SEVERE
  (`risk_classifier.classify_risk()`), with thresholds from `models.RiskThresholds` and
  `risk_thresholds` in `config/default.yaml`:
  - SEVERE: 6+ in 25 days, or 4+ in 15 days, or close below both the 21EMA and the 50SMA with 5+
    in 25 days (`ibd_monitor.py` lines 159-160)
  - HIGH: 5+ in 25 days, 3+ in 15 days, or 2+ in 5 days
  - CAUTION: 3+ in 25 days
  - NORMAL: otherwise
- `risk_classifier.py` and `math_utils.py` are vendored. The moving-average test
  `_compute_ma_filters()` (`ibd_monitor.py` line 125) is copied into `dd_glue.py` and tested
  against production `analyze_index`'s `risk`. The EMA depends on the bar count, so it uses the
  same 85-bar window as production.
- `daily_status.csv` gains `risk_level` and `market_below_ma`.
- The chart shades the background by daily risk level, merging runs of equal level into one band;
  NORMAL is unshaded. CAUTION light yellow, HIGH light orange, SEVERE light red. A legend, and a
  hover showing each day's d5/d15/d25 and level.
- The statistics page adds 20- and 60-session returns by risk level, presented as in section 3.4.

### 3.5 Comparing correction depths (extension of outcomes.py)

Aim: the skill's "3% decline" is only a number chosen in place of IBD's human judgment of a
"correction". Show how the FTD count and outcomes change with that number, so the difference in
definitions is visible in numbers.

- Depths: 3% (skill default), 5%, 8%, 10%.
- Mechanism:
  - The depth is the module constant `MIN_CORRECTION_PCT` of `rally_tracker.py` (line 35), read
    on every judgment (line 72).
  - Vendor files are not edited. `loader.py` loads `rally_tracker` as a separate module per depth
    and overrides only that constant; the other constants stay at their defaults.
  - The replay of sections 3.2-3.4 runs in full per depth: four times the work, expected to take
    tens of seconds.
- Output:
  - Per depth: FTD count, count per year, median and IQR of outcomes, invalidation rate within 60
    sessions.
  - Which FTDs survive to which depth. A different depth changes the anchoring swing low and
    shifts the rally day count, so a deep setting's FTD can fall on a day the shallow one lacks.
    Count same-date matches, deep-only and shallow-only separately.
- Publication:
  - 3% is the skill's judgment and appears in the event list and the chart.
  - 5%, 8% and 10% appear only in the statistics comparison, labelled "a research setting, not
    the skill's definition".
- Caveat: the anchoring high is searched only 40 bars back and the judgment window is 60 bars
  (section 1.1), so an 8-10% decline taking longer than 40 bars may not count as a correction.
  Deeper settings are biased toward fewer FTDs; say so on the statistics page and label settings
  with fewer than 10 FTDs "for reference".
- As built, this comparison is part of research R (`research/variants.py`, `CORRECTION_PCTS`);
  no separate `ftd_depth_variants.csv` is produced.

### 3.6 Research phase R: refining the signals (before P4)

Background (checked 2026-11-07, computed from the CSVs in `build/`):
- About 20% of days are DDs (^GSPC 19.8%, ^IXIC 20.5%), about 5 per 25 days on average, so the
  SEVERE threshold "6+ in 25 days" sits just above the average. d25 is 6+ on about 30% of days.
- The share of days followed by a 10%+ decline within 60 days: ^GSPC NORMAL 12.0%, SEVERE 19.7%.
  ^IXIC shows no difference (26.4% and 23.5%).
- Returns in the 60 days after an FTD have the same median as all days and a wider spread.

These background figures were computed over the whole of 2000-2026, which includes the test
period chosen later (2015 onward). So statistics including the test period were seen before R
was designed. This is the third exception of "having seen the test period", after the tie-break
rule and the R2 preview (corrected in the re-review of 2026-11-07; see "Re-review corrections
(R)" below). The figures are recorded in `research/contamination.py`, and the publish gate
recomputes them from `daily_status.csv` and checks they match.

Goal: keep the skill's definition as the baseline, compare variants against it, and move toward
signals usable for defensive decisions. A good variant becomes an improvement proposal for the
claude-trading-skills skill.

**Rules against overfitting (fixed in advance)**
- Split the period. Selection period 2000-01-03 to 2014-12-31; test period 2015-01-02 to
  2026-11-06. The end of the test period is the date fixed as the constant `TEST_END` in
  `research/split.py`, not the newest data date (same for R and R2). Sessions after 2026-11-06
  are not in the test period and go only into the "forward record".
- Variants are chosen on selection-period results only. Test-period results are produced once,
  for the baseline and the chosen variants, and nothing is changed after seeing them.
- Every variant tried is recorded and reported.
- Each index is evaluated separately, never pooled.

**Re-review corrections (R, 2026-11-07)**
- The test period end is fixed at `TEST_END = 2026-11-06`. The publish gate checks that the
  recorded end equals this constant.
- R also has a "forward record": the baseline and chosen variants (A, B, C) computed with the
  test-period code on sessions after `TEST_END`. Until there is data it shows "not started". A
  and C count only windows with 60 sessions ahead, so they show numbers later than B.
- The background above came from whole-period statistics including the test period, so as the
  third exception it is noted on the research page, in `report.html`, in `selected.json`'s
  `test_period_contamination` and in the Home key-point note. The three exceptions are the
  tie-break rule, the R2 preview, and these whole-period statistics.
- Exact history of the variant grid: the grid was written in this document before the
  computations. But the hash-frozen file `research/grid_freeze.json` was created after R's
  `variants.csv` had already come out of several runs (4 by computation, the first
  20261108T032834Z) and after R2's `overlay_variants.csv` had come out (3 runs). Outputs from
  runs before the freeze are byte-identical for R2. R's `variants.csv` differs only in the header
  line, from renaming the column `min_decline_pct` to `min_decline_frac`; every data row is the
  same. This tally was computed from the files and recorded in `grid_freeze.json`'s
  `provenance`. The earlier wording "frozen, then computed" did not match this history and was
  corrected.
- Dates and times: the registration date 2026-11-07 is the local date (Pacific, UTC-8); the hash
  file was created at 2026-11-08T04:06:20Z UTC. They are the same moment (2026-11-07 20:06:20
  PST), which is why the UTC date is a day later.
- `avg_exposure`: B's "average exposure" JSON key and CSV column were renamed from
  `time_invested` to `avg_exposure` (the old name read as "share of days invested"). Readers of
  old runs read `time_invested` as `avg_exposure`.

**Variants**
- DD conditions:
  - Decline: -0.2% (baseline), -0.5%, -1.0%
  - Volume: above the previous day (baseline), above the 50-day average, both
  - Risk basis: the skill's fixed thresholds (baseline), or d25's position in its trailing 5-year
    distribution (top 20%, 10%, 5%), computed from data before that day only
  - Decline is varied through the vendored `DistributionDayRule.min_decline_pct`. The volume
    conditions are not in the vendored functions, so research code implements them, tested to
    match the vendor at the baseline setting
- FTD conditions:
  - Correction depth: 3% (baseline), 5%, 8%, 10% (same mechanism as section 3.5)
  - Minimum gain: 1.25% (baseline), 1.5%, 2.0% (`FTD_GAIN_MINIMUM` overridden the same way)
  - Whether volume above the 50-day average is required

**Evaluation**
- A. FTD as a trade:
  - Buy at the close of detected_on.
  - Sell at the close on the day the close breaks the FTD day's low (same as
    `check_ftd_invalidation`).
  - Otherwise sell at the close 60 sessions later.
  - Output: count, win rate, median and mean P&L per trade, bottom and top 10%, profit factor.
- B. DD risk as exposure control:
  - On days at or above the risk threshold, exposure goes to 0% or 50% from the next day;
    otherwise 100%.
  - The signal is set at that day's close and applies from the next day's move. Cash earns 0.
  - Output: annualized return, maximum drawdown, volatility, share of time invested, next to buy
    and hold.
- C. As events: the share followed by a 10%+ decline within 60 days, per risk level (as in the
  current tables).

**Output**
- Per-variant result CSVs and the list of variants tried, in `build/research/`.
- A local research report in HTML: baseline, chosen variants, and the test-period results side
  by side.

**Done when**
- Baseline results equal P3's results from the vendored code.
- No computation uses data after its day (tested).
- Selection and test periods are separated in the code.

### 3.7 Research phase R2: leverage overlay after an FTD (after P5)

The owner's hypothesis (2026-11-07): hold the S&P 500 at 100% always, raise exposure on margin
when an FTD appears, and go back to 100% when distribution days pile up after the FTD; this
might add excess return over buy and hold.

Preview result (2026-11-07, rules fixed first, computed once, with the entry condition "risk is
not HIGH or SEVERE"):
- 150%, 5% annual borrowing rate, back to 100% on SEVERE. ^GSPC: 2000-14 annualized 2.1%,
  below buy and hold's 2.3%; 2015-26 11.4%, the same as buy and hold. ^IXIC: 2000-14 -0.8%,
  below buy and hold's 0.9%; 2015-26 17.2%, above buy and hold's 14.9%.
- Leveraged on only 10-20% of days. At an FTD the risk level is often already high from the DDs
  accumulated during the correction, so there are few entries.
- So R2 exits on "the number of DDs counted after the FTD" instead of the 25-day risk level.

**Rules against overfitting** (as in section 3.6)
- Selection period 2000-01-03 to 2014-12-31; test period 2015-01-02 to 2026-11-06 (the fixed
  `TEST_END`, as in section 3.6). The test period is produced once, for the baseline and the
  chosen variants.
- Only the 12 variants below, all recorded. Each index evaluated separately.

**Variants (12)**
- Entry: raise exposure at the close of the FTD's detected_on (applies from the next day). No
  risk condition. A new FTD during an overlay does nothing.
- Exit: back to 100% at the close of the day any of these happens (applies from the next day):
  - k DDs counted after the FTD day (k = 3, 4, 5)
  - the FTD is invalidated (close below the FTD day's low, as `check_ftd_invalidation`)
  - invalidation used as an exit or not: 2 ways
- Exposure: 125%, 150%
- 3 x 2 x 2 = 12. Baseline: buy and hold at 100% with no overlay.

**Costs and data**
- Borrowing rate: FRED's effective federal funds rate (`DFF`, daily) plus 1.5% a year, cached
  locally.
- For comparison, fixed rates of 0%, 3% and 6% a year.
- The indexes are price indexes without dividends; the overlay's dividends are missing too, so
  the overlay effect comes out slightly low.

**Evaluation**
- Annualized return, maximum drawdown, volatility, share of days leveraged, number of overlays.
- Compared with:
  - buy and hold at 100%
  - always borrowing at the same average exposure (does timing matter?)
- Risk: the largest fall of equity during an overlay. At 150%, an index fall of about 33% loses
  half the equity. As a margin-call guide, whether equity fell below a set ratio on any day; the
  ratio is a stated placeholder in the report.
- A chapter in the research report; the chart shows overlay periods as bands.

**Done when**
- The no-overlay setting equals the buy-and-hold computation.
- No decision uses data after its day (tested).
- Selection and test periods are separated in the code.
- The report says this is not a recommendation to trade.

**Re-review corrections (2026-11-07)**
- The preview above saw the 2015-26 test period before R2 was designed, so R2's test-period
  results are not an out-of-sample check. The grid was written in this document before R2's own
  computations, but that alone does not make the test period untouched. The hash freeze came
  after 3 runs that produced R2's `overlay_variants.csv` (see "Re-review corrections (R)" in
  section 3.6). The preview and its numbers are recorded in overlay_selected.json's
  `test_period_contamination` and shown on the research page and in report.html's R2 chapter.
- Sessions after 2026-11-06 are treated as a period nobody has seen. The research page has a
  "forward record" table computing the baseline and the chosen variants with the same code from
  2026-11-09. Until there is data it shows "not started".
- The tie-break rule (selected.json's `tie_break_affected_picks` records the old rule's pick and
  the current one) and the borrowing-rate lookup (FRED publishes each day's value on the next
  business day, so the newest value dated on or before the business day before the previous day
  is used) were changed after seeing results. Both are recorded as post-hoc changes, with their
  computed effect.
- The publish gate re-runs the selectors, re-renders every page and recomputes the result files,
  and stops on any difference from the record.
- The recomputation is complete. From the cached prices it rebuilds the replay, events, outcomes,
  and DD and FTD statistics (`daily_status.csv`, `ftd_events.csv`, `distribution_days.csv`,
  `ftd_outcomes.csv`, `rally_attempts.csv`, `dd_bucket_stats.csv`, `risk_level_stats.csv`) and
  every file of research R and R2 (all rows of `variants.csv`, `selected.json`,
  `test_results.csv`, `overlay_*`, `report.html`), and requires them to equal the run being
  published exactly. Only generation times are ignored (`summary.json`'s `generated_at` and
  report.html's "generated"). It takes about 25 seconds. R2's rates come from that run's copy of
  `research/fred_DFF.csv`.
- The rate data (FRED `DFF`) is re-fetched when its last observation is earlier than the rate
  date the last return needs (the third-last price session, the same basis as the gate's
  `rate_data` check), or when the file is more than 3 days old. A gap of up to 3 business days
  carries the previous rate forward (the gate warns); a longer gap stops. If a re-fetch still
  leaves a gap over 3 business days, it stops (the 2026-11-26 code audit aligned the fetch path
  and the recomputation path). `--use-cache` does not fetch, as for FMP, so stale data stops with
  a clear message; `--refresh-fred` allows FRED alone. The gate's `rate_data` checks that the
  copy has the rate the last return needs: carry-forward of up to 3 business days warns, more or
  none fails.
- Vendor files are checked against the `VENDOR.lock` hashes and also byte for byte against
  `git show <ref>:<source>` in the upstream checkout (default
  `~/PycharmProjects/claude-trading-skills`, changed with `CTS_CHECKOUT` or `--upstream`).
  Without the upstream checkout it records SKIP with the reason.

## 4. Published files

### 4.1 Data (`/data/`)

Planned, not yet published (ticket "publish the FTD and DD detection history as CSV").

| File | Content |
|---|---|
| `daily_status.csv` | Date, index, change, volume change, DD or not, active DD count, FTD state. Closes and volumes themselves were to follow R7; the public dashboard's data files already carry closes, lows and volume changes since 2026-11-28 |
| `ftd_events.csv` | Index, rally day 1, `ftd_date`, `detected_on`, `lag_sessions`, rally day, gain, volume change, above the 50-day average, `invalidated_at_detection`, invalidation date |
| `distribution_days.csv` | Index, date, decline, volume change, expiry date (26th session), invalidation date and reason |
| `ftd_outcomes.csv` | Outcomes after each FTD |
| `summary.json` | Summary statistics, data period, generation time, vendor source commit |
| `data_dictionary.md` | Column meanings and judgment rules, in English |
| `data_issues.csv` | Rows with zero, empty, or repeated volume |

`llms.txt` at the site root is the first thing an AI reads: the URL of each file and
`data_dictionary.md`. The research site generates it (`webgen/`); the public dashboard does not
have one yet.

### 4.2 Pages (`/ja/` and `/en/`)

The research site (`webgen/`, built into `build/current`, local):

| Page | Content |
|---|---|
| Top | Today's state, active DD count, recent events |
| Chart | Price and volume with marks for FTDs, rally day 1, DDs and invalidations; switchable ranges |
| Events | FTDs and DDs, sortable and filterable |
| Statistics | The tables of section 3.4 and the depth comparison of section 3.5 |
| Definitions | The rules with diagrams and examples. States that **these are judgments by the claude-trading-skills skills' definitions, not IBD's official FTD**, and the main differences from IBD |
| Data | CSV downloads and column descriptions |

- The site root `/` lets the reader choose a language.
- Wording lives in `site/templates/i18n/ja.yaml` and `en.yaml`; a test checks the keys match.

The public dashboard (`pages/site/`) is smaller: a top page and a look-back page per language,
built for phones (section 5.2).

## 5. Build and validation (publish.py)

`publish.py --output-dir <new directory>` builds and validates, and returns exit code 0 only on
PASS.

Checks:
- The newest date equals the latest XNYS session at run time.
- Both indexes are present and match the sessions exactly.
- CSV columns match the definitions in `data_dictionary.md`.
- Row counts of `ftd_events.csv` equal the counts in `summary.json`; the same for DDs.
- The `VENDOR.lock` hashes match.
- **Difference from the previous published version.** Take the previously published
  `ftd_events.csv` and `distribution_days.csv` and count events other than on the newest date
  that were added, removed or changed, since FMP corrections to old data change old events. The
  difference is recorded in `validation.json`; more than 5 fails. The limit will be reviewed in
  operation. On exceeding it, a clear message goes to `logs/needs_review.log` and
  `build/NEEDS_REVIEW` is created; the next passing run removes it. Meanwhile `build/current`
  stays at the previous version. On GitHub Actions each run starts with no previous version, so
  this check has nothing to compare against there.
- **A browser check that cannot run fails.** The Python with Playwright is `PLAYWRIGHT_PYTHON`,
  or else `python3` on PATH and similar, tried with `import playwright`. If none is found the
  result is FAIL, not SKIP. `--allow-no-browser` allows it only on purpose.

### 5.1 Running on the Mac

- The plan was for launchd to run `scripts/run_daily.sh` at 15:00 on weekdays (Pacific; the Mac
  clock is Pacific), which is 18:00 Eastern, two hours after the close. Pacific and Eastern
  switch daylight saving on the same day, so it is 18:00 Eastern all year. Whether the wait is
  long enough is to be checked over the first two weeks.
- As installed on 2026-11-28, only the pages job (`com.ftd-dd-dashboard.pages`) is loaded; it
  runs `run_daily.sh` through `publish_pages.sh` at the same time. The daily plist is a template
  that has not been installed. Once the pages job is removed (section 5.2), nothing runs on the
  Mac on a schedule.
- Output goes to `~/PycharmProjects/ftd-dd-dashboard/build/`: each run in `build/runs/<time>/`,
  and `build/current` is repointed only on PASS. On FAIL the previous version stays.
- `FMP_API_KEY` is in `.env`, which is git-ignored. The same file may set `PLAYWRIGHT_PYTHON`.
- Templates: `launchd/com.ftd-dd-dashboard.daily.plist` (the validated build) and
  `launchd/com.ftd-dd-dashboard.pages.plist` (the public update of section 5.2); replace
  `__PROJECT_DIR__` before installing. Logs: `logs/daily-YYYYMMDD.log`, `logs/pages-YYYYMMDD.log`.
- Scheduled runs never pass `--no-site-check` or `--allow-no-browser`. `run_daily.sh` sets
  `FTD_DD_SCHEDULED=1`, and `publish.py` then refuses both with exit code 2. `--use-cache` is
  not passed either: every day fetches fresh prices.
- Retention: keep the last 10 passing runs and the one `build/current` points to; delete older
  ones. Failing runs are deleted after 7 days. Screenshots (about 50 MB) are not kept by default,
  only for a failed browser check, or with `--keep-screens` for that run. A run without images is
  about 6 MB.

### 5.2 The public dashboard

- `scripts/publish_pages.sh` runs `run_daily.sh`, then `pages/replay.py`, `pages/build_data.py`
  and `pages/check.py`. It replaces the tree of a `gh-pages` clone and pushes. Any failure leaves
  the site as it was.
- GitHub Actions (`.github/workflows/daily-pages.yml`) runs it on weekdays at 23:30 UTC, which
  is 18:30 Eastern in winter and 19:30 in summer. Each run fetches from FMP inside the runner;
  nothing is cached or uploaded as an artifact. The key is the repository secret `FMP_API_KEY`,
  and the push uses the workflow's `GITHUB_TOKEN`.
- The Mac launchd job `com.ftd-dd-dashboard.pages` ran the same script before Actions took over.
  It stays installed until the first scheduled Actions run (2026-11-30) succeeds, then it is
  removed.

## 6. Order of work

Each phase is test-first.

| Phase | Content | Done when | Status (2026-11-28) |
|---|---|---|---|
| P0 | FMP terms (R7). The 2026-11-07 search excerpt says public display or distribution of FMP data, and of data computed from it, needs a separate Data Display and Licensing Agreement, even free and non-commercial. On 2026-11-07 the owner wrote to FMP from a personal mailbox (as reported by the owner) | A written answer from FMP fixing what may be published | No answer yet. The owner published anyway on 2026-11-28 (section 0) |
| P1 | Repository, vendoring, loader | Both skills load in one process; the lock check passes | Done |
| P2 | fetch, replay, dd_glue | The newest day's judgment equals production functions given the same `^GSPC` and `^IXIC` data (not compared through the CLI, which is fixed to QQQ); `dd_glue` equals production `analyze_index` | Done |
| P3 | events, outcomes, the history | Every replay day equals the vendored code called directly on data up to that day. Differences from IBD's published dates are only reported | Done |
| R | Research phase (section 3.6), including the depth comparison of section 3.5 | Section 3.6's conditions | Done |
| P4 | CSVs, validation, publish.py | Validation tests with corrupted data all fail, including the previous-version difference | Done |
| P5 | Japanese and English site | All pages render in both languages and the wording keys match | Done |
| R2 | Research phase R2 (section 3.7), after P5. Not the same as risk R2 | Section 3.7's conditions | Done |
| P6 | Daily run and `build/current` replacement | Manual and scheduled runs move `build/current`; a failed validation keeps the previous version | Done; the public update runs on GitHub Actions (section 5.2) |
| P7 | Statistics page, depth comparison, llms.txt | Statistics counts equal the CSVs; the 3% setting equals the default P3 replay exactly; per-depth loads do not affect each other's constants | Partly: the research site has the statistics and llms.txt; the public dashboard has neither the `/data/` CSVs nor llms.txt |
| P8 | Announcement | Links from the claude-trading-skills guide page and README | Not started |

## 7. Risks

| ID | Risk | Mitigation |
|---|---|---|
| R1 | Quality of old index volumes is unknown. DDs and FTDs depend on day-over-day volume, so results can change | Compare a few days of 2000-2006 volume with another source; if they disagree, start publication later |
| R2 | FTDs appear late, some already invalidated when they appear | Output `detected_on` and `lag_sessions`; measure outcomes from `detected_on` (sections 3.3, 3.4) |
| R3 | Missing rows from FMP's 5,000-row limit | Row-count check per range and a session match fail the run |
| R4 | Skill updates change judgments | Vendor at a fixed commit and update by PR; the PR shows the difference in past events |
| R5 | Production skill and site use different symbols: the skill uses QQQ or SPY, the site the indexes | State it on the definitions page; whether to align the skill is Q1 |
| R6 | Misreading statistics: these FTDs are more frequent than IBD's and counts are small | Per index, with counts and ranges, non-overlapping samples; state that this is not IBD's official signal |
| R7 | Redistributing FMP data may breach its terms | Asked in P0. The site is public since 2026-11-28 with that risk accepted by the owner. If FMP objects: publish only judgments and ratios, draw charts from ratios, or use a source that allows it |
| R8 | FMP corrections to old data silently change past events | Difference check against the previous version (section 5) |
| R9 | Rows repeating the previous day's volume change judgments | Publish them in `data_issues.csv` and explain on the definitions page |
| R10 | Deep correction settings miss slow declines and undercount | Write the caveat of section 3.5 on the statistics page; label settings with fewer than 10 FTDs "for reference" |

## 8. Open questions

| ID | Question | Proposal |
|---|---|---|
| Q1 | Align the ftd-detector skill's QQQ to ^IXIC? | Discuss in a separate issue after the site is public |
| Q2 | Early-close days (e.g. the day after Thanksgiving) | Judge them as normal sessions and mark them in the event list |
| Q3 | Limit for the difference from the previous version | Start at 5 and review in operation |
