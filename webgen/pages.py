"""HTML for each page. All numbers come from webgen.facts; all words from the i18n files."""

from __future__ import annotations

import hashlib
import html
import json

from research import freeze as freeze_mod
from research import variants
from research.split import HORIZON
from webgen import chartdata, facts, layout
from webgen.facts import BUCKETS, RISK_LEVELS, Facts, fnum, truthy
from webgen.i18n import Translator


def esc(x) -> str:
    return html.escape(str(x), quote=True)


# --------------------------------------------------------------------------- number format


def pct(x, d: int = 1) -> str:
    return "-" if x is None else f"{x * 100:.{d}f}%"


def spct(x, d: int = 1) -> str:
    return "-" if x is None else f"{x * 100:+.{d}f}%"


def shown(x: float, d: int = 1) -> float:
    """The percent value as displayed (rounded to d decimals)."""
    return float(f"{x * 100:.{d}f}")


def pdiff(a: float, b: float, d: int = 1) -> str:
    """Absolute difference in percentage points. Rule: the gap is computed from the UNROUNDED
    values and rounded once, to d decimals. The caller shows both values with the same d,
    which `gap_digits` chooses so that the printed values really differ by this gap."""
    return f"{abs(a - b) * 100:.{d}f}"


def gap_digits(*pairs: tuple, d: int = 1, extra: int = 2) -> int:
    """Decimals (starting at d, at most d + extra) at which every (a, b) pair, printed rounded,
    differs by exactly the gap that `pdiff` prints for it. Rounding two values and subtracting
    can disagree with the rounded gap (11.44% and 11.35% print as 11.4% and 11.3%, a gap of 0.1,
    while the true gap is 0.09); one more decimal makes the sentence agree with its numbers.
    Pairs with a missing value are ignored."""
    live = [(a, b) for a, b in pairs if a is not None and b is not None]
    for k in range(d, d + extra + 1):
        if all(f"{abs(shown(a, k) - shown(b, k)):.{k}f}" == pdiff(a, b, k) for a, b in live):
            return k
    return d + extra


def num(x, d: int = 2) -> str:
    return "-" if x is None else f"{x:.{d}f}"


def integer(x) -> str:
    return "-" if x is None else f"{int(x):,}"


def g(x) -> str:
    return f"{x:g}"


class Ctx:
    def __init__(self, lang: str, f: Facts):
        self.lang = lang
        self.t = Translator(lang)
        self.f = f
        self.other = "en" if lang == "ja" else "ja"

    def name(self, idx: str) -> str:
        return self.t(f"index.{'sp500' if idx == '^GSPC' else 'nasdaq'}")

    def state(self, code: str) -> str:
        k = f"state.{code}"
        return self.t(k) if self.t.has(k) else code

    def risk(self, code: str) -> str:
        k = f"risk.{code}"
        return self.t(k) if self.t.has(k) else code

    def yesno(self, v) -> str:
        b = truthy(v)
        return "-" if b is None else self.t("yes" if b else "no")

    def cmp(self, a, b, hi="higher", lo="lower", d: int = 1) -> str:
        """Plain-language comparison of a against b at the displayed precision (d decimals of
        a percentage), so the word agrees with the printed numbers."""
        if a is None or b is None:
            return "-"
        sa, sb = shown(a, d), shown(b, d)
        if abs(sa - sb) < 1e-9:
            return self.t("cmp.equal")
        return self.t(f"cmp.{hi}" if sa > sb else f"cmp.{lo}")

    def than(self, a, b, hi="higher", lo="lower", d: int = 1) -> str:
        """Phrase that finishes a sentence about `a` relative to `b`, with the particle in it:
        Japanese "より高いです" / "と同じです", English "higher than" / "the same as"."""
        if a is None or b is None:
            return "-"
        sa, sb = shown(a, d), shown(b, d)
        if abs(sa - sb) < 1e-9:
            return self.t("cmp.than_equal")
        return self.t(f"cmp.than_{hi}" if sa > sb else f"cmp.than_{lo}")

    def gap(self, a, b, hi="higher", lo="lower", d: int = 1) -> str:
        """Like `than` with the size of the difference in points ("より1.2ポイント高いです",
        "1.2 points higher than"), or "no different from" when the displayed values are equal."""
        if a is None or b is None:
            return "-"
        sa, sb = shown(a, d), shown(b, d)
        if abs(sa - sb) < 1e-9:
            return self.t("cmp.gap_equal")
        return self.t(f"cmp.gap_{hi}" if sa > sb else f"cmp.gap_{lo}", gap=pdiff(a, b, d))

    def _join(self, items: list[str], two: str, last: str) -> str:
        items = list(items)
        if len(items) < 2:
            return "".join(items)
        if len(items) == 2:
            return items[0] + self.t(two) + items[1]
        return self.t("list.sep").join(items[:-1]) + self.t(last) + items[-1]

    def join_or(self, items: list[str]) -> str:
        """Alternatives: "125% or 150%", "3, 4 or 5" in English; "125%または150%", "3、4、5"
        in Japanese (the sentence adds "のいずれか" where it is needed)."""
        return self._join(items, "list.or_two", "list.or_last")

    def join_all(self, items: list[str]) -> str:
        """Everything is meant: "0%, 3% and 6%" / "0%、3%、6%"."""
        return self._join(items, "list.and_two", "list.and_last")

    def join_sentences(self, items: list[str]) -> str:
        return ("" if self.lang == "ja" else " ").join(items)

    def cost_label(self, m: dict) -> str:
        """Translated name of a borrow-cost model (the label stored in the JSON is English)."""
        if m["kind"] == "fixed":
            return self.t("research.ov_cost_fixed", rate=pct(m["rate"], 0))
        return self.t("research.ov_cost_dff", spread=pct(m["spread"]))


# ------------------------------------------------------------------------------ html pieces


def table(headers: list[str], rows: list[list[str]], *, classes: list[str] | None = None) -> str:
    th = "".join(f"<th>{esc(h)}</th>" for h in headers)
    body = []
    for k, r in enumerate(rows):
        cls = f' class="{classes[k]}"' if classes and classes[k] else ""
        tds = "".join(f'<td class="{"l" if i == 0 else "num"}">{c}</td>' for i, c in enumerate(r))
        body.append(f"<tr{cls}>{tds}</tr>")
    return f'<div class="tablewrap"><table><thead><tr>{th}</tr></thead><tbody>{"".join(body)}</tbody></table></div>'


def link(href: str, text: str) -> str:
    return f'<a href="{esc(href)}">{esc(text)}</a>'


def ul(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{i}</li>" for i in items) + "</ul>"


def page(c: Ctx, name: str, title: str, body: str, head: str = "", tail: str = "") -> str:
    t = c.t
    nav = "".join(
        f'<a href="{p}.html"{" class=active" if p == name else ""}>{esc(t("nav." + p))}</a>'
        for p in layout.PAGES
    )
    s = c.f.summary
    foot = t(
        "footer.data",
        last=c.f.last_date,
        generated=s.get("generated_at", "-"),
        ref=str(s.get("vendor_ref", "-"))[:10],
    )
    return f"""<!doctype html>
<html lang="{c.lang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} | {esc(t("site.title_short"))}</title>
<link rel="stylesheet" href="../assets/style.css">
{head}</head>
<body>
<header class="top">
<div class="wrap">
<div class="brand">{esc(t("site.title"))}</div>
<nav>{nav}<a class="lang" href="../{c.other}/{name}.html">{esc(t("nav.switch"))}</a></nav>
</div>
</header>
<main class="wrap">
<h1>{esc(title)}</h1>
{body}
</main>
<footer class="wrap foot">
<p class="notice">{esc(t("notice.local"))}</p>
<p>{esc(t("notice.not_ibd"))}</p>
<p class="muted">{esc(foot)}</p>
</footer>
{tail}</body>
</html>
"""


# ------------------------------------------------------------------------------ research text


def _a_rows(c: Ctx, info: dict) -> tuple[list[list[str]], list[str]]:
    rows, cls = [], []
    for period in ("design", "test"):
        for role in ("baseline", "selected"):
            r = info[period][role]
            rows.append(
                [
                    f"{c.t('period.' + period)} / {_role(c, info, role)}",
                    integer(r["n_trades"]),
                    pct(r["win_rate"], 0),
                    spct(r["median"], 2),
                    spct(r["mean"], 2),
                    spct(r["p10"]),
                    spct(r["p90"]),
                    num(r["profit_factor"]),
                    integer(r["n_stopped"]),
                ]
            )
            cls.append("test" if period == "test" else "")
    return rows, cls


def _role(c: Ctx, info: dict, role: str) -> str:
    if role == "selected" and info["selected"] == info["baseline"]:
        return f"{c.t('role.same')} ({info[role]})"
    return f"{c.t('role.' + role)} ({info[role]})"


def _b_rows(c: Ctx, info: dict) -> tuple[list[list[str]], list[str]]:
    rows, cls = [], []
    for period in ("design", "test"):
        for role in ("baseline", "selected"):
            r = info[period][role]
            rows.append(
                [
                    f"{c.t('period.' + period)} / {_role(c, info, role)}",
                    pct(r["cagr"]),
                    pct(r["max_dd"]),
                    pct(r["avg_exposure"], 0),
                    pct(r["em_cagr"]),
                    pct(r["em_max_dd"]),
                    pct(r["bh_cagr"]),
                    pct(r["bh_max_dd"]),
                ]
            )
            cls.append("test" if period == "test" else "")
    return rows, cls


def _c_rows(c: Ctx, info: dict) -> tuple[list[list[str]], list[str]]:
    rows, cls = [], []
    for period in ("design", "test"):
        for role in ("baseline", "selected"):
            r = info[period][role]
            rows.append(
                [
                    f"{c.t('period.' + period)} / {_role(c, info, role)}",
                    f"{pct(r['p_elevated'])} (n={integer(r['n_elevated'])})",
                    f"{pct(r['p_normal'])} (n={integer(r['n_normal'])})",
                    pct(r["spread"]),
                ]
            )
            cls.append("test" if period == "test" else "")
    return rows, cls


def _levels_table(c: Ctx, info: dict) -> str:
    rows = []
    for period in ("design", "test"):
        r = info[period]["baseline"]
        rows.append(
            [c.t("period." + period)]
            + [f"{pct(r.get(f'p_{lv}'))} (n={integer(r.get(f'n_{lv}'))})" for lv in RISK_LEVELS]
        )
    return table([c.t("col.period")] + [c.risk(lv) for lv in RISK_LEVELS], rows)


SMALL_STEP_POINTS = 1.0  # a step smaller than this is reported next to "rises at every step"


def ordering_detail(vals: list[float | None]) -> dict:
    """How the four risk-level values (NORMAL, CAUTION, HIGH, SEVERE) are ordered, judged on
    the displayed percentages (one decimal):

      rises  every step is higher than the one before. `step` is the smallest step in points
             and `lo` / `hi` the level indexes it lies between.
      falls  every step is lower (so NORMAL is the highest).
      none   anything else (equal neighbours, a turn, a missing value).
    """
    none = {"kind": "none", "step": None, "lo": None, "hi": None}
    if len(vals) < 2 or any(v is None for v in vals):
        return none
    p = [shown(v) for v in vals]
    steps = [round(b - a, 6) for a, b in zip(p, p[1:], strict=False)]
    if all(s > 0 for s in steps):
        i = min(range(len(steps)), key=lambda k: steps[k])
        return {"kind": "rises", "step": steps[i], "lo": i, "hi": i + 1}
    if all(s < 0 for s in steps):
        return {"kind": "falls", "step": None, "lo": None, "hi": None}
    return none


def ordering(vals: list[float | None]) -> str:
    """Kind only: "rises", "falls" or "none" (see ordering_detail)."""
    return ordering_detail(vals)["kind"]


def _pick(row: dict, keys: tuple[str, ...]) -> dict:
    return {k: row.get(k) for k in keys}


A_KEYS = (
    "n_events",
    "n_trades",
    "n_dropped_incomplete_window",
    "n_skipped_invalidated_at_entry",
    "win_rate",
    "median",
    "mean",
    "n_stopped",
    "profit_factor",
)
B_KEYS = ("cagr", "bh_cagr", "max_dd", "bh_max_dd", "avg_exposure", "em_cagr", "em_max_dd")
C_KEYS = (
    "p_elevated",
    "n_elevated",
    "p_normal",
    "n_normal",
    "spread",
    *[f"p_{lv}" for lv in RISK_LEVELS],
)


OV_KEYS = ("cagr", "max_dd", "bh_cagr", "bh_max_dd")


def _ov_key_row(info: dict, main: str) -> dict:
    """Figures of the R2 key point for one index: the picked variant against buy and hold in
    the test period and in the design period (None when buy and hold itself was kept)."""
    if info["fallback_to_baseline"] or "selected" not in info["test"]:
        return {"fallback": True, "test": None, "design": None}
    return {
        "fallback": False,
        "test": _pick(info["test"]["selected"][main], OV_KEYS),
        "design": _pick(info["design"]["selected"][main], OV_KEYS),
    }


def key_facts(f: Facts) -> dict:
    """Language-independent numbers behind the key-point sentences (raw values, no words).

    Saved as site/key_facts.json; the publish gate recomputes it from the run's data files and
    re-renders the sentences from it, so a number in a key point that is not produced from the
    data fails the gate."""
    rc = facts.research_constants()
    cnt = rc["counts"]
    kf: dict = {
        "variants": {
            "n_total": cnt["total_variants"],
            "n_ftd": cnt["ftd_variants"],
            "n_dd": cnt["dd_variants"],
            "d0": rc["design"][0],
            "d1": rc["design"][1],
            "t0": rc["test_start"],
            "t1": f.selected["split"]["test"][1],
        },
        "overlap": {o["index"]: o for o in facts.ftd_overlap(f)},
        "horizon": rc["horizon"],
        "dd_threshold": rc["dd_threshold"],
        "a": {},
        "b": {},
        "c": {},
    }
    for ev, keys, name in (("A", A_KEYS, "a"), ("B", B_KEYS, "b"), ("C", C_KEYS, "c")):
        for idx, info in facts.evaluation(f, ev).items():
            kf[name][idx] = {
                "design": _pick(info["design"]["baseline"], keys),
                "test": _pick(info["test"]["baseline"], keys),
            }
    rows = [r for r in facts.selected_beats_baseline(f) if not r["same"]]
    kf["tally"] = {"n": len(rows), "better": sum(1 for r in rows if r["better"])}
    kf["b_violations"] = facts.b_constraint_violations(f)
    ov = f.overlay
    kf["ov"] = None
    if ov is not None:
        main = ov["main_cost_model"]
        kf["ov"] = {
            "after": _forward_after(ov),
            "tolerance": ov["max_dd_tolerance"],
            "indices": {idx: _ov_key_row(info, main) for idx, info in _ov_items(ov)},
        }
    return json.loads(json.dumps(kf))


def _k_design(c: Ctx, idx: str, a: dict, b: dict, cc: dict, h: int, thr: float) -> str:
    d_dd = gap_digits((b["max_dd"], b["em_max_dd"]))
    d_p = gap_digits((cc["p_elevated"], cc["p_normal"]))
    return c.t(
        "research.k_design",
        idx=c.name(idx),
        n=integer(a["n_trades"]),
        win=pct(a["win_rate"], 0),
        median=spct(a["median"], 2),
        dd=pct(b["max_dd"], d_dd),
        em_dd=pct(b["em_max_dd"], d_dd),
        word=c.gap(b["max_dd"], b["em_max_dd"], hi="shallower", lo="deeper", d=d_dd),
        h=h,
        thr=pct(thr, 0),
        p_el=pct(cc["p_elevated"], d_p),
        p_no=pct(cc["p_normal"], d_p),
        dir=c.gap(cc["p_elevated"], cc["p_normal"], d=d_p),
        mono=_mono(c, cc),
    )


def _mono(c: Ctx, cc: dict) -> str:
    d = ordering_detail([cc.get(f"p_{lv}") for lv in RISK_LEVELS])
    if d["kind"] == "rises" and d["step"] < SMALL_STEP_POINTS:
        return c.t(
            "mono.rises_small",
            lo=c.risk(RISK_LEVELS[d["lo"]]),
            hi=c.risk(RISK_LEVELS[d["hi"]]),
            step=f"{d['step']:.1f}",
        )
    return c.t(f"mono.{d['kind']}")


def render_key_points(c: Ctx, kf: dict) -> list[str]:
    """The key-point sentences, rendered from `key_facts` only."""
    v, h, thr = kf["variants"], kf["horizon"], kf["dd_threshold"]
    out = [c.t("research.k_variants", **v)]
    for idx in kf["a"]:
        out.append(
            _k_design(
                c,
                idx,
                kf["a"][idx]["design"],
                kf["b"][idx]["design"],
                kf["c"][idx]["design"],
                h,
                thr,
            )
        )
    for idx, d in kf["a"].items():
        b = d["test"]
        out.append(_k_a(c, idx, b, kf["overlap"].get(idx), h))
    for idx, d in kf["b"].items():
        r = d["test"]
        d_dd = gap_digits((r["max_dd"], r["em_max_dd"]))
        out.append(
            c.t(
                "research.k_b",
                idx=c.name(idx),
                cagr=pct(r["cagr"]),
                bh_cagr=pct(r["bh_cagr"]),
                max_dd=pct(r["max_dd"], d_dd),
                bh_dd=pct(r["bh_max_dd"]),
                inv=pct(r["avg_exposure"], 0),
                em_cagr=pct(r["em_cagr"]),
                em_dd=pct(r["em_max_dd"], d_dd),
                word=c.gap(r["max_dd"], r["em_max_dd"], hi="shallower", lo="deeper", d=d_dd),
            )
        )
    for idx, d in kf["c"].items():
        r = d["test"]
        d_p = gap_digits((r["p_elevated"], r["p_normal"]))
        out.append(
            c.t(
                "research.k_c",
                idx=c.name(idx),
                dd=pct(thr, 0),
                h=h,
                p_el=pct(r["p_elevated"], d_p),
                n_el=integer(r["n_elevated"]),
                p_no=pct(r["p_normal"], d_p),
                n_no=integer(r["n_normal"]),
                dir=c.gap(r["p_elevated"], r["p_normal"], d=d_p),
                mono=_mono(c, r),
            )
        )
    t = kf["tally"]
    out.append(
        c.t("research.k_tally", n=t["n"], better=t["better"])
        if t["n"]
        else c.t("research.k_tally_none")
    )
    for x in kf["b_violations"]:
        d_v = gap_digits((x["cagr"], x["bh_cagr"]))
        out.append(
            c.t(
                "research.k_b_violation",
                idx=c.name(x["index"]),
                id=x["id"],
                cagr=pct(x["cagr"], d_v),
                bh_cagr=pct(x["bh_cagr"], d_v),
                gap=pdiff(x["cagr"], x["bh_cagr"], d_v),
                tol=g(x["tolerance"] * 100),
            )
        )
    ovk = kf.get("ov")
    if ovk:
        items = [
            c.t("research.ov_key_item_same", idx=c.name(idx))
            if v["fallback"]
            else _ov_key_item(c, idx, v, ovk["tolerance"])
            for idx, v in ovk["indices"].items()
        ]
        out.append(c.t("research.ov_key", items=c.join_sentences(items), after=ovk["after"]))
    return out


def _k_a(c: Ctx, idx: str, b: dict, ov: dict | None, h: int) -> str:
    """FTD-trade key point: the trades of the test period, the events that did not become
    trades and why, and how many of the FTDs lie within `h` sessions of another one."""
    t = c.t
    dropped, skipped = b["n_dropped_incomplete_window"], b["n_skipped_invalidated_at_entry"]
    notes = []
    if dropped:
        notes.append(t("research.k_a_dropped", n=dropped, h=h))
    if skipped:
        notes.append(t("research.k_a_skipped", n=skipped))
    if ov is not None:
        notes.append(t("research.k_a_overlap", n=ov["n"], k=ov["k"], h=ov["h"]))
    return t(
        "research.k_a",
        idx=c.name(idx),
        events=integer(b["n_events"]),
        n=integer(b["n_trades"]),
        notes=(" " if c.lang == "en" and notes else "") + c.join_sentences(notes),
        win=pct(b["win_rate"], 0),
        median=spct(b["median"], 2),
        mean=spct(b["mean"], 2),
        stopped=integer(b["n_stopped"]),
        pf=num(b["profit_factor"]),
    )


def _ov_key_item(c: Ctx, idx: str, v: dict, tol: float) -> str:
    """One index in the R2 key point: test-period and design-period figures of the picked
    variant against buy and hold, and the margin by which it passed the drawdown limit."""
    te, de = v["test"], v["design"]
    d_t = gap_digits((te["cagr"], te["bh_cagr"]), (te["max_dd"], te["bh_max_dd"]))
    limit = de["bh_max_dd"] - tol
    margin = (de["max_dd"] - limit) * 100
    d_d = gap_digits((de["cagr"], de["bh_cagr"]), (de["max_dd"], limit), d=2)
    return c.t(
        "research.ov_key_item",
        idx=c.name(idx),
        cagr=pct(te["cagr"], d_t),
        bh_cagr=pct(te["bh_cagr"], d_t),
        dd=pct(te["max_dd"], d_t),
        bh_dd=pct(te["bh_max_dd"], d_t),
        d_cagr=pct(de["cagr"], d_d),
        d_bh_cagr=pct(de["bh_cagr"], d_d),
        d_w=c.than(de["cagr"], de["bh_cagr"], d=d_d),
        d_dd=pct(de["max_dd"], d_d),
        limit=pct(limit, d_d),
        margin=f"{margin:.{d_d}f}",
        narrow=c.t("research.ov_key_narrow") if margin < NARROW_MARGIN_POINTS else "",
    )


def key_points(c: Ctx) -> list[str]:
    return render_key_points(c, key_facts(c.f))


def amendment_items(c: Ctx, block: dict, tie_key: str, with_amend: bool = True) -> list[str]:
    """Tie rule, the recorded post-hoc amendments and the grid freeze (selected.json or
    overlay_selected.json), shown on the research page."""
    out = [c.t(tie_key)]
    if with_amend:
        out += [
            c.t("research.m_amend", date=a["date"]) for a in block.get("post_hoc_amendments", [])
        ]
    fr = block.get("freeze")
    if fr:
        out.append(c.t("research.m_freeze", date=fr["pre_registered"], sha=fr["grid_sha256"]))
        if fr.get("frozen_at_utc"):
            out.append(freeze_item(c, fr, block.get("freeze_provenance")))
    return out


def _rows_hash(path) -> str | None:
    """sha256 of a CSV file without its header line (None when the file is missing)."""
    try:
        data = path.read_bytes()
    except OSError:
        return None
    return hashlib.sha256(data.split(b"\n", 1)[1] if b"\n" in data else b"").hexdigest()


def freeze_item(c: Ctx, fr: dict, prov: dict | None) -> str:
    """When the grid hash file was made and what the earlier runs show (from the provenance
    stored in the freeze file and copied into selected.json / overlay_selected.json).

    The grids were written in docs/plan.md before the computations; the hash file came later.
    `pre_registered` is the local date, `frozen_at_utc` the same moment in UTC."""
    t = c.t
    ts = fr["frozen_at_utc"]
    local = freeze_mod.local_date_of(ts)
    head = t(
        "research.freeze_head",
        ts=ts,
        date=fr["pre_registered"],
        local=local,
        utc_note=t("research.freeze_utc_next") if ts[:10] != local else "",
    )
    if not prov or not prov.get("n_runs"):
        return c.join_sentences([head.strip(), t("research.freeze_prov_none")])
    runs_dir = c.f.run / prov["file"]
    this_rows = _rows_hash(runs_dir)
    first = prov["runs"][0]
    if prov["identical_bytes"]:
        state = t("research.prov_bytes_same", sha=first["sha256"][:12])
    elif prov["identical_rows"]:
        state = t("research.prov_rows_same")
    else:
        state = t("research.prov_diff")
    this_same = prov["identical_rows"] and this_rows == first["rows_sha256"]
    tail = t(
        "research.freeze_prov",
        n=prov["n_runs"],
        first=prov["first_run"],
        file=prov["file"],
        state=state,
        this=t("research.prov_this_same" if this_same else "research.prov_this_diff"),
    )
    return c.join_sentences([head.strip(), tail])


def tie_items(c: Ctx) -> list[str]:
    """The picks the amended tie rule changed, with both picks' results, and the tally."""
    f, t = c.f, c.t
    comp = f.selected.get("tie_break_comparison") or []
    if not comp:
        return [t("research.tie_none")]
    out = []
    for x in comp:
        b, a = x["before"], x["after"]
        ev = x["evaluation"]
        common = {"idx": c.name(x["index"]), "before": b["id"], "after": a["id"]}
        if ev == "A":
            out.append(
                t(
                    "research.tie_pick_a",
                    **common,
                    bd_med=spct(b["design"]["median"], 2),
                    b_n=integer(b["test"]["n_trades"]),
                    b_med=spct(b["test"]["median"], 2),
                    b_mean=spct(b["test"]["mean"], 2),
                    ad_med=spct(a["design"]["median"], 2),
                    a_n=integer(a["test"]["n_trades"]),
                    a_med=spct(a["test"]["median"], 2),
                    a_mean=spct(a["test"]["mean"], 2),
                )
            )
        elif ev == "B":
            out.append(
                t(
                    "research.tie_pick_b",
                    **common,
                    bd_dd=pct(b["design"]["max_dd"]),
                    b_cagr=pct(b["test"]["cagr"]),
                    b_dd=pct(b["test"]["max_dd"]),
                    ad_dd=pct(a["design"]["max_dd"]),
                    a_cagr=pct(a["test"]["cagr"]),
                    a_dd=pct(a["test"]["max_dd"]),
                )
            )
        else:
            out.append(
                t(
                    "research.tie_pick_c",
                    **common,
                    bd_sp=pct(b["design"]["spread"]),
                    b_sp=pct(b["test"]["spread"]),
                    ad_sp=pct(a["design"]["spread"]),
                    a_sp=pct(a["test"]["spread"]),
                )
            )
    tally = facts.tie_tally(f)
    new, old = tally["new"], tally["old"]
    if new == old:
        out.append(t("research.tie_tally_same", n=new["n"], better=new["better"]))
    else:
        out.append(
            t(
                "research.tie_tally_changed",
                n=new["n"],
                better=new["better"],
                n_old=old["n"],
                better_old=old["better"],
            )
        )
    return out


def _sel_a(c: Ctx, idx: str, info: dict) -> str:
    if info["selected"] == info["baseline"]:
        return c.t("research.sel_same", id=info["selected"])
    d, t = info["design"], info["test"]
    return c.t(
        "research.a_sel",
        id=info["selected"],
        d_med=spct(d["selected"]["median"], 2),
        d_base=spct(d["baseline"]["median"], 2),
        t_med=spct(t["selected"]["median"], 2),
        t_base=spct(t["baseline"]["median"], 2),
        word=c.than(t["selected"]["median"], t["baseline"]["median"], d=2),
        n=integer(t["selected"]["n_trades"]),
    )


def _sel_b(c: Ctx, idx: str, info: dict) -> str:
    if info["selected"] == info["baseline"]:
        return c.t("research.sel_same", id=info["selected"])
    d, t = info["design"], info["test"]
    return c.t(
        "research.b_sel",
        id=info["selected"],
        d_dd=pct(d["selected"]["max_dd"]),
        d_base=pct(d["baseline"]["max_dd"]),
        t_dd=pct(t["selected"]["max_dd"]),
        t_base=pct(t["baseline"]["max_dd"]),
        word=c.than(t["selected"]["max_dd"], t["baseline"]["max_dd"], hi="shallower", lo="deeper"),
        t_cagr=pct(t["selected"]["cagr"]),
        t_base_cagr=pct(t["baseline"]["cagr"]),
    )


def _sel_c(c: Ctx, idx: str, info: dict) -> str:
    if info["selected"] == info["baseline"]:
        return c.t("research.sel_same", id=info["selected"])
    d, t = info["design"], info["test"]
    return c.t(
        "research.c_sel",
        id=info["selected"],
        d_sp=pct(d["selected"]["spread"]),
        d_base=pct(d["baseline"]["spread"]),
        t_sp=pct(t["selected"]["spread"]),
        t_base=pct(t["baseline"]["spread"]),
        word=c.than(t["selected"]["spread"], t["baseline"]["spread"]),
    )


def _stat_table(c: Ctx, stats: dict, idxs: list[str], labels: tuple[str, ...], label_name) -> str:
    """Rows: index x label x horizon. Columns: per basis n / median / quartiles."""
    bases = ("all_days", "non_overlapping", "calendar_year_blocks")
    heads = [c.t("col.group"), c.t("col.horizon")]
    for b in bases:
        heads += [c.t(f"basis.{b}") + " n", c.t("col.median"), c.t("col.iqr")]
    rows = []
    for idx in idxs:
        for lab in labels:
            for hz in ("20", "60"):
                row = [f"{c.name(idx)} / {label_name(lab)}", f"{hz}"]
                for b in bases:
                    s = stats.get((idx, lab, hz, b))
                    if s is None:
                        row += ["-", "-", "-"]
                    else:
                        # effective n of a calendar-year block statistic is the number of years
                        n = (
                            c.t("research.n_years", n=s["n_years"])
                            if b == "calendar_year_blocks" and s["n_years"]
                            else integer(s["n"])
                        )
                        row += [
                            n,
                            spct(s["median"], 2),
                            f"{spct(s['p25'], 1)} .. {spct(s['p75'], 1)}",
                        ]
                rows.append(row)
    return table(heads, rows)


def _cy_takeaway(
    c: Ctx, stats: dict, idx: str, labels: tuple[str, ...], label_name, key: str
) -> str:
    vals = {}
    for lab in labels:
        s = stats.get((idx, lab, str(HORIZON), "calendar_year_blocks"))
        if s:
            vals[lab] = s
    lo, hi = labels[0], labels[-1]
    if lo not in vals or hi not in vals:
        return ""
    listing = c.t("sep").join(
        f"{label_name(lab)} {spct(vals[lab]['median'], 2)}" for lab in labels if lab in vals
    )
    overlap = max(vals[lo]["p25"], vals[hi]["p25"]) <= min(vals[lo]["p75"], vals[hi]["p75"])
    return c.t(
        key,
        idx=c.name(idx),
        h=HORIZON,
        listing=listing,
        hi=label_name(hi),
        lo=label_name(lo),
        word=c.gap(vals[hi]["median"], vals[lo]["median"], d=2),
        overlap=c.t("overlap.yes" if overlap else "overlap.no"),
    )


def _ov_main_rows(c: Ctx, info: dict, main: str) -> tuple[list[list[str]], list[str]]:
    rows, cls = [], []
    for period in ("design", "test"):
        for role, m in info[period].items():
            r = m[main]
            vid = info["selected"] if role == "selected" else "buy_hold"
            rows.append(
                [
                    f"{c.t('period.' + period)} / {c.t('role.' + role)} ({vid})",
                    pct(r["cagr"]),
                    pct(r["max_dd"]),
                    pct(r["vol"]),
                    pct(r["levered_share"], 0),
                    integer(r["n_episodes"]),
                    pct(r["worst_levered_dd"]),
                    integer(r["margin_stress_days"]),
                    pct(r["em_cagr"]),
                    pct(r["em_max_dd"]),
                ]
            )
            cls.append("test" if period == "test" else "")
    return rows, cls


def overlay_takeaway(c: Ctx, idx: str, info: dict, main: str) -> str:
    if info["fallback_to_baseline"]:
        return c.t("research.ov_k_same", idx=c.name(idx))
    r = info["test"]["selected"][main]
    d_c = gap_digits((r["cagr"], r["bh_cagr"]), (r["cagr"], r["em_cagr"]))
    d_d = gap_digits((r["max_dd"], r["bh_max_dd"]))
    return c.t(
        "research.ov_k",
        idx=c.name(idx),
        cagr=pct(r["cagr"], d_c),
        bh_cagr=pct(r["bh_cagr"], d_c),
        w_bh=c.than(r["cagr"], r["bh_cagr"], d=d_c),
        em_cagr=pct(r["em_cagr"], d_c),
        w_em=c.than(r["cagr"], r["em_cagr"], d=d_c),
        dd=pct(r["max_dd"], d_d),
        bh_dd=pct(r["bh_max_dd"], d_d),
        w_dd=c.than(r["max_dd"], r["bh_max_dd"], hi="shallower", lo="deeper", d=d_d),
        lev=pct(r["levered_share"], 0),
        ep=integer(r["n_episodes"]),
        wl=pct(r["worst_levered_dd"]),
        ms=integer(r["margin_stress_days"]),
    )


NARROW_MARGIN_POINTS = 0.5  # a qualification margin below this is called out as very small


def _contam_block(c: Ctx, ov: dict) -> list[str]:
    """The test-period contamination notice of R2 (overlay_selected.json
    "test_period_contamination"): the preview, its figures and what follows from it."""
    t = c.t
    cd = ov["test_period_contamination"]
    pv = cd["preview"]
    lo, hi = pv["levered_share_range"]
    lead = t(
        "research.ov_contam_lead",
        date=cd["date"],
        y0=pv["period"][0][:4],
        y1=pv["period"][1][:4],
        lev=pct(pv["leverage"], 0),
        rate=pct(pv["borrow_rate"], 0),
        share_lo=pct(lo, 0),
        share_hi=pct(hi, 0),
    )
    rows = []
    for r in pv["results"]:
        same = abs(r["preview_cagr"] - r["buy_hold_cagr"]) < 1e-9
        rows.append(
            [
                esc(f"{c.name(r['index'])} / {r['period'][0]}-{r['period'][1]}"),
                pct(r["preview_cagr"]),
                esc(t("research.ov_contam_same")) if same else pct(r["buy_hold_cagr"]),
            ]
        )
    return [
        '<div class="callout">',
        f"<h3>{esc(t('research.ov_contam_h'))}</h3>",
        f"<p>{esc(lead)}</p>",
        table([t("col.group"), t("col.ov_preview_cagr"), t("col.bh_cagr")], rows),
        f"<p>{esc(t('research.ov_contam_effect'))}</p>",
        f"<p><strong>{esc(t('research.ov_contam_verdict'))}</strong></p>",
        "</div>",
    ]


def _design_sentences(c: Ctx, ov: dict, idx: str, info: dict, main: str) -> list[str]:
    """Design-period figures of the picked variant against buy and hold, and how narrowly it
    passed the drawdown limit. The rule does not require beating buy and hold on CAGR."""
    t = c.t
    sel, bh = info["design"]["selected"][main], info["design"]["baseline"][main]
    tol = ov["max_dd_tolerance"]
    limit = bh["max_dd"] - tol
    margin = (sel["max_dd"] - limit) * 100
    d_c = gap_digits((sel["cagr"], bh["cagr"]))
    d_m = gap_digits((sel["max_dd"], limit), (sel["max_dd"], bh["max_dd"]), d=2)
    out = [
        t(
            "research.ov_design",
            idx=c.name(idx),
            cagr=pct(sel["cagr"], d_c),
            bh_cagr=pct(bh["cagr"], d_c),
            w=c.than(sel["cagr"], bh["cagr"], d=d_c),
            dd=pct(sel["max_dd"], d_m),
            bh_dd=pct(bh["max_dd"], d_m),
        ),
        t("research.ov_design_rule", pp=g(tol * 100)),
    ]
    if shown(sel["cagr"], d_c) < shown(bh["cagr"], d_c):
        out.append(t("research.ov_design_worse", idx=c.name(idx)))
    out.append(
        t(
            "research.ov_design_margin",
            idx=c.name(idx),
            limit=pct(limit, d_m),
            pp=g(tol * 100),
            bh_dd=pct(bh["max_dd"], d_m),
            dd=pct(sel["max_dd"], d_m),
            margin=f"{margin:.{d_m}f}",
        )
    )
    if margin < NARROW_MARGIN_POINTS:
        out.append(t("research.ov_design_narrow"))
    return out


def _forward_table(c: Ctx, ov: dict) -> str:
    t = c.t
    rows = []
    for idx, info in _ov_items(ov):
        fw = info["forward"]
        for role in ("baseline", "selected"):
            if role not in fw:
                continue
            vid = info["selected"] if role == "selected" else "buy_hold"
            label = esc(f"{c.name(idx)} / {t('role.' + role)} ({vid})")
            r = fw[role]
            if r["n_days"] == 0:
                rows.append([label, esc(t("research.ov_forward_none")), "-", "-", "-"])
            else:
                rows.append(
                    [
                        label,
                        integer(r["n_days"]),
                        spct(r["cum_return"], 2),
                        pct(r["max_dd"]),
                        integer(r["levered_days"]),
                    ]
                )
    heads = [t("col.group")] + [
        t("col." + k) for k in ("ov_fw_n", "ov_fw_cum", "max_dd", "ov_fw_lev")
    ]
    return table(heads, rows)


def _rate_items(c: Ctx, ov: dict) -> list[str]:
    """The post-hoc change of the borrow-rate timing and its computed effect."""
    t = c.t
    am = next((a for a in ov.get("post_hoc_amendments", []) if "rate" in a["what"]), None)
    if am is None:
        return []
    out = [t("research.ov_m_rate_amend", date=am["date"])]
    for idx, info in _ov_items(ov):
        e = info.get("rate_timing_effect")
        if not e:
            continue
        out.append(
            t(
                "research.ov_m_rate_item",
                idx=c.name(idx),
                design=_before_after(c, e["design"]),
                test=_before_after(c, e["test"]),
                same=t("research.ov_unchanged" if not e["pick_changed"] else "research.ov_changed"),
            )
        )
    return out


def _before_after(c: Ctx, e: dict) -> str:
    """ "12.70% to 12.71%", or "12.70%, unchanged" when the two values print the same."""
    before, after = pct(e["cagr_before"], 4), pct(e["cagr_after"], 4)
    if before == after:
        return c.t("research.ov_rate_same", value=after)
    return c.t("research.ov_rate_changed", before=before, after=after)


def _ov_tie_items(c: Ctx, ov: dict) -> list[str]:
    """Did the R2 tie rule change a pick? (computed in overlay_selected.json)"""
    t = c.t
    items = []
    for idx, info in _ov_items(ov):
        e = info.get("tie_effect")
        if e and (e["n_exact_ties_at_top"] or e["changed"]):
            items.append(
                t(
                    "research.ov_tie_item",
                    idx=c.name(idx),
                    n=e["n_exact_ties_at_top"] + 1,
                    changed=t("research.ov_changed" if e["changed"] else "research.ov_unchanged"),
                )
            )
    return items or [t("research.ov_tie_none")]


def overlay_section(c: Ctx) -> list[str]:
    """Research R2 chapter (docs/plan.md section 3.7). Every number comes from overlay_selected."""
    t, ov = c.t, c.f.overlay
    if ov is None:
        return [
            f'<h2 id="overlay">{esc(t("research.h_overlay"))}</h2>',
            f"<p>{esc(t('research.ov_none'))}</p>",
        ]
    main = ov["main_cost_model"]
    labels = {m["id"]: c.cost_label(m) for m in ov["cost_models"]}
    grid = ov["variant_grid"]
    fixed = [m for m in ov["cost_models"] if m["kind"] == "fixed"]
    spread = next(m["spread"] for m in ov["cost_models"] if m["kind"] == "dff")
    d0, d1 = ov["split"]["design"]
    t0, t1 = ov["split"]["test"]
    ratio = pct(ov["maintenance_equity_ratio"], 0)
    parts = [f'<h2 id="overlay">{esc(t("research.h_overlay"))}</h2>']
    parts.append(f"<p>{esc(t('research.ov_lead'))}</p>")
    parts.append(
        f'<div class="callout">{esc(t("research.ov_disclaimer"))} {esc(t("research.ov_dividend"))}</div>'
    )
    parts.extend(_contam_block(c, ov))
    method = [
        t(
            "research.ov_m_variants",
            n=ov["n_variants"],
            ks=c.join_or([str(k) for k in grid["k"]]),
            levs=c.join_or([pct(x, 0) for x in grid["leverage"]]),
        ),
        t("research.ov_m_split", d0=d0, d1=d1, t0=t0, t1=t1),
        t(
            "research.ov_m_criterion",
            pp=g(ov["max_dd_tolerance"] * 100),
            main=labels[main],
        ),
        t("research.ov_m_timing"),
        t(
            "research.ov_m_cost",
            spread=pct(spread),
            fixed=c.join_all([pct(m["rate"], 0) for m in fixed]),
        ),
        t("research.ov_m_margin", ratio=ratio),
        t("research.ov_m_em"),
        *amendment_items(c, ov, "research.ov_m_tie", with_amend=False),
        *_ov_tie_items(c, ov),
        *_rate_items(c, ov),
    ]
    parts.append(ul([esc(x) for x in method]))
    parts.append(ul([esc(overlay_takeaway(c, i, info, main)) for i, info in _ov_items(ov)]))
    for idx, info in _ov_items(ov):
        parts.append(f"<h3>{esc(c.name(idx))}</h3>")
        if info["fallback_to_baseline"]:
            parts.append(f"<p>{esc(t('research.ov_sel_fallback', idx=c.name(idx)))}</p>")
            continue
        p = info["selected_params"]
        parts.append(
            "<p>"
            + esc(
                t(
                    "research.ov_sel",
                    idx=c.name(idx),
                    id=info["selected"],
                    k=p["k"],
                    inv=t("research.ov_yes" if p["use_invalidation"] else "research.ov_no"),
                    lev=pct(p["leverage"], 0),
                    nq=info["n_qualifying"],
                    nc=info["n_considered"],
                )
            )
            + "</p>"
        )
        parts.append(ul([esc(x) for x in _design_sentences(c, ov, idx, info, main)]))
        parts.append(f"<p>{esc(t('research.ov_h_main', label=labels[main]))}</p>")
        rows, cls = _ov_main_rows(c, info, main)
        heads = [t("col.period_role")] + [
            t("col." + k2)
            for k2 in (
                "cagr",
                "max_dd",
                "ov_vol",
                "ov_lev_share",
                "ov_episodes",
                "ov_worst",
                "ov_margin",
                "em_cagr",
                "em_max_dd",
            )
        ]
        parts.append(table(heads, rows, classes=cls))
        parts.append(
            f"<p><strong>{esc(t('research.ov_h_sens'))}</strong> {esc(t('research.ov_sens_note'))}</p>"
        )
        srows = []
        for m in ov["cost_models"]:
            sel = info["test"]["selected"][m["id"]]
            srows.append(
                [
                    esc(labels[m["id"]]),
                    pct(sel["cagr"]),
                    pct(sel["max_dd"]),
                    pct(sel["em_cagr"]),
                    pct(sel["em_max_dd"]),
                    pct(sel["bh_cagr"]),
                    pct(sel["bh_max_dd"]),
                ]
            )
        parts.append(
            table(
                [t("col.ov_cost")]
                + [
                    t("col." + k2)
                    for k2 in ("cagr", "max_dd", "em_cagr", "em_max_dd", "bh_cagr", "bh_max_dd")
                ],
                srows,
            )
        )
    parts.append(f"<h3>{esc(t('research.ov_forward_h'))}</h3>")
    parts.append(f"<p>{esc(t('research.ov_forward_note', after=_forward_after(ov)))}</p>")
    parts.append(_forward_table(c, ov))
    parts.append(f"<h3>{esc(t('research.ov_h_cv'))}</h3>")
    parts.append(
        ul(
            [
                esc(t("research.ov_cv_pick", n=ov["n_variants"])),
                esc(t("research.ov_cv_margin", ratio=ratio)),
                esc(t("research.ov_cv_cost")),
                esc(t("research.ov_dividend")),
            ]
        )
    )
    return parts


def _forward_after(ov: dict) -> str:
    return ov["test_period_contamination"]["forward_holdout"]["data_after"]


def _ov_items(ov: dict) -> list[tuple[str, dict]]:
    return [(i, ov["indices"][i]) for i in facts.INDEX_ORDER if i in ov["indices"]]


def _contam_r_block(c: Ctx) -> list[str]:
    """The third test-period exception (selected.json "test_period_contamination"): R was
    designed after statistics over the whole of 2000 to 2026, which include the test period."""
    t = c.t
    cd = c.f.selected["test_period_contamination"]
    fig = cd["figures"]
    dd_thr = pct(facts.research_constants()["dd_threshold"], 0)
    rows = []
    for idx in facts.INDEX_ORDER:
        if idx not in fig["dd_day_share"]:
            continue
        lv = fig["p_drawdown_60d"][idx]
        rows.append(
            [
                esc(c.name(idx)),
                pct(fig["dd_day_share"][idx]),
                pct(lv["NORMAL"]),
                pct(lv["SEVERE"]),
            ]
        )
    lead = t(
        "research.r_contam_lead",
        date=cd["date"],
        y0=cd["period"][0][:4],
        y1=cd["period"][1][:4],
        test_start=facts.TEST_START,
    )
    return [
        '<div class="callout">',
        f"<h3>{esc(t('research.r_contam_h'))}</h3>",
        f"<p>{esc(lead)}</p>",
        table(
            [
                t("col.group"),
                t("col.r_dd_share"),
                t("col.r_p_normal", dd=dd_thr, h=HORIZON),
                t("col.r_p_severe", dd=dd_thr, h=HORIZON),
            ],
            rows,
        ),
        f"<p>{esc(t('research.r_contam_ftd'))}</p>",
        f"<p>{esc(t('research.r_contam_effect'))}</p>",
        f"<p><strong>{esc(t('research.r_contam_verdict'))}</strong></p>",
        "</div>",
    ]


def forward_r_section(c: Ctx) -> list[str]:
    """Forward record of research R: the baseline and the picks over the sessions after the
    frozen end of the test period (selected.json "forward_record"). "Not started" until a
    session exists after the end."""
    t, f = c.t, c.f
    fw = f.selected["forward_record"]
    parts = [
        f'<h2 id="forward">{esc(t("research.h_forward"))}</h2>',
        f"<p>{esc(t('research.forward_note', after=fw['after']))}</p>",
    ]
    heads = {
        "A": ("fw_events", "fw_trades", "median"),
        "B": ("ov_fw_n", "ov_fw_cum", "ov_fw_bh_cum", "max_dd", "bh_max_dd"),
        "C": ("fw_el", "fw_no", "spread"),
    }
    for ev in "ABC":
        rows = []
        for idx in facts.INDEX_ORDER:
            for role, r in fw[ev].get(idx, {}).items():
                vid = r["variant_id"]
                label = esc(f"{c.name(idx)} / {t('role.' + role)} ({vid})")
                if fw["n_sessions"][idx] == 0:
                    rows.append(
                        [label, esc(t("research.ov_forward_none"))] + ["-"] * (len(heads[ev]) - 1)
                    )
                elif ev == "A":
                    rows.append(
                        [
                            label,
                            integer(r["n_events"]),
                            integer(r["n_trades"]),
                            spct(r["median"], 2),
                        ]
                    )
                elif ev == "B":
                    rows.append(
                        [
                            label,
                            integer(r["n_days"]),
                            spct(r["cum_return"], 2),
                            spct(r["bh_cum_return"], 2),
                            pct(r["max_dd"]),
                            pct(r["bh_max_dd"]),
                        ]
                    )
                else:
                    rows.append(
                        [label, integer(r["n_elevated"]), integer(r["n_normal"]), pct(r["spread"])]
                    )
        parts.append(f"<h3>{esc(t('research.h_' + ev.lower()))}</h3>")
        parts.append(table([t("col.group")] + [t("col." + k) for k in heads[ev]], rows))
    return parts


def research_body(c: Ctx) -> str:
    t, f = c.t, c.f
    rc = facts.research_constants()
    cnt = rc["counts"]
    parts = [f'<p class="lead">{esc(t("research.lead"))}</p>']
    parts.append(f'<div class="callout">{esc(t("research.caveat_short"))}</div>')

    parts.append(f'<h2 id="conclusions">{esc(t("research.h_conclusions"))}</h2>')
    parts.append(ul([esc(x) for x in key_points(c)]))
    parts.append(f'<p class="muted">{esc(t("research.conclusions_note"))}</p>')

    parts.append(f'<h2 id="method">{esc(t("research.h_method"))}</h2>')
    method = [
        t(
            "research.m_split",
            d0=rc["design"][0],
            d1=rc["design"][1],
            t0=rc["test_start"],
            t1=f.selected["split"]["test"][1],
        ),
        t("research.m_once"),
        t("research.m_index"),
        t("research.m_pit"),
        t("research.m_window", h=rc["horizon"]),
        t("research.m_crit_a", n=rc["min_trades_a"]),
        t("research.m_crit_b", pp=g(rc["cagr_tol_b"] * 100)),
        t("research.m_crit_c", n=rc["min_days_c"], dd=pct(rc["dd_threshold"], 0), h=rc["horizon"]),
        t("research.m_fallback"),
    ]
    parts.append(ul([esc(x) for x in method]))
    parts.append(f'<h3 id="amendments">{esc(t("research.h_amend"))}</h3>')
    parts.append(
        ul([esc(x) for x in [*amendment_items(c, f.selected, "research.m_tie"), *tie_items(c)]])
    )
    parts.extend(_contam_r_block(c))

    parts.append(f'<h2 id="variants">{esc(t("research.h_variants"))}</h2>')
    parts.append(
        f"<p>{esc(t('research.variants_intro', n_total=cnt['total_variants'], n_ftd=cnt['ftd_variants'], n_dd=cnt['dd_variants'], n_cfg=cnt['distinct_ftd_detector_configs'], n_sig=cnt['distinct_dd_signal_definitions']))}</p>"
    )
    vrows = [
        [
            t("research.v_corr"),
            ", ".join(g(x) + "%" for x in rc["corrections"]),
            g(rc["corrections"][0]) + "%",
        ],
        [t("research.v_gain"), ", ".join(g(x) + "%" for x in rc["gains"]), g(rc["gains"][0]) + "%"],
        [t("research.v_vol"), t("research.v_vol_vals"), t("research.v_vol_base")],
        [
            t("research.v_decline"),
            ", ".join(pct(x) for x in rc["declines"]),
            pct(rc["declines"][0]),
        ],
        [t("research.v_dvol"), ", ".join(rc["volume_rules"]), rc["volume_rules"][0]],
        [t("research.v_risk"), ", ".join(rc["risk_rules"]), rc["risk_rules"][0]],
        [t("research.v_exp"), ", ".join(pct(x, 0) for x in rc["exposures"]), pct(0.5, 0)],
    ]
    parts.append(table([t("col.dimension"), t("col.values"), t("col.baseline")], vrows))
    parts.append(f'<p class="muted">{esc(t("research.variants_note"))}</p>')
    parts.append(
        f'<p class="muted">{esc(t("research.v_codes", look=variants.PCTL_LOOKBACK, min_obs=variants.PCTL_MIN_OBS))}</p>'
    )

    # --- evaluations
    for ev, rows_fn, head_keys, sel_fn, intro in (
        (
            "A",
            _a_rows,
            ("n_trades", "win_rate", "median", "mean", "p10", "p90", "pf", "stopped"),
            _sel_a,
            "research.a_intro",
        ),
        (
            "B",
            _b_rows,
            ("cagr", "max_dd", "invested", "em_cagr", "em_max_dd", "bh_cagr", "bh_max_dd"),
            _sel_b,
            "research.b_intro",
        ),
        ("C", _c_rows, ("p_elevated", "p_normal", "spread"), _sel_c, "research.c_intro"),
    ):
        parts.append(f'<h2 id="eval-{ev.lower()}">{esc(t("research.h_" + ev.lower()))}</h2>')
        parts.append(f"<p>{esc(t(intro, h=rc['horizon'], dd=pct(rc['dd_threshold'], 0)))}</p>")
        if ev == "B":
            parts.append(f'<div class="callout">{esc(t("research.b_em_note"))}</div>')
        for idx, info in facts.evaluation(f, ev).items():
            parts.append(f"<h3>{esc(c.name(idx))}</h3>")
            parts.append(f"<p>{esc(sel_fn(c, idx, info))}</p>")
            rows, cls = rows_fn(c, info)
            heads = [t("col.period_role")] + [t("col." + k2) for k2 in head_keys]
            parts.append(table(heads, rows, classes=cls))
            if ev == "C":
                parts.append(f"<p class='muted'>{esc(t('research.c_levels'))}</p>")
                parts.append(_levels_table(c, info))
    # --- calendar-year block stats
    parts.append(f'<h2 id="blocks">{esc(t("research.h_blocks"))}</h2>')
    parts.append(f"<p>{esc(t('research.blocks_intro', first=f.first_date, last=f.last_date))}</p>")
    bstats = facts.stats_rows(f.bucket, "bucket")
    rstats = facts.stats_rows(f.risk, "risk_level")
    idxs = f.indices
    parts.append(f"<h3>{esc(t('research.h_blocks_dd'))}</h3>")
    items = [
        _cy_takeaway(c, bstats, i, BUCKETS, lambda b: f"d25 {b}", "research.cy_bucket")
        for i in idxs
    ]
    parts.append(ul([esc(x) for x in items if x]))
    parts.append(_stat_table(c, bstats, idxs, BUCKETS, lambda b: f"d25 {b}"))
    parts.append(f"<h3>{esc(t('research.h_blocks_risk'))}</h3>")
    items = [_cy_takeaway(c, rstats, i, RISK_LEVELS, c.risk, "research.cy_risk") for i in idxs]
    parts.append(ul([esc(x) for x in items if x]))
    parts.append(_stat_table(c, rstats, idxs, RISK_LEVELS, c.risk))
    parts.append(f'<p class="muted">{esc(t("research.blocks_note"))}</p>')

    # --- FTD outcomes (descriptive)
    parts.append(f'<h2 id="ftd-outcomes">{esc(t("research.h_ftd_out"))}</h2>')
    parts.append(f"<p>{esc(t('research.ftd_out_intro'))}</p>")
    orows = []
    for idx in idxs:
        st = f.summary["per_index"][idx]
        os_ = st["outcome_stats"]
        for k in ("fwd_5", "fwd_10", "fwd_20", "fwd_60", "max_dd_60"):
            m = os_.get(k)
            if m:
                orows.append(
                    [
                        f"{c.name(idx)} / {t('col.' + k)}",
                        integer(m["n"]),
                        spct(m["median"], 2),
                        f"{spct(m['p25'])} .. {spct(m['p75'])}",
                    ]
                )
    parts.append(table([t("col.group"), "n", t("col.median"), t("col.iqr")], orows))

    # --- R: forward record (sessions after the frozen test end)
    parts.extend(forward_r_section(c))

    # --- R2: leverage overlay
    parts.extend(overlay_section(c))

    # --- caveats
    parts.append(f'<h2 id="caveats">{esc(t("research.h_caveats"))}</h2>')
    sel_tests = [
        info["test"]["selected"]["n_trades"]
        for info in facts.evaluation(f, "A").values()
        if info["selected"] != info["baseline"]
    ]
    caveats = [
        t(
            "research.cv_multiple",
            n_total=cnt["total_variants"],
            n_ftd=cnt["ftd_variants"],
            n_dd=cnt["dd_variants"],
        ),
        t("research.cv_small_n", n=min(sel_tests)) if sel_tests else t("research.cv_small_n_none"),
        t("research.cv_overlap"),
        *[
            t("research.cv_trade_overlap", idx=c.name(o["index"]), n=o["n"], k=o["k"], h=o["h"])
            for o in facts.ftd_overlap(f)
        ],
        t("research.cv_price_index"),
        t("research.cv_cash"),
        t("research.cv_volume", n_issues=integer(facts.same_volume_count(f))),
        t("research.cv_one_path"),
        t("research.cv_not_ibd"),
    ]
    parts.append(ul([esc(x) for x in caveats]))

    parts.append(f'<h2 id="files">{esc(t("research.h_files"))}</h2>')
    parts.append(
        ul(
            [
                link("../../research/variants.csv", "variants.csv")
                + " - "
                + esc(t("research.f_variants")),
                link("../../research/test_results.csv", "test_results.csv")
                + " - "
                + esc(t("research.f_test")),
                link("../../research/selected.json", "selected.json")
                + " - "
                + esc(t("research.f_selected")),
                link("../../research/report.html", "report.html")
                + " - "
                + esc(t("research.f_report")),
                link("../../research/overlay_variants.csv", "overlay_variants.csv")
                + " - "
                + esc(t("research.f_ov_variants")),
                link("../../research/overlay_test_results.csv", "overlay_test_results.csv")
                + " - "
                + esc(t("research.f_ov_test")),
                link("../../research/overlay_selected.json", "overlay_selected.json")
                + " - "
                + esc(t("research.f_ov_selected")),
                link("../../data/dd_bucket_stats.csv", "dd_bucket_stats.csv")
                + ", "
                + link("../../data/risk_level_stats.csv", "risk_level_stats.csv"),
            ]
        )
    )
    return "\n".join(parts)


# --------------------------------------------------------------------------------- top page


def _latest_ftd_rows(c: Ctx, n: int = 6) -> str:
    t = c.t
    ev = sorted(c.f.ftd, key=lambda r: (r["ftd_date"], r["index"]), reverse=True)[:n]
    rows = [
        [
            c.name(r["index"]),
            r["ftd_date"],
            r["detected_on"],
            r["ftd_day_number"],
            f"{float(r['gain_pct']):.2f}%",
            c.yesno(r["invalidated_within_60"]),
            r["invalidation_date"] or "-",
        ]
        for r in ev
    ]
    heads = [
        t("col.index"),
        "ftd_date",
        "detected_on",
        t("col.day"),
        t("col.gain"),
        t("col.invalidated"),
        t("col.invalidation_date"),
    ]
    return table(heads, rows)


def expiry_cell(c: Ctx, r: dict) -> tuple[str, str]:
    """(text, sort key) of a DD's expiry. An active DD whose expiry session lies after the
    last session of the data has a blank expires_on; its date is computed from the XNYS
    calendar and labelled as scheduled."""
    if r["expires_on"]:
        return r["expires_on"], r["expires_on"]
    if not r["invalidated_on"]:
        sched = facts.scheduled_expiry(r["date"])
        return f"{sched} {c.t('events.scheduled')}", sched
    return "-", ""


def _latest_dd_rows(c: Ctx, n: int = 8) -> str:
    t = c.t
    ev = sorted(c.f.dd, key=lambda r: (r["date"], r["index"]), reverse=True)[:n]
    rows = [
        [
            c.name(r["index"]),
            r["date"],
            f"{float(r['pct_change']):.2f}%",
            f"{float(r['volume_change_pct']):+.1f}%",
            expiry_cell(c, r)[0],
            c.t("reason." + (r["removal_reason"] or "active")),
        ]
        for r in ev
    ]
    heads = [
        t("col.index"),
        t("col.date"),
        t("col.change"),
        t("col.volume_change"),
        t("col.expires_on"),
        t("col.status"),
    ]
    return table(heads, rows)


def top_body(c: Ctx) -> str:
    t, f = c.t, c.f
    parts = [f'<p class="lead">{esc(t("top.lead", last=f.last_date))}</p>']
    parts.append(f'<h2>{esc(t("top.h_state"))}</h2><div class="cards">')
    for s in facts.index_states(f):
        day1 = s["rally_day1_date"] or "-"
        parts.append(
            f"""<section class="card">
<h3>{esc(c.name(s["index"]))}</h3>
<p class="muted">{esc(s["date"])} / {esc(t("top.close"))} {s["close"]:,.2f}</p>
<dl>
<dt>{esc(t("top.risk"))}</dt><dd><span class="badge lv-{esc(s["risk"])}">{esc(c.risk(s["risk"]))}</span></dd>
<dt>{esc(t("top.ftd_state"))}</dt><dd>{esc(c.state(s["ftd_state"]))}</dd>
<dt>{esc(t("top.rally_day1"))}</dt><dd>{esc(day1)}</dd>
<dt>{esc(t("top.d_counts"))}</dt><dd>{s["d5"]} / {s["d15"]} / {s["d25"]}</dd>
<dt>{esc(t("top.today_dd"))}</dt><dd>{esc(t("yes" if s["is_dd"] else "no"))}</dd>
</dl>
</section>"""
        )
    parts.append("</div>")
    parts.append(f'<p class="muted">{esc(t("top.state_note"))}</p>')

    parts.append(f"<h2>{esc(t('top.h_research'))}</h2>")
    parts.append(f"<p>{esc(t('top.research_intro'))}</p>")
    parts.append(ul([esc(x) for x in key_points(c)]))
    parts.append(f'<p class="muted">{esc(t("research.conclusions_note"))}</p>')
    parts.append(
        f"<p>{link('research.html', t('top.research_link'))} / "
        f"{link('research.html#caveats', t('top.caveats_link'))} / "
        f"{link('definitions.html', t('top.definitions_link'))}</p>"
    )

    parts.append(f"<h2>{esc(t('top.h_latest'))}</h2>")
    parts.append(f"<h3>{esc(t('top.latest_ftd'))}</h3>")
    parts.append(_latest_ftd_rows(c))
    parts.append(f"<h3>{esc(t('top.latest_dd'))}</h3>")
    parts.append(_latest_dd_rows(c))
    parts.append(
        f"<p>{link('events.html', t('top.all_events'))} / {link('chart.html', t('top.chart_link'))}</p>"
    )
    return "\n".join(parts)


# ----------------------------------------------------------------------------- events page


def _year(d: str) -> str:
    return d[:4]


def events_body(c: Ctx) -> str:
    t, f = c.t, c.f
    out_by = {(r["index"], r["ftd_date"]): r for r in f.outcomes}
    years = sorted({_year(r["ftd_date"]) for r in f.ftd} | {_year(r["date"]) for r in f.dd})
    idx_opts = "".join(f'<option value="{esc(i)}">{esc(c.name(i))}</option>' for i in f.indices)
    year_opts = "".join(f'<option value="{y}">{y}</option>' for y in reversed(years))
    controls = f"""<div class="filters">
<label>{esc(t("events.f_index"))} <select id="f-index"><option value="">{esc(t("events.all"))}</option>{idx_opts}</select></label>
<label>{esc(t("events.f_year"))} <select id="f-year"><option value="">{esc(t("events.all"))}</option>{year_opts}</select></label>
</div>"""

    def th(label: str, kind: str = "text") -> str:
        return f'<th data-type="{kind}">{esc(label)}</th>'

    def td(text, key=None, cls="num") -> str:
        v = f' data-v="{esc(key)}"' if key is not None else ""
        return f'<td class="{cls}"{v}>{esc(text)}</td>'

    ftd_heads = [
        th(t("col.index")),
        th("ftd_date"),
        th("detected_on"),
        th(t("col.day"), "num"),
        th(t("col.gain"), "num"),
        th(t("col.volume_vs_prev"), "num"),
        th(t("col.vol_above_avg")),
        th(t("col.invalidated_60")),
        th(t("col.invalidation_date")),
        th("fwd 5", "num"),
        th("fwd 10", "num"),
        th("fwd 20", "num"),
        th("fwd 60", "num"),
    ]
    rows = []
    for r in sorted(f.ftd, key=lambda r: (r["ftd_date"], r["index"]), reverse=True):
        o = out_by.get((r["index"], r["ftd_date"]), {})
        cells = [
            td(c.name(r["index"]), r["index"], "l"),
            td(r["ftd_date"], r["ftd_date"], "l"),
            td(r["detected_on"], r["detected_on"], "l"),
            td(r["ftd_day_number"], r["ftd_day_number"]),
            td(f"{float(r['gain_pct']):.2f}%", r["gain_pct"]),
            td(f"{float(r['volume_vs_prev_pct']):+.1f}%", r["volume_vs_prev_pct"]),
            td(c.yesno(r["volume_above_avg"]), r["volume_above_avg"], "l"),
            td(c.yesno(r["invalidated_within_60"]), r["invalidated_within_60"], "l"),
            td(r["invalidation_date"] or "-", r["invalidation_date"], "l"),
        ]
        for k in ("fwd_5", "fwd_10", "fwd_20", "fwd_60"):
            v = fnum(o.get(k))
            cells.append(td(spct(v), "" if v is None else v))
        rows.append(
            f'<tr data-index="{esc(r["index"])}" data-year="{_year(r["ftd_date"])}">{"".join(cells)}</tr>'
        )
    ftd_table = f'<div class="tablewrap"><table class="sortable" id="tbl-ftd"><thead><tr>{"".join(ftd_heads)}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'

    dd_heads = [
        th(t("col.index")),
        th(t("col.date")),
        th(t("col.change"), "num"),
        th(t("col.volume_change"), "num"),
        th(t("col.expires_on")),
        th(t("col.invalidated_on")),
        th(t("col.status")),
    ]
    rows = []
    for r in sorted(f.dd, key=lambda r: (r["date"], r["index"]), reverse=True):
        reason = r["removal_reason"] or "active"
        cells = [
            td(c.name(r["index"]), r["index"], "l"),
            td(r["date"], r["date"], "l"),
            td(f"{float(r['pct_change']):.2f}%", r["pct_change"]),
            td(f"{float(r['volume_change_pct']):+.1f}%", r["volume_change_pct"]),
            td(*expiry_cell(c, r), "l"),
            td(r["invalidated_on"] or "-", r["invalidated_on"], "l"),
            td(t("reason." + reason), reason, "l"),
        ]
        rows.append(
            f'<tr data-index="{esc(r["index"])}" data-year="{_year(r["date"])}">{"".join(cells)}</tr>'
        )
    dd_table = f'<div class="tablewrap"><table class="sortable" id="tbl-dd"><thead><tr>{"".join(dd_heads)}</tr></thead><tbody>{"".join(rows)}</tbody></table></div>'

    return f"""<p class="lead">{esc(t("events.lead"))}</p>
<p class="muted">{esc(t("events.note_units"))}</p>
{controls}
<h2>{esc(t("events.h_ftd"))} <span class="count" id="count-ftd"></span></h2>
<p class="muted">{esc(t("events.ftd_note"))}</p>
{ftd_table}
<h2>{esc(t("events.h_dd"))} <span class="count" id="count-dd"></span></h2>
<p class="muted">{esc(t("events.dd_note"))}</p>
{dd_table}
"""


EVENTS_TAIL = '<script src="../assets/events.js"></script>\n'


# ------------------------------------------------------------------------------ chart page


CHART_KEYS = (
    "close",
    "dd",
    "ftd",
    "active_dd",
    "risk",
    "all",
    "detected",
    "gain",
    "y_close",
    "y_dd",
    "range_6m",
    "range_1y",
    "range_5y",
    "range_all",
    "lev_band",
    "lev_toggle",
    "hover_counts",
)


def chart_body(c: Ctx) -> str:
    t = c.t
    chips = "".join(
        f'<span><span class="chip lv-{lv}"></span>{esc(t("chart.leg_" + lv))}</span>'
        for lv in ("NORMAL", "CAUTION", "HIGH", "SEVERE")
    )
    d = facts.definition_values()
    lev = ""
    if c.f.overlay and any(i["bands"] for i in c.f.overlay["indices"].values()):
        chips += f'<span><span class="chip lev-band"></span>{esc(t("chart.lev_band"))}</span>'
        lev = (
            f'<p><label><input type="checkbox" id="lev-toggle" checked> {esc(t("chart.lev_toggle"))}'
            f'</label></p>\n<p class="muted">{esc(t("chart.lev_note"))}</p>\n'
        )
    return f"""<p class="lead">{esc(t("chart.lead"))}</p>
<div class="legend">{chips}</div>
{lev}
<p class="muted">{esc(t("chart.band_note", c25=d["caution_d25"], h25=d["high_d25"], h15=d["high_d15"], h5=d["high_d5"], s25=d["severe_d25"], s15=d["severe_d15"], sm=d["severe_ma_d25"]))}</p>
<p class="muted">{esc(t("chart.marker_note"))}</p>
<div id="charts"></div>
<noscript><p class="callout">{esc(t("chart.noscript"))}</p></noscript>
"""


def chart_head(c: Ctx) -> str:
    return (
        f'<script src="{chartdata.PLOTLY_SRC}" integrity="{chartdata.PLOTLY_SRI}" '
        'crossorigin="anonymous"></script>\n'
    )


def chart_tail(c: Ctx) -> str:
    strings = {k: c.t("chart." + k) for k in CHART_KEYS}
    return (
        f"<script>window.CHART_I18N = {json.dumps(strings, ensure_ascii=False)};</script>\n"
        '<script src="../assets/chart_data.js"></script>\n'
        '<script src="../assets/chart.js"></script>\n'
    )


# ------------------------------------------------------------------------- definitions page


def definitions_body(c: Ctx) -> str:
    t, f = c.t, c.f
    d = facts.definition_values()
    p = []
    p.append(f'<p class="lead">{esc(t("def.lead"))}</p>')
    p.append(f'<div class="callout">{esc(t("def.not_ibd_box"))}</div>')

    p.append(f"<h2>{esc(t('def.h_ftd'))}</h2>")
    p.append(
        ul(
            [
                esc(
                    t(
                        "def.ftd_swing",
                        pct=g(d["min_correction_pct"]),
                        days=d["min_down_days"],
                        back=d["swing_lookback"],
                    )
                ),
                esc(t("def.ftd_day1")),
                esc(
                    t(
                        "def.ftd_window",
                        a=d["ftd_day_start"],
                        b=d["ftd_day_end"],
                        gain=g(d["gain_min"]),
                    )
                ),
                esc(t("def.ftd_gain_grades", rec=g(d["gain_rec"]), strong=g(d["gain_strong"]))),
                esc(t("def.ftd_bars", analysis=d["analysis_bars"], fetch=d["ftd_fetch_bars"])),
                esc(t("def.ftd_invalid", h=d["invalidation_horizon"])),
            ]
        )
    )
    p.append(f"<h2>{esc(t('def.h_dd'))}</h2>")
    p.append(
        ul(
            [
                esc(t("def.dd_rule", decline=f"{d['dd_decline_pct']:g}")),
                esc(t("def.dd_expire", n=d["dd_expiration"], m=d["dd_expiration"] + 1)),
                esc(
                    t(
                        "def.dd_invalid",
                        gain=g(d["dd_invalidation_gain_pct"]),
                        src=t(f"def.src_{d['dd_invalidation_source']}"),
                    )
                ),
                esc(t("def.dd_counts")),
            ]
        )
    )
    p.append(f"<h3>{esc(t('def.h_levels'))}</h3>")
    lv = [
        [c.risk("CAUTION"), t("def.lv_caution", c25=d["caution_d25"])],
        [c.risk("HIGH"), t("def.lv_high", h25=d["high_d25"], h15=d["high_d15"], h5=d["high_d5"])],
        [
            c.risk("SEVERE"),
            t(
                "def.lv_severe",
                s25=d["severe_d25"],
                s15=d["severe_d15"],
                sm=d["severe_ma_d25"],
                ema=d["ema_period"],
                sma=d["sma_period"],
            ),
        ],
        [c.risk("NORMAL"), t("def.lv_normal")],
    ]
    p.append(table([t("col.level"), t("col.condition")], [[esc(a), esc(b)] for a, b in lv]))
    p.append(f'<p class="muted">{esc(t("def.source_note"))}</p>')

    p.append(f"<h2>{esc(t('def.h_ibd'))}</h2>")
    p.append(
        ul(
            [
                esc(t("def.ibd_correction")),
                esc(t("def.ibd_gain")),
                esc(t("def.ibd_dd")),
                esc(t("def.ibd_index")),
                esc(t("def.ibd_freq", n_ftd=len(f.ftd), first=f.first_date, last=f.last_date)),
            ]
        )
    )

    p.append(f"<h2>{esc(t('def.h_base'))}</h2>")
    p.append(f"<p>{esc(t('def.base_intro', win=facts.D25_WINDOW))}</p>")
    items = []
    for b in facts.base_rates(f):
        items.append(
            esc(
                t(
                    "def.base_item",
                    idx=c.name(b["index"]),
                    dd=integer(b["dd_days"]),
                    n=integer(b["sessions"]),
                    share=pct(b["dd_share"]),
                    win=b["window"],
                    exp=num(b["expected_window"], 1),
                    mean=num(b["mean_d25"], 1),
                    s25=b["severe_d25"],
                    s15=b["severe_d15"],
                    sm=b["severe_ma_d25"],
                    share_sev=pct(b["share_d25_severe"]),
                    lv_sev=pct(b["levels"]["SEVERE"]),
                    lv_norm=pct(b["levels"]["NORMAL"]),
                    sev_days=integer(b["severe_days"]),
                    share_not=pct(b["share_severe_not_d25"]),
                )
            )
        )
    p.append(ul(items))
    p.append(f"<p>{esc(t('def.base_why'))}</p>")

    p.append(f"<h2>{esc(t('def.h_pit'))}</h2>")
    lag = f.summary["counts"]
    max_lag = max((v["max_lag_sessions"] or 0) for v in f.summary["per_index"].values())
    p.append(
        ul(
            [
                esc(t("def.pit_replay", n=d["ftd_fetch_bars"], m=d["dd_window_bars"])),
                esc(t("def.pit_detected")),
                esc(t("def.pit_lag", max_lag=max_lag, n=lag["ftd_events"])),
                esc(t("def.pit_outcome")),
                esc(t("def.pit_start", first=f.first_date)),
            ]
        )
    )
    p.append(f"<h2>{esc(t('def.h_data_issues'))}</h2>")
    p.append(f"<p>{esc(t('def.data_issues', n=integer(len(f.issues))))}</p>")
    return "\n".join(p)


# ---------------------------------------------------------------------------------- data page


DATA_FILES = [
    ("data/daily_status.csv", "daily_status"),
    ("data/ftd_events.csv", "ftd_events"),
    ("data/distribution_days.csv", "distribution_days"),
    ("data/rally_attempts.csv", "rally_attempts"),
    ("data/ftd_outcomes.csv", "ftd_outcomes"),
    ("data/dd_bucket_stats.csv", "dd_bucket_stats"),
    ("data/risk_level_stats.csv", "risk_level_stats"),
    ("data/data_issues.csv", "data_issues"),
    ("data/summary.json", "summary"),
    ("data/data_dictionary.md", "data_dictionary"),
    ("research/variants.csv", "variants"),
    ("research/test_results.csv", "test_results"),
    ("research/selected.json", "selected"),
    ("research/report.html", "report"),
    ("research/overlay_variants.csv", "overlay_variants"),
    ("research/overlay_test_results.csv", "overlay_test_results"),
    ("research/overlay_selected.json", "overlay_selected"),
    ("site/key_facts.json", "key_facts"),
    ("validation.json", "validation"),
]


def data_body(c: Ctx) -> str:
    t, f = c.t, c.f
    rows = []
    for path, key in DATA_FILES:
        p = f.run / path
        # validation.json is written after the site, so its size is never shown: the page must
        # render the same before and after the gate has run
        size = f"{p.stat().st_size / 1024:,.0f} KB" if p.exists() and key != "validation" else "-"
        rows.append([link("../../" + path, path), esc(t("data.d_" + key)), esc(size)])
    return f"""<div class="callout">{esc(t("notice.local"))} {esc(t("data.fmp_notice"))}</div>
<p class="lead">{esc(t("data.lead"))}</p>
{table([t("col.file"), t("col.description"), t("col.size")], rows)}
<h2>{esc(t("data.h_units"))}</h2>
{ul([esc(t("data.unit_pct_points")), esc(t("data.unit_fraction")), esc(t("data.unit_pct_change")), esc(t("data.unit_dates"))])}
<p>{link("../../data/data_dictionary.md", t("data.dict_link"))}</p>
<h2>{esc(t("data.h_ai"))}</h2>
<p>{esc(t("data.ai_note"))} {link("../llms.txt", "llms.txt")}</p>
<h2>{esc(t("data.h_range"))}</h2>
<p>{esc(t("data.range", first=f.first_date, last=f.last_date, fetched=f.summary["data_range"]["fetched_first"]))}</p>
"""


# -------------------------------------------------------------------------------- chooser


def chooser_html(f: Facts) -> str:
    ja, en = Translator("ja"), Translator("en")
    return f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(en("site.title"))}</title>
<link rel="stylesheet" href="assets/style.css">
</head>
<body>
<main class="wrap chooser">
<h1>{esc(en("site.title"))}</h1>
<p class="lead">{esc(ja("chooser.lead"))}<br>{esc(en("chooser.lead"))}</p>
<p class="choose"><a class="btn" href="ja/index.html">日本語</a> <a class="btn" href="en/index.html">English</a></p>
<p class="notice">{esc(ja("notice.local"))}<br>{esc(en("notice.local"))}</p>
<p class="muted">{esc(en("footer.data", last=f.last_date, generated=f.summary.get("generated_at", "-"), ref=str(f.summary.get("vendor_ref", "-"))[:10]))}</p>
</main>
</body>
</html>
"""


def llms_txt(f: Facts) -> str:
    from webgen import dictionary

    lines = [
        "# ftd-dd-dashboard",
        "",
        "> Local, personal-use history of Follow-Through Day (FTD) and Distribution Day (DD)",
        "> signals for the S&P 500 (^GSPC) and Nasdaq Composite (^IXIC), replayed session by",
        "> session with the rules of the vendored claude-trading-skills code. NOT official IBD",
        "> signals. Price data comes from FMP and must not be redistributed.",
        "",
        f"Data through {f.last_date} (XNYS session). Paths below are relative to the run",
        "directory, i.e. the parent of this site/ folder.",
        "",
        "## Read first",
        "",
        "- data/data_dictionary.md: every column of every CSV, with type and unit",
        "  (percent points vs fractions differ between files; read the unit column).",
        "- data/summary.json: counts, data range, vendor reference, per-index outcome statistics.",
        "- validation.json: PASS/FAIL gate result, checks, sha256 of every file.",
        "",
        "## Data (CSV, UTF-8, header row)",
        "",
    ]
    for path in dictionary.files():
        if path.startswith("data/"):
            lines.append(f"- {path}")
    lines += [
        "",
        "## Research (design period vs test period, per index)",
        "",
        "- research/variants.csv: every variant, design-period metrics",
        "- research/test_results.csv: test-period metrics of the baseline and the picked variants",
        "- research/selected.json: split, selection criteria, baseline and picked variants",
        "- research/overlay_*.csv, overlay_selected.json (R2): leverage after an FTD vs buy and hold,",
        "  12 variants, borrow-cost cases; research only, not a trading recommendation",
        "",
        "## Pages (human readable, ja/ and en/)",
        "",
    ]
    for p in layout.PAGES:
        lines.append(f"- site/en/{p}.html")
    lines += [
        "",
        "## Caveats an analysis should keep",
        "",
        "- Many variants were tried; the picked ones look better in the design period by selection.",
        "- Sample sizes are small; windows of the daily statistics overlap.",
        "- Price indices without dividends; cash earns 0% in the exposure evaluation.",
        "- Volume quality of the early years is unverified.",
        "- R2: the test period was previewed before R2 was designed (see",
        "  overlay_selected.json test_period_contamination), so its test result is not a clean",
        "  out-of-sample test. Sessions after the data date in that block are an untouched holdout.",
        "- pct_change is a percent value in distribution_days.csv and a fraction in daily_status.csv.",
        "",
    ]
    return "\n".join(lines)
