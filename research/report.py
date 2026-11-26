"""Local HTML research report (Japanese UI text, English column names). Plain HTML/CSS."""

from __future__ import annotations

import html
from datetime import UTC, datetime

from research import freeze, outputs, selection, variants
from research.split import DESIGN_END, DESIGN_START, HORIZON, TEST_START

CSS = """
body{font-family:-apple-system,'Hiragino Sans',sans-serif;max-width:1180px;margin:24px auto;
padding:0 16px;color:#1a1a1a;line-height:1.55}
h1{font-size:22px}h2{font-size:18px;margin-top:34px;border-bottom:2px solid #333;padding-bottom:4px}
h3{font-size:15px;margin-top:22px}
table{border-collapse:collapse;font-size:12.5px;margin:6px 0 14px}
th,td{border:1px solid #bbb;padding:3px 8px;text-align:right}
th{background:#eee}td.l,th.l{text-align:left}
tr.test td{background:#fff7e0}tr.bh td{color:#555;font-style:italic}
.note{background:#f3f3f3;padding:8px 12px;font-size:13px}
code{background:#eee;padding:0 3px}
"""


EM_NOTE = (
    "「投資比率一定」の列は、その案の平均投資比率 (avg_exposure) を毎日一定に保ち、"
    "残りを現金 (リターン 0) とした比較対象である。投資比率を下げるだけでも最大下落は小さくなるため、"
    "DD シグナルに意味があるかは、同じ平均投資比率の一定運用と比べて初めて分かる。"
    "この列は参考表示で、案の選択には使っていない。"
)


def e(s) -> str:
    return html.escape(str(s))


def pct(x, d=1) -> str:
    return "-" if x is None else f"{x * 100:.{d}f}%"


def num(x, d=2) -> str:
    return "-" if x is None else f"{x:.{d}f}"


def intv(x) -> str:
    return "-" if x is None else f"{int(x)}"


def table(headers: list[str], rows: list[list[str]], classes: list[str] | None = None) -> str:
    th = "".join(
        f'<th class="l">{e(h)}</th>' if i < 2 else f"<th>{e(h)}</th>" for i, h in enumerate(headers)
    )
    body = []
    for k, r in enumerate(rows):
        cls = f' class="{classes[k]}"' if classes and classes[k] else ""
        tds = "".join(
            f'<td class="l">{e(c)}</td>' if i < 2 else f"<td>{c}</td>" for i, c in enumerate(r)
        )
        body.append(f"<tr{cls}>{tds}</tr>")
    return f"<table><tr>{th}</tr>{''.join(body)}</table>"


def _rows_for(ev: str, info: dict) -> tuple[list[list[str]], list[str], list[str]]:
    rows, cls = [], []
    for period in ("design", "test"):
        for role in ("baseline", "selected"):
            r = info[period][role]
            label = f"{role} ({info[role]})"
            if role == "selected" and info["selected"] == info["baseline"]:
                label = f"selected = baseline ({info[role]})"
            if ev == "A":
                vals = [
                    intv(r["n_trades"]),
                    pct(r["win_rate"], 0),
                    pct(r["median"], 2),
                    pct(r["mean"], 2),
                    pct(r["p10"], 1),
                    pct(r["p90"], 1),
                    num(r["profit_factor"]),
                    intv(r["n_stopped"]),
                    f"{intv(r['n_dropped_incomplete_window'])} / {intv(r['n_skipped_invalidated_at_entry'])}",
                ]
            elif ev == "B":
                vals = [
                    pct(r["cagr"]),
                    pct(r["max_dd"]),
                    pct(r["vol"]),
                    pct(r["avg_exposure"], 0),
                    pct(r["bh_cagr"]),
                    pct(r["bh_max_dd"]),
                    pct(r["bh_vol"]),
                    pct(r["em_cagr"]),
                    pct(r["em_max_dd"]),
                ]
            else:
                vals = [
                    f"{pct(r['p_elevated'])} (n={intv(r['n_elevated'])})",
                    f"{pct(r['p_normal'])} (n={intv(r['n_normal'])})",
                    pct(r["spread"]),
                    intv(r["n_dropped_incomplete_window"]),
                ]
            rows.append([period, label, *vals])
            cls.append("test" if period == "test" else "")
    return rows, cls, []


HEAD = {
    "A": [
        "period",
        "variant",
        "n_trades",
        "win_rate",
        "median",
        "mean",
        "p10",
        "p90",
        "profit_factor",
        "n_stopped",
        "dropped / skipped",
    ],
    "B": [
        "period",
        "variant",
        "CAGR",
        "max_dd",
        "vol",
        "avg_exposure",
        "B&H CAGR",
        "B&H max_dd",
        "B&H vol",
        "投資比率一定 CAGR",
        "投資比率一定 max_dd",
    ],
    "C": ["period", "variant", "P(dd>=10% | elevated)", "P(dd>=10% | normal)", "spread", "dropped"],
}


def _top_table(ev: str, rows: list[dict], index: str, k: int = 8) -> str:
    mine = [r for r in rows if r["index"] == index]
    if ev == "A":
        q = [r for r in mine if r["n_trades"] >= selection.MIN_TRADES_A and r["median"] is not None]
        q.sort(key=lambda r: (-r["median"], r["variant_id"]))
        body = [
            [r["variant_id"], "", intv(r["n_trades"]), pct(r["median"], 2), pct(r["win_rate"], 0)]
            for r in q[:k]
        ]
        return table(["variant", "", "n_trades", "median", "win_rate"], body)
    if ev == "B":
        q = [
            r
            for r in mine
            if r["cagr"] is not None and r["cagr"] >= r["bh_cagr"] - selection.CAGR_TOLERANCE_B
        ]
        q.sort(key=lambda r: (-r["max_dd"], -r["cagr"], r["variant_id"]))
        body = [
            [r["variant_id"], "", pct(r["cagr"]), pct(r["max_dd"]), pct(r["avg_exposure"], 0)]
            for r in q[:k]
        ]
        return table(["variant", "", "CAGR", "max_dd", "avg_exposure"], body)
    q = [
        r
        for r in mine
        if r["n_elevated"] >= selection.MIN_DAYS_C
        and r["n_normal"] >= selection.MIN_DAYS_C
        and r["spread"] is not None
    ]
    q.sort(key=lambda r: (-r["spread"], r["signal_id"]))
    body = [
        [r["signal_id"], "", intv(r["n_elevated"]), pct(r["spread"]), pct(r["p_elevated"])]
        for r in q[:k]
    ]
    return table(["signal", "", "n_elevated", "spread", "P(dd | elevated)"], body)


def _level_table(result: dict, index: str) -> str:
    cmp = outputs.compared(result)["C"][index]
    rows, cls = [], []
    for period in ("design", "test"):
        r = cmp[period]["baseline"]
        if r.get("p_NORMAL") is None and r.get("n_NORMAL") in (None, 0):
            continue
        rows.append(
            [period, cmp["baseline"]]
            + [
                f"{pct(r.get(f'p_{lv}'))} (n={intv(r.get(f'n_{lv}'))})"
                for lv in ("NORMAL", "CAUTION", "HIGH", "SEVERE")
            ]
        )
        cls.append("test" if period == "test" else "")
    return table(["period", "signal", "NORMAL", "CAUTION", "HIGH", "SEVERE"], rows, cls)


OVERLAY_HEAD = [
    "period",
    "variant",
    "CAGR",
    "max_dd",
    "vol",
    "levered share",
    "episodes",
    "worst levered dd",
    "margin stress days",
    "B&H CAGR",
    "B&H max_dd",
    "投資比率一定 CAGR",
    "投資比率一定 max_dd",
]


def _overlay_row(period: str, label: str, m: dict) -> list[str]:
    return [
        period,
        label,
        pct(m["cagr"]),
        pct(m["max_dd"]),
        pct(m["vol"]),
        pct(m["levered_share"], 0),
        intv(m["n_episodes"]),
        pct(m["worst_levered_dd"]),
        intv(m["margin_stress_days"]),
        pct(m["bh_cagr"]),
        pct(m["bh_max_dd"]),
        pct(m["em_cagr"]),
        pct(m["em_max_dd"]),
    ]


def _tie_metrics(ev: str, m: dict) -> str:
    if ev == "A":
        return (
            f"取引 {intv(m['n_trades'])} 回、中央値 {pct(m['median'], 2)}、平均 {pct(m['mean'], 2)}"
        )
    if ev == "B":
        return f"年率 {pct(m['cagr'])}、最大下落 {pct(m['max_dd'])}"
    return f"差 {pct(m['spread'])}"


def _amendments_block(sel: dict) -> str:
    """Post-hoc amendments, tie rule and the grid freeze (selected.json / overlay_selected.json)."""
    items = []
    for x in sel.get("tie_break_comparison", []):
        b, a2 = x["before"], x["after"]
        items.append(
            f"<li>同点の決め方の変更で選ばれた案が変わった: {e(x['evaluation'])} {e(x['index'])}、"
            f"以前 <code>{e(b['id'])}</code> → 今 <code>{e(a2['id'])}</code>。"
            f"検証期間の結果は、以前の案が {_tie_metrics(x['evaluation'], b['test'])}、"
            f"今の案が {_tie_metrics(x['evaluation'], a2['test'])}。</li>"
        )
    for a in sel.get("post_hoc_amendments", []):
        items.append(
            f"<li><b>事後の変更 {e(a['date'])}</b>: {e(a['what'])}。理由: {e(a['reason'])}。"
            "結果を見たあとに決めたため、選択に影響した可能性がある。</li>"
        )
    if sel.get("tie_rule"):
        items.append(f"<li>同点の決め方: {e(sel['tie_rule'])}</li>")
    fr = sel.get("freeze")
    if fr:
        items.append(
            f"<li>案の格子の固定: 登録日 {e(fr['pre_registered'])}、"
            f"sha256 <code>{e(fr['grid_sha256'])}</code>。公開のときに現在の格子と照合する。</li>"
        )
        items.append(f"<li>{e(freeze_sentence(fr, sel.get('freeze_provenance')))}</li>")
    return "<ul class='note'>" + "".join(items) + "</ul>" if items else ""


def freeze_sentence(fr: dict, prov: dict | None) -> str:
    """Honest account of when the grid hash file was made, from the stored provenance."""
    ts = fr.get("frozen_at_utc")
    if not ts:
        return "格子のハッシュのファイルを作った時刻は記録されていません。"
    local = freeze.local_date_of(ts)
    head = (
        f"案の格子は、計算の前に docs/plan.md に書きました。ハッシュのファイルで固定したのは、"
        f"その後の UTC {ts} です。登録日 {fr['pre_registered']} は手元の日付で、太平洋時間 "
        f"(UTC-8) の {local} にあたります。"
        + ("UTC では日付が翌日になります。" if ts[:10] != local else "")
    )
    if not prov or not prov.get("n_runs"):
        return head + "固定より前の実行は見つかりませんでした。"
    n, f = prov["n_runs"], prov["file"]
    if prov["identical_bytes"]:
        state = f"どれもバイト単位で同じでした (sha256 の先頭 {prov['runs'][0]['sha256'][:12]})"
    elif prov["identical_rows"]:
        state = "見出しの行を除いて、データの行はすべて同じでした"
    else:
        state = "同じではありませんでした"
    return (
        head + f"ハッシュのファイルを作るより前の実行は {n} 回 (最初は {prov['first_run']}) あり、"
        f"それらの {f} は{state}。"
    )


def _contamination_block(ov: dict) -> str:
    """R2's test period was seen in a preview before R2 was designed: say so prominently."""
    cd = ov["test_period_contamination"]
    pv = cd["preview"]
    lo, hi = pv["levered_share_range"]
    rows = []
    for r in pv["results"]:
        same = abs(r["preview_cagr"] - r["buy_hold_cagr"]) < 1e-9
        rows.append(
            [
                f"{r['index']} {r['period'][0]}-{r['period'][1]}",
                "",
                pct(r["preview_cagr"]),
                "同じ" if same else pct(r["buy_hold_cagr"]),
            ]
        )
    fw = cd["forward_holdout"]
    return (
        "<div class='note' style='border:2px solid #b00;background:#fff3f3'>"
        "<b>検証期間は下見で見ていた (R2 の検証結果は、検証外データによるきれいな確認ではない)</b>"
        f"<p>R2 を設計する前の {e(cd['date'])} に、{pv['period'][0][:4]} 年から {pv['period'][1][:4]} 年までの全期間で"
        f"下見の計算を一度している。条件は、危険度が HIGH または SEVERE の間は入らない、比率 {pv['leverage'] * 100:.0f}%、"
        f"借入金利は年 {pv['borrow_rate'] * 100:.0f}%、SEVERE で 100% に戻す、というもの。"
        f"上乗せしていた日は全体の {lo * 100:.0f}% から {hi * 100:.0f}%。</p>"
        + table(["区分", "", "下見の年率", "持ち続けた年率"], rows)
        + "<p>この下見で入る機会が少ないと分かり、R2 では戻す条件を危険度ではなく、FTD の後に数えた DD の回数に変えた。"
        "案の格子は R2 自身の計算の前に決めて docs/plan.md に書いたが、検証期間はすでに見ていた。</p>"
        f"<p>今後の記録: {e(fw['data_after'])} より後のセッションは、まだ誰も見ていない期間である。"
        "サイトの研究ページに、基準と選んだ案の成績を同じコードで載せる (今は記録開始前)。</p></div>"
    )


def render_overlay(ov: dict) -> list[str]:
    """Chapter for research R2 (docs/plan.md section 3.7); every number from overlay_selected."""
    main = ov["main_cost_model"]
    labels = {c["id"]: c["label"] for c in ov["cost_models"]}
    d0, d1 = ov["split"]["design"]
    t0, t1 = ov["split"]["test"]
    parts = [
        "<h2>8. 研究 R2: FTD 後のレバレッジ上乗せ</h2>",
        "<p>S&amp;P 500 とナスダック総合を常に 100% 持ち、FTD の検出日の引けで比率を 125% または 150% に上げ、"
        "FTD の日より後に数えた DD が k 回に達した日、または FTD が無効になった日の引けで 100% に戻す案を比べる。</p>",
        f"<p class='note'>{e(ov['disclaimer'])} 実際の売買をすすめるものではない。{e(ov['dividend_caveat'])}</p>",
        _contamination_block(ov),
        "<ul>",
        f"<li>案の数: <b>{ov['n_variants']}</b> 案 = k {list(ov['variant_grid']['k'])} × 無効化で戻す {['あり' if x else 'なし' for x in ov['variant_grid']['use_invalidation']]} × 比率 {[f'{x:g}' for x in ov['variant_grid']['leverage']]}。基準は買い持ち 100%。</li>",
        f"<li>期間: 設計期間 {e(d0)} 〜 {e(d1)}、検証期間 {e(t0)} 〜 {e(t1)}。検証期間は基準と選んだ案について一度だけ計算した。"
        "ただし、この検証期間は設計の前に下見で見ていた (上の注意を参照)。</li>",
        f"<li>選択基準 (案の格子と規則は R2 自身の計算の前に決めた。検証期間は下見で見ていたため、結果は検証外データによる確認ではない): {e(ov['criterion'])}</li>",
        f"<li>{_amendments_block(ov)}</li>",
        f"<li>借入費用: 主ケースは {e(labels[main])}。比較として {', '.join(e(labels[c['id']]) for c in ov['cost_models'] if c['id'] != main)}。"
        "費用は (比率 − 1) に対して、カレンダー日数で日割り (1/365)、前日の引けで分かっていた金利を使う。</li>",
        f"<li>追証の目安 (仮置き): 元手 ÷ 建玉が {ov['maintenance_equity_ratio'] * 100:.0f}% を下回る日があったか。"
        "この値は実際の証券会社の規定ではなく、説明用の仮の値である。</li>",
        "</ul>",
    ]
    for idx, info in ov["indices"].items():
        parts.append(f"<h3>{e(idx)}</h3>")
        if info["fallback_to_baseline"]:
            parts.append("<p>設計期間で条件を満たす案がなく、買い持ちを維持した。</p>")
        else:
            p = info["selected_params"]
            parts.append(
                f"<p>選択: <code>{e(info['selected'])}</code> (k={p['k']}, 無効化で戻す={'あり' if p['use_invalidation'] else 'なし'}, "
                f"比率 {p['leverage']:g}。条件を満たす {info['n_qualifying']} 案 / {info['n_considered']} 案)</p>"
            )
        for cid in [main] + [c["id"] for c in ov["cost_models"] if c["id"] != main]:
            rows, cls = [], []
            for period in ("design", "test"):
                for role, m in info[period].items():
                    rows.append(_overlay_row(period, f"{role} / {labels[cid]}", m[cid]))
                    cls.append("test" if period == "test" else "")
            parts.append(f"<p>{e(labels[cid])}</p>")
            parts.append(table(OVERLAY_HEAD, rows, cls))
    fw_rows = []
    for idx, info in ov["indices"].items():
        for role, r in info["forward"].items():
            if role not in ("baseline", "selected"):
                continue
            fw_rows.append(
                [idx, role, "記録開始前", "-", "-"]
                if r["n_days"] == 0
                else [idx, role, intv(r["n_days"]), pct(r["cum_return"], 2), pct(r["max_dd"])]
            )
    parts.append(
        f"<h3>今後の記録 ({e(ov['test_period_contamination']['forward_holdout']['data_after'])} より後)</h3>"
    )
    parts.append(table(["index", "role", "新しいセッション数", "累積の騰落", "最大下落"], fw_rows))
    parts.append("<h3>事後の変更と、その影響</h3><ul>")
    for a in ov.get("post_hoc_amendments", []):
        parts.append(f"<li>{e(a['date'])}: {e(a['what'])}。理由: {e(a['reason'])}。</li>")
    for idx, info in ov["indices"].items():
        eff = info.get("rate_timing_effect")
        if eff:
            parts.append(
                f"<li>金利の取り方の変更の影響 {e(idx)}: 選んだ案の年率 設計期間 {pct(eff['design']['cagr_before'], 4)} → "
                f"{pct(eff['design']['cagr_after'], 4)}、検証期間 {pct(eff['test']['cagr_before'], 4)} → "
                f"{pct(eff['test']['cagr_after'], 4)}。選ばれた案は{'変わった' if eff['pick_changed'] else '変わらなかった'}。</li>"
            )
        te = info.get("tie_effect")
        if te:
            parts.append(
                f"<li>R2 の同点の決め方 {e(idx)}: 年率が同じ案 {te['n_exact_ties_at_top']} 件、"
                f"選ばれた案は{'変わった' if te['changed'] else '変わらなかった'}。</li>"
            )
    parts.append("</ul>")
    parts.append(
        "<p class='note'>結論は上の表の数字から読むこと。選んだ案が検証期間で買い持ちと、同じ平均比率で借り続ける場合の両方を上回らなければ、"
        "FTD のタイミングに意味があったとは言えない。</p>"
    )
    return parts


def _contamination_r_block(sel: dict) -> str:
    """The third test-period exception: R was designed after full-period statistics (which
    include the test period) had been looked at."""
    cd = sel["test_period_contamination"]
    fig = cd["figures"]
    rows = []
    for idx, lv in fig["p_drawdown_60d"].items():
        rows.append(
            [
                f"{idx} {cd['period'][0][:4]}-{cd['period'][1][:4]}",
                "",
                pct(fig["dd_day_share"][idx]),
                pct(lv["NORMAL"]),
                pct(lv["SEVERE"]),
            ]
        )
    return (
        "<div class='note' style='border:2px solid #b00;background:#fff3f3'>"
        "<b>検証期間を見たうえで R を設計していた (三つ目の例外)</b>"
        f"<p>R を設計する前の {e(cd['date'])} に、docs/plan.md の「背景」の節を、"
        f"{cd['period'][0][:4]} 年から {cd['period'][1][:4]} 年までの全期間の統計から書いた。"
        "この全期間には、検証期間 (2015 年以降) が含まれる。使った統計は、DD になった日の割合、"
        "60 セッション以内に 10% 以上下げる割合の危険度別の違い、FTD の後 60 セッションの騰落である。</p>"
        + table(
            ["区分", "", "DD の日の割合", "NORMAL の後の下落割合", "SEVERE の後の下落割合"],
            rows,
        )
        + f"<p>FTD の後 60 セッションの騰落は、{e(fig['ftd_forward_60d'])}。</p>"
        f"<p>{e(cd['consequence'])}。そのため、R の検証期間の結果も、検証外データによるきれいな確認ではない。"
        "例外は三つある。同点の決め方 (結果を見たあとに定めた)、R2 の下見、そしてこの全期間の統計である。</p>"
        "</div>"
    )


def _forward_r_block(sel: dict) -> list[str]:
    """Forward record of R: baseline and picks over the sessions after the frozen test end."""
    fw = sel["forward_record"]
    parts = [
        f"<h2>R の今後の記録 ({e(fw['after'])} より後)</h2>",
        f"<p>検証期間の終わりは {e(sel['split']['test'][1])} に固定した。それより後のセッションは、"
        "基準と選んだ案について、ここまでと同じコードで別に記録する。データがない間は「記録開始前」と表示する。</p>",
    ]
    headers = {
        "A": ["index", "role", "FTD の検出数", "完結した取引", "中央値"],
        "B": ["index", "role", "セッション数", "累積の騰落", "最大下落"],
        "C": ["index", "role", "基準以上の日", "それ以外の日", "差"],
    }
    names = {"A": "A. FTD の売買", "B": "B. 投資比率", "C": "C. 大きな下落の割合"}
    for ev in "ABC":
        rows = []
        for idx, roles in fw[ev].items():
            started = fw["n_sessions"][idx] > 0
            for role, r in roles.items():
                if not started:
                    rows.append([idx, f"{role} ({r['variant_id']})", "記録開始前", "-", "-"])
                elif ev == "A":
                    rows.append(
                        [
                            idx,
                            f"{role} ({r['variant_id']})",
                            intv(r["n_events"]),
                            intv(r["n_trades"]),
                            pct(r["median"], 2),
                        ]
                    )
                elif ev == "B":
                    rows.append(
                        [
                            idx,
                            f"{role} ({r['variant_id']})",
                            intv(r["n_days"]),
                            pct(r["cum_return"], 2),
                            pct(r["max_dd"]),
                        ]
                    )
                else:
                    rows.append(
                        [
                            idx,
                            f"{role} ({r['variant_id']})",
                            intv(r["n_elevated"]),
                            intv(r["n_normal"]),
                            pct(r["spread"]),
                        ]
                    )
        parts.append(f"<h3>{e(names[ev])}</h3>")
        parts.append(table(headers[ev], rows))
    return parts


def render(result: dict, summary: dict, overlay: dict | None = None) -> str:
    """The report is Japanese text with English column names. The only thing that changes
    between two renderings of the same data is the generation time in the second paragraph."""
    cmp = outputs.compared(result)
    counts = variants.count_summary()
    last = result["last_date"]
    test_end = result["test_end"]
    parts = [
        f"<!doctype html><html lang='ja'><head><meta charset='utf-8'><title>研究レポート R</title>"
        f"<style>{CSS}</style></head><body>",
        "<h1>FTD / Distribution Day シグナルの洗練: 研究レポート (段階 R)</h1>",
        f"<p>生成: {datetime.now(UTC).strftime('%Y-%m-%d %H:%M UTC')} / データ末日: {e(last)} / "
        "手元専用 (公開しない)</p>",
        "<h2>1. 目的</h2>",
        "<p>claude-trading-skills の FTD / DD の定義を基準に置き、条件を変えた案と比べて、"
        "守りの判断に使えるシグナルに近づくかを調べる。良い条件が見つかれば、スキルの改良案として出す。"
        "IBD 公式の定義ではなく、スキルの定義が基準である。</p>",
        "<h2>2. 先に固定したルール</h2><ul>",
        f"<li>期間: 設計期間 (design) {DESIGN_START} 〜 {DESIGN_END}、検証期間 (test) {TEST_START} 〜 {e(test_end)}。コードに固定 (検証期間の終わりも固定した日付で、最新日ではない)。{e(test_end)} より後のセッションは、下の「今後の記録」にだけ入る。</li>",
        "<li>条件は design の成績だけで選ぶ。test は基準と選んだ案について一度だけ計算し、見てから条件を変えない。ただし例外が三つある (下の「検証期間を見ていた例外」)。</li>",
        "<li>指数ごとに評価し、合算しない。シグナルはその日より前 (当日を含む) のデータだけで決める。</li>",
        f"<li>前方の窓 ({HORIZON} セッション) が期間の末尾を超えるイベントは捨てる (design では test 期間に入り込むものを捨てる)。件数は各表の dropped 列。</li>",
        f"<li>選択基準 A: {e(selection.CRITERIA['A'])}</li>",
        f"<li>選択基準 B: {e(selection.CRITERIA['B'])}</li>",
        f"<li>選択基準 C: {e(selection.CRITERIA['C'])}</li>",
        "<li>該当する案がない指数は基準案を維持する (fallback_to_baseline)。</li></ul>",
        _amendments_block(summary),
        "<h3>検証期間を見ていた例外</h3>",
        _contamination_r_block(summary),
        "<h2>3. 試した案の数</h2>",
        f"<p>合計 <b>{counts['total_variants']}</b> 案 "
        f"(FTD {counts['ftd_variants']} 案 = 押し目 4 × 最低上昇率 3 × 出来高必須 2。"
        f"DD {counts['dd_variants']} 案 = 下落率 3 × 出来高 3 × 危険度の基準 5 × 危険度時の投資比率 2)。"
        f"DD のシグナル定義としては {counts['distinct_dd_signal_definitions']} 通り (評価 C はこの単位)。"
        "選んだ案の成績は多重比較で良く見えやすい点に注意。</p>",
        "<div class='note'>仕様の曖昧さへの対応: (1) 危険度の「スキルの基準」は 4 段階なので、HIGH 以上 (skill_high) と SEVERE のみ (skill_severe) の"
        "2 つのトリガーに分けた。基準案は skill_high、-0.2%、前日比出来高、投資比率 50%。"
        "(2) 過去 5 年の分布は直前 1260 セッション (その日を含まない) で、252 セッション未満の間はシグナルなし。"
        "(3) 出来高必須の FTD は検出後のフィルタであり、検出器を変えるのと同じではない (ラリーは続くため、検出器に組み込めば後日の別の日が FTD になりうる)。"
        "このフィルタ案は検出器に組み込んだ案の部分集合を返す。</div>",
    ]
    names = {
        "A": "A. FTD を売買として評価",
        "B": "B. DD の危険度を投資比率として評価",
        "C": "C. 60 セッション以内に 10% 以上下げる割合",
    }
    for n, ev in enumerate("ABC", start=4):
        parts.append(f"<h2>{n}. {names[ev]}</h2><p>{e(outputs.TITLES[ev])}</p>")
        for index, info in cmp[ev].items():
            parts.append(f"<h3>{e(index)}</h3>")
            if ev == "B":
                parts.append(
                    f"<p class='note'>{EM_NOTE}</p>" if index == next(iter(cmp[ev])) else ""
                )
            if info["fallback_to_baseline"]:
                parts.append("<p>design で条件を満たす案がなく、基準案を維持した。</p>")
            else:
                parts.append(
                    f"<p>選択: <code>{e(info['selected'])}</code> "
                    f"(候補 {info['n_considered']} 件中、条件を満たす {info['n_qualifying']} 件)</p>"
                )
            rows, cls, _ = _rows_for(ev, info)
            parts.append(table(HEAD[ev], rows, cls))
            parts.append("<p>design 上位 (選択基準の順):</p>")
            parts.append(_top_table(ev, result["design"][ev], index))
            if ev == "C":
                parts.append("<p>基準案の危険度 4 段階別:</p>")
                parts.append(_level_table(result, index))
    parts.extend(_forward_r_block(summary))
    parts.append("<h2>7. 注意</h2><ul>")
    parts.append(
        "<li>選んだ案が test で基準案を上回らなければ、design への当てはめにすぎなかった可能性が高い。</li>"
    )
    parts.append(
        "<li>古い年の出来高の質は未確認 (plan.md R1)。FMP のデータは手元の分析にだけ使う。</li></ul>"
    )
    if overlay is not None:
        parts.extend(render_overlay(overlay))
    parts.append("</body></html>")
    return "\n".join(parts)
