"""Open the assembled Pages tree at phone width and fail on script errors or sideways overflow.

usage: python pages/check.py <site dir> <expected as_of date>
"""

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

root, as_of = Path(sys.argv[1]).resolve(), sys.argv[2]
bad = []
with sync_playwright() as p:
    b = p.chromium.launch()
    for lang in ("ja", "en"):
        for page in ("index.html", "history.html"):
            pg = b.new_page(viewport={"width": 390, "height": 844})
            errs = []
            pg.on("pageerror", lambda e, errs=errs: errs.append(str(e)))
            pg.goto((root / lang / page).as_uri())
            pg.wait_for_timeout(1500)
            info = pg.evaluate("""() => ({w: document.documentElement.scrollWidth,
                as_of: (window.MOCK || {}).as_of || null,
                text: document.body.innerText.length})""")
            ok = not errs and info["w"] <= 390 and info["text"] > 200
            if page == "index.html" and info["as_of"] != as_of:
                ok = False
                errs.append(f"as_of {info['as_of']} != {as_of}")
            print(("OK  " if ok else "BAD ") + f"{lang}/{page}", info, errs)
            if not ok:
                bad.append(f"{lang}/{page}")
            pg.close()
    b.close()
sys.exit(1 if bad else 0)
