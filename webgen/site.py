"""Static site generator: <run>/site/{index.html, llms.txt, assets/, ja/, en/}.

No network, no external resources except the pinned Plotly script on the chart pages.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pandas as pd

from webgen import chartdata, facts, layout, pages

ROOT = Path(__file__).resolve().parents[1]
ASSET_DIR = ROOT / "site" / "templates" / "assets"


def _body(c: pages.Ctx, name: str) -> tuple[str, str, str, str]:
    """(title, body, head, tail) for one page."""
    t = c.t
    if name == "index":
        return t("top.title"), pages.top_body(c), "", ""
    if name == "research":
        return t("research.title"), pages.research_body(c), "", ""
    if name == "chart":
        return (
            t("chart.title"),
            pages.chart_body(c),
            pages.chart_head(c),
            pages.chart_tail(c),
        )
    if name == "events":
        return t("events.title"), pages.events_body(c), "", pages.EVENTS_TAIL
    if name == "definitions":
        return t("def.title"), pages.definitions_body(c), "", ""
    if name == "data":
        return t("data.title"), pages.data_body(c), "", ""
    raise KeyError(name)


def render_site(run_dir: Path) -> dict[str, str]:
    """Every generated text file of the site as {path relative to site/: content}, rendered in
    memory from the run's data and research files. build_site writes exactly these (plus the
    copied static assets); the publish gate renders them again and compares with the disk."""
    run = Path(run_dir)
    f = facts.load_facts(run)
    out: dict[str, str] = {}

    df = pd.read_csv(run / "data" / "daily_status.csv", parse_dates=["date"])
    ev = pd.read_csv(run / "data" / "ftd_events.csv")
    payload = chartdata.site_payload(df, ev, f.overlay)
    out["assets/chart_data.js"] = (
        "window.CHART_DATA = " + json.dumps(payload, separators=(",", ":")) + ";\n"
    )
    # language-independent numbers behind the key points; the publish gate recomputes them
    out["key_facts.json"] = json.dumps(pages.key_facts(f), indent=1, ensure_ascii=False) + "\n"
    for lang in layout.LANGS:
        c = pages.Ctx(lang, f)
        for name in layout.PAGES:
            title, body, head, tail = _body(c, name)
            out[f"{lang}/{name}.html"] = pages.page(c, name, title, body, head, tail)
    out["index.html"] = pages.chooser_html(f)
    out["llms.txt"] = pages.llms_txt(f)
    return out


def build_site(run_dir: Path) -> Path:
    run = Path(run_dir)
    site = run / "site"
    if site.exists():
        shutil.rmtree(site)
    (site / "assets").mkdir(parents=True)
    for name in layout.ASSETS:
        shutil.copyfile(ASSET_DIR / name, site / "assets" / name)
    # key_facts.json goes to disk first: the data page shows the size of every listed file
    # that exists, and site/key_facts.json is one of them
    (site / "key_facts.json").write_text(
        json.dumps(pages.key_facts(facts.load_facts(run)), indent=1, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    for rel, text in render_site(run).items():
        p = site / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(text, encoding="utf-8")
    return site
