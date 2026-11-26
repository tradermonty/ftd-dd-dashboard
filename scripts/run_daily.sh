#!/bin/bash
# Daily job for launchd (launchd/com.ftd-dd-dashboard.daily.plist): fetch the day's FMP data,
# rebuild everything into a new build/runs/<UTC time>/, validate it, and only on PASS repoint
# build/current. A failed run leaves the previous build/current in place.
#
# Rules for this script (docs/plan.md section 5.1):
#   * Never add --no-site-check or --allow-no-browser: they switch off the site, key-fact and
#     browser checks. FTD_DD_SCHEDULED=1 makes publish.py refuse both flags.
#   * Do not add --use-cache: the daily job must fetch fresh prices.
#   * The FMP key comes from .env (FMP_API_KEY). The same file may set PLAYWRIGHT_PYTHON, the
#     python3 that can `import playwright`; without it publish.py tries python3 on PATH and the
#     usual system locations, and a browser check that cannot run FAILS the run.
#   * Output goes to logs/daily-YYYYMMDD.log. When the history of already-detected events changed
#     more than the limit, publish.py also writes build/NEEDS_REVIEW and logs/needs_review.log.
set -u
cd "$(dirname "$0")/.." || exit 1
mkdir -p logs
LOG="logs/daily-$(date +%Y%m%d).log"

export FTD_DD_SCHEDULED=1
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
if [ -f .env ]; then
    set -a
    # shellcheck disable=SC1091
    . ./.env
    set +a
fi

{
    echo "== $(date '+%F %T %Z') daily run start"
    ./.venv/bin/python scripts/publish.py
    code=$?
    echo "== $(date '+%F %T %Z') exit code ${code}"
    if [ -f build/NEEDS_REVIEW ]; then
        echo "== build/NEEDS_REVIEW exists: the history of old events changed, see logs/needs_review.log"
    fi
} >>"$LOG" 2>&1

# keep one month of logs
find logs -name 'daily-*.log' -mtime +30 -delete 2>/dev/null
exit "${code}"
