"""Shared names of the generated run directory and site."""

from __future__ import annotations

LANGS = ("ja", "en")
PAGES = ("index", "research", "chart", "events", "definitions", "data")
ASSETS = ("style.css", "events.js", "chart.js")


def required_site_files() -> list[str]:
    files = ["site/index.html", "site/llms.txt"]
    files += [f"site/assets/{a}" for a in ASSETS]
    files += ["site/assets/chart_data.js"]
    files += [f"site/{lang}/{p}.html" for lang in LANGS for p in PAGES]
    return files
