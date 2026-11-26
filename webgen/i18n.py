"""UI text: site/templates/i18n/{ja,en}.yaml, flattened to dotted keys."""

from __future__ import annotations

from functools import cache
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
I18N_DIR = ROOT / "site" / "templates" / "i18n"


def flatten(d: dict, prefix: str = "") -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in d.items():
        key = f"{prefix}{k}"
        if isinstance(v, dict):
            out.update(flatten(v, key + "."))
        else:
            out[key] = str(v)
    return out


@cache
def load(lang: str) -> dict[str, str]:
    data = yaml.safe_load((I18N_DIR / f"{lang}.yaml").read_text(encoding="utf-8"))
    return flatten(data)


class Translator:
    def __init__(self, lang: str):
        self.lang = lang
        self.d = load(lang)
        self.used: set[str] = set()

    def __call__(self, key: str, **kw) -> str:
        self.used.add(key)
        try:
            text = self.d[key]
        except KeyError:
            raise KeyError(f"missing i18n key {key!r} for {self.lang}") from None
        return text.format(**kw) if kw else text

    def has(self, key: str) -> bool:
        return key in self.d
