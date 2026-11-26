#!/usr/bin/env python3
"""Build a local preview page (build/preview.html) from build/daily_status.csv.

Price line + DD markers + FTD stars + active-DD bars per index, with the background
shaded by the ibd-distribution-day-monitor risk level (consecutive days merged into one band).
Not the P5 site; depends only on pandas.

Usage:
    python scripts/build_preview.py [--src build/daily_status.csv] [--out build/preview.html]
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from webgen.chartdata import (  # noqa: E402,F401
    NAMES,
    PLOTLY_SRC,
    PLOTLY_SRI,
    SHADED,
    risk_bands,
)
from webgen.chartdata import payload as _payload  # noqa: E402

HTML = """<!doctype html><html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>FTD・DD プレビュー</title>
<script src="__PLOTLY_SRC__" integrity="__PLOTLY_SRI__" crossorigin="anonymous"></script>
<style>
:root{--bg:#f6f7f9;--card:#fff;--ink:#1c2733;--muted:#5d6b78;--line:#dfe6ec}
body{margin:0;background:var(--bg);color:var(--ink);font:14px/1.5 -apple-system,"Hiragino Sans",sans-serif}
main{max-width:1200px;margin:0 auto;padding:20px 16px}
h1{font-size:20px;margin:0 0 4px}.note{color:var(--muted);font-size:12px;margin:0 0 10px}
.legend{display:flex;flex-wrap:wrap;gap:6px 16px;align-items:center;margin:0 0 6px;font-size:13px}
.chip{display:inline-block;width:22px;height:12px;border:1px solid var(--line);border-radius:3px;vertical-align:-1px;margin-right:5px}
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(260px,1fr));gap:12px;margin:12px 0 16px}
.k{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:12px 14px}
.k b{font-size:15px}.k div{color:var(--muted);font-size:13px}
.chart{background:var(--card);border:1px solid var(--line);border-radius:10px;margin-bottom:16px;height:560px}
</style></head><body><main>
<h1>FTD・ディストリビューションデー プレビュー</h1>
<p class="note">手元専用の試作版です。FTD の印は「後で FTD とされた日」の位置で、初めて見つかった日とは違うことがあります。DD は判定日に印を付けています。</p>
<div class="legend" id="legend"></div>
<p class="note">背景の色は、ibd-distribution-day-monitor スキルのしきい値による危険度です。
CAUTION は直近25日の有効DDが3以上、HIGH は25日で5以上・15日で3以上・5日で2以上のどれか、
SEVERE は25日で6以上、15日で4以上、または終値が21EMAと50SMAの両方を下回り25日で5以上です。NORMAL は塗りません。</p>
<div class="kpis" id="kpis"></div><div id="charts"></div>
<script>
const D = __DATA__;
const COLORS = {CAUTION: 'rgba(250,204,21,0.22)', HIGH: 'rgba(249,115,22,0.22)', SEVERE: 'rgba(220,38,38,0.22)'};
const CHIPS = {CAUTION: 'rgba(250,204,21,0.55)', HIGH: 'rgba(249,115,22,0.55)', SEVERE: 'rgba(220,38,38,0.55)'};
const DESC = {CAUTION: 'CAUTION（25日で3以上）', HIGH: 'HIGH（25日5 / 15日3 / 5日2）', SEVERE: 'SEVERE（25日6 / 15日4 / MA割れ+5）'};
const lg = document.getElementById('legend');
lg.innerHTML = '<span><span class="chip" style="background:#fff"></span>NORMAL（塗りなし）</span>'
  + Object.keys(COLORS).map(k => `<span><span class="chip" style="background:${CHIPS[k]}"></span>${DESC[k]}</span>`).join('');
const PRICE_DOMAIN = [0.3, 1];
const kp = document.getElementById('kpis'), ch = document.getElementById('charts');
for (const [sym, d] of Object.entries(D)) {
  const k = document.createElement('div'); k.className = 'k';
  k.innerHTML = `<b>${d.name}</b><div>${d.last.date} 終値 ${d.last.close.toLocaleString()}</div>`
    + `<div>危険度 <b>${d.last.risk}</b> / 状態 ${d.last.state} / ラリー1日目 ${d.last.day1}</div>`
    + `<div>有効DD 5/15/25日 ${d.last.d5}/${d.last.d15}/${d.last.d25} / 期間中のFTD ${d.n_ftd}件・DD ${d.n_dd}日</div>`;
  kp.appendChild(k);
  const h = document.createElement('h2'); h.textContent = d.name; h.style.cssText = 'font-size:16px;margin:8px 0 6px'; ch.appendChild(h);
  const el = document.createElement('div'); el.className = 'chart'; ch.appendChild(el);
  const end = d.date[d.date.length - 1], start = new Date(end); start.setFullYear(start.getFullYear() - 2);
  const shapes = d.bands.map(b => ({
    type: 'rect', xref: 'x', yref: 'paper', x0: b.x0, x1: b.x1, y0: PRICE_DOMAIN[0], y1: PRICE_DOMAIN[1],
    fillcolor: COLORS[b.level], line: {width: 0}, layer: 'below'
  }));
  const custom = d.date.map((_, i) => [d.d5[i], d.d15[i], d.d25[i], d.risk[i]]);
  Plotly.newPlot(el, [
    {x: d.date, y: d.close, name: '終値', type: 'scatter', mode: 'lines', line: {color: '#2f6fb2', width: 1.4},
     customdata: custom,
     hovertemplate: '%{y:,.2f}　DD 5/15/25日: %{customdata[0]}/%{customdata[1]}/%{customdata[2]}　危険度: %{customdata[3]}<extra>終値</extra>'},
    {x: d.dd_date, y: d.dd_close, name: 'ディストリビューションデー', mode: 'markers', marker: {color: '#c2445a', size: 6, symbol: 'triangle-down'}},
    {x: d.ftd_date, y: d.ftd_close, name: 'FTD', mode: 'markers', marker: {color: '#1f8a7a', size: 10, symbol: 'star'}},
    {x: d.date, y: d.d25, name: '有効DD（25日）', type: 'bar', yaxis: 'y2', marker: {color: 'rgba(194,68,90,.35)'}}
  ], {
    margin: {l: 60, r: 20, t: 70, b: 30}, shapes: shapes,
    xaxis: {range: [start.toISOString().slice(0, 10), end],
      rangeselector: {buttons: [{count: 6, label: '6M', step: 'month', stepmode: 'backward'},
        {count: 1, label: '1Y', step: 'year', stepmode: 'backward'},
        {count: 5, label: '5Y', step: 'year', stepmode: 'backward'}, {step: 'all', label: '全期間'}]}},
    yaxis: {domain: PRICE_DOMAIN, title: '終値'}, yaxis2: {domain: [0, 0.22], title: '有効DD'},
    legend: {orientation: 'h', y: 1.14, x: 0}, hovermode: 'x unified', paper_bgcolor: '#fff', plot_bgcolor: '#fff'
  }, {responsive: true}).then(() => fitY(el, d));
  el.on('plotly_relayout', ev => { if (!('yaxis.range' in ev)) fitY(el, d); });
}
function fitY(el, d) {
  const r = el.layout.xaxis.range.map(v => String(v).slice(0, 10));
  let lo = Infinity, hi = -Infinity;
  d.date.forEach((t, i) => { if (t >= r[0] && t <= r[1]) { lo = Math.min(lo, d.close[i]); hi = Math.max(hi, d.close[i]); } });
  if (lo < hi) { const pad = (hi - lo) * 0.05; Plotly.relayout(el, {'yaxis.range': [lo - pad, hi + pad]}); }
}
</script></main></body></html>"""


def build(src: Path, out: Path, events_csv: Path | None = None) -> dict:
    df = pd.read_csv(src, parse_dates=["date"])
    events_csv = events_csv if events_csv is not None else src.parent / "ftd_events.csv"
    events = pd.read_csv(events_csv) if events_csv.exists() else None
    payload = _payload(df, events)
    html = (
        HTML.replace("__PLOTLY_SRC__", PLOTLY_SRC)
        .replace("__PLOTLY_SRI__", PLOTLY_SRI)
        .replace("__DATA__", json.dumps(payload))
    )
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    return payload


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--src", type=Path, default=ROOT / "build" / "daily_status.csv")
    ap.add_argument("--out", type=Path, default=ROOT / "build" / "preview.html")
    args = ap.parse_args(argv)
    payload = build(args.src, args.out)
    print(
        args.out,
        {
            k: (v["n_ftd"], v["n_dd"], len(v["bands"]), v["last"]["risk"])
            for k, v in payload.items()
        },
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
