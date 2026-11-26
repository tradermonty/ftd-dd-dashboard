#!/usr/bin/env python3
"""Open every page of a built site in headless Chromium and check it (system python3 + Playwright).

For each language x page: no console errors / page errors / failed requests, tables are not
empty, the chart pages render two Plotly charts. Also exercises the event-table sort and
filter and the chart range buttons. Full-page screenshots go to <out>/ (default: <run>/screens)
at desktop width, plus phone width for every page.

Usage:
    python3 scripts/check_site.py build/current/site [--out build/current/screens]
Exit code 0 when every check passes.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PAGES = ("index", "research", "chart", "events", "definitions", "data")
LANGS = ("ja", "en")


def check_page(browser, url: str, shot: Path, width: int, page_name: str) -> dict:
    ctx = browser.new_context(viewport={"width": width, "height": 900})
    page = ctx.new_page()
    errors: list[str] = []
    page.on(
        "console",
        lambda m: errors.append(f"console.{m.type}: {m.text}") if m.type == "error" else None,
    )
    page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
    page.on("requestfailed", lambda r: errors.append(f"requestfailed: {r.url} {r.failure}"))
    page.goto(url, wait_until="load")
    res: dict = {"url": url, "width": width}
    if page_name == "chart":
        try:
            page.wait_for_function(
                "document.querySelectorAll('.chart[data-ready=\"1\"]').length === 2", timeout=30000
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(f"chart not ready: {type(exc).__name__}")
        res["plotly_charts"] = page.locator(".js-plotly-plot").count()
        res["svg_traces"] = page.locator(".js-plotly-plot .scatterlayer path.js-line").count()
        if res["plotly_charts"] == 2 and width > 600:
            page.locator("g.rangeselector .button").nth(0).click()  # 6M
            page.wait_for_timeout(500)
    res["tables"] = page.locator("table").count()
    res["nonempty_tables"] = page.evaluate(
        "Array.from(document.querySelectorAll('table')).filter(t => t.tBodies[0] && t.tBodies[0].rows.length > 0).length"
    )
    if page_name == "events":
        first = page.locator("#tbl-ftd tbody tr").first.inner_text()
        page.locator("#tbl-ftd thead th").nth(1).click()  # sort by ftd_date asc
        asc_first = page.locator("#tbl-ftd tbody tr").first.inner_text()
        res["sort_changes_order"] = first != asc_first
        total = page.locator("#tbl-dd tbody tr").count()
        page.select_option("#f-index", "^GSPC")
        visible = page.locator("#tbl-dd tbody tr:visible").count()
        res["filter_rows"] = {"total": total, "visible_gspc": visible}
    page.screenshot(path=str(shot), full_page=True)
    res["errors"] = errors
    ok = not errors and res["tables"] == res["nonempty_tables"]
    if page_name == "chart":
        ok = ok and res.get("plotly_charts") == 2 and res.get("svg_traces", 0) >= 2
    if page_name == "events":
        ok = ok and res["sort_changes_order"] and 0 < res["filter_rows"]["visible_gspc"] < total
    if page_name in ("research", "events", "index"):
        ok = ok and res["tables"] > 0
    res["ok"] = ok
    ctx.close()
    return res


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("site", type=Path)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args(argv)
    site = args.site.resolve()
    out = args.out or site.parent / "screens"
    out.mkdir(parents=True, exist_ok=True)

    from playwright.sync_api import sync_playwright

    results = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for lang in LANGS:
            for name in PAGES:
                for width, suffix in ((1280, ""), (390, "_phone")):
                    url = (site / lang / f"{name}.html").as_uri()
                    shot = out / f"{lang}_{name}{suffix}.png"
                    results.append(check_page(browser, url, shot, width, name))
        shot = out / "root_chooser.png"
        results.append(check_page(browser, (site / "index.html").as_uri(), shot, 1280, "root"))
        browser.close()
    bad = [r for r in results if not r["ok"]]
    print(json.dumps(results, indent=1, ensure_ascii=False))
    print(f"{len(results) - len(bad)}/{len(results)} pages OK; screenshots in {out}")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
