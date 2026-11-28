#!/bin/bash
# Daily public update of https://tradermonty.github.io/ftd-dd-dashboard/ (GitHub Actions:
# .github/workflows/daily-pages.yml; on the Mac: launchd/com.ftd-dd-dashboard.pages.plist).
#
#   1. scripts/run_daily.sh: fetch FMP, rebuild, validate; build/current moves only on PASS
#   2. pages/replay.py: judge every session since 2000 with pages/skill
#   3. pages/build_data.py: mock_data.js (top page) and history_data.js (look-back page)
#   4. pages/check.py: open all 4 pages at phone width; any script error stops the push
#   5. replace the gh-pages tree and push as tradermonty
#
# The public site carries FMP-derived prices. The owner decided on 2026-11-28 to publish
# before FMP answered the redistribution question (tasks: 20261126_11).
set -u
cd "$(dirname "$0")/.." || exit 1
ROOT="$(pwd)"
mkdir -p logs build/pages
LOG="logs/pages-$(date +%Y%m%d).log"
export PATH="/opt/homebrew/bin:/usr/local/bin:/usr/bin:/bin:${PATH:-}"
if [ -f .env ]; then set -a; . ./.env; set +a; fi
PY="$ROOT/.venv/bin/python"
PWPY="${PLAYWRIGHT_PYTHON:-python3}"
WT="$ROOT/build/pages/gh-pages"
OUT="$ROOT/build/pages/out"

run() {
    echo "== $(date '+%F %T %Z') pages run start"
    if [ "${SKIP_DAILY:-0}" != "1" ]; then
        ./scripts/run_daily.sh || { echo "run_daily.sh failed; site left as it was"; return 1; }
    fi
    "$PY" pages/replay.py build/pages/replay_daily.csv || return 1
    "$PY" pages/build_data.py build/pages/replay_daily.csv "$OUT" || return 1
    AS_OF=$("$PY" -c "import json,sys;t=open(sys.argv[1]).read();print(json.loads(t[t.index('=')+1:].rstrip().rstrip(';'))['as_of'])" "$OUT/mock_data.js")
    LAST=$(tail -1 build/pages/replay_daily.csv | cut -d, -f1)
    [ "$AS_OF" = "$LAST" ] || { echo "as_of $AS_OF != replay end $LAST"; return 1; }

    if [ ! -d "$WT/.git" ]; then
        rm -rf "$WT"
        git clone -q --branch gh-pages --single-branch https://github.com/tradermonty/ftd-dd-dashboard.git "$WT" || return 1
    fi
    git -C "$WT" fetch -q origin gh-pages && git -C "$WT" reset -q --hard origin/gh-pages || return 1
    git -C "$WT" rm -rq --ignore-unmatch . >/dev/null
    rm -rf "${WT:?}"/*
    cp -R pages/site/. "$WT"/
    cp "$OUT/mock_data.js" "$OUT/history_data.js" "$WT"/
    touch "$WT/.nojekyll"
    "$PWPY" pages/check.py "$WT" "$AS_OF" || { echo "page check failed; not pushed"; return 1; }

    git -C "$WT" add -A
    if git -C "$WT" diff --cached --quiet; then
        echo "no change to publish"
        return 0
    fi
    git -C "$WT" -c user.name=tradermonty -c user.email=tradermonty@users.noreply.github.com \
        commit -qm "data: sessions through $AS_OF" || return 1
    # The token travels in the environment of this one git call, never on a command line
    # (argv is visible to every local user through ps); the helper expands it itself.
    # GitHub Actions passes its GITHUB_TOKEN in PAGES_PUSH_TOKEN; on the Mac it is tradermonty's.
    if [ -z "${PAGES_PUSH_TOKEN:-}" ]; then
        PAGES_PUSH_TOKEN=$(gh auth token -u tradermonty) || { echo "no tradermonty token"; return 1; }
    fi
    PAGES_PUSH_TOKEN="$PAGES_PUSH_TOKEN" git -C "$WT" -c credential.helper= \
        -c 'credential.helper=!f() { echo username=x-access-token; echo "password=$PAGES_PUSH_TOKEN"; }; f' \
        push -q origin HEAD:gh-pages || return 1
    unset PAGES_PUSH_TOKEN
    echo "pushed $(git -C "$WT" log --oneline -1)"
}

{ run; code=$?; echo "== $(date '+%F %T %Z') exit code ${code}"; } >>"$LOG" 2>&1
find logs -name 'pages-*.log' -mtime +30 -delete 2>/dev/null
exit "${code}"
