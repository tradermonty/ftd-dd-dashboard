// Price chart per index: risk-level background bands, DD markers, FTD stars, active-DD bars.
// Data: window.CHART_DATA (assets/chart_data.js), text: window.CHART_I18N (inline in the page).
(function () {
  var D = window.CHART_DATA, I = window.CHART_I18N;
  var host = document.getElementById('charts');
  if (!D || !I || !host) return;
  if (typeof Plotly === 'undefined') {
    host.innerHTML = '<p class="callout">Plotly could not be loaded (offline?).</p>';
    return;
  }
  var COLORS = {CAUTION: 'rgba(250,204,21,0.22)', HIGH: 'rgba(249,115,22,0.22)', SEVERE: 'rgba(220,38,38,0.22)'};
  var PRICE_DOMAIN = [0.3, 1];

  function fitY(el, d) {
    var r = el.layout.xaxis.range.map(function (v) { return String(v).slice(0, 10); });
    var lo = Infinity, hi = -Infinity;
    for (var i = 0; i < d.date.length; i++) {
      var t = d.date[i];
      if (t >= r[0] && t <= r[1]) { lo = Math.min(lo, d.close[i]); hi = Math.max(hi, d.close[i]); }
    }
    if (lo < hi) {
      var pad = (hi - lo) * 0.05;
      Plotly.relayout(el, {'yaxis.range': [lo - pad, hi + pad]});
    }
  }

  Object.keys(D).forEach(function (sym) {
    var d = D[sym];
    var h = document.createElement('h2');
    h.textContent = d.name;
    host.appendChild(h);
    var el = document.createElement('div');
    el.className = 'chart';
    el.id = 'chart-' + sym.replace('^', '');
    host.appendChild(el);

    var end = d.date[d.date.length - 1];
    var start = new Date(end);
    start.setFullYear(start.getFullYear() - 1);
    var shapes = d.bands.map(function (b) {
      return {type: 'rect', xref: 'x', yref: 'paper', x0: b.x0, x1: b.x1,
              y0: PRICE_DOMAIN[0], y1: PRICE_DOMAIN[1], fillcolor: COLORS[b.level],
              line: {width: 0}, layer: 'below'};
    });
    // Research R2: levered periods of the picked variant, a thin blue strip at the top of the
    // price panel (distinct from the risk-level backgrounds); toggled by #lev-toggle.
    var toggle = document.getElementById('lev-toggle');
    var levShapes = (d.lev ? d.lev.bands : []).map(function (b) {
      return {type: 'rect', xref: 'x', yref: 'paper', x0: b.x0, x1: b.x1,
              y0: PRICE_DOMAIN[1] - 0.035, y1: PRICE_DOMAIN[1], fillcolor: 'rgba(37,99,235,0.55)',
              line: {width: 0}, layer: 'below', name: 'lev'};
    });
    var riskShapes = shapes;
    if (levShapes.length && (!toggle || toggle.checked)) shapes = riskShapes.concat(levShapes);
    var custom = d.date.map(function (_, i) { return [d.d5[i], d.d15[i], d.d25[i], d.risk[i]]; });
    var ftdCustom = d.ftd_date.map(function (_, i) { return [d.ftd_detected[i], d.ftd_gain[i]]; });
    var traces = [
      {x: d.date, y: d.close, name: I.close, type: 'scatter', mode: 'lines',
       line: {color: '#2f6fb2', width: 1.4}, customdata: custom,
       hovertemplate: '%{y:,.2f}  ' + I.hover_counts +
                      ': %{customdata[0]}/%{customdata[1]}/%{customdata[2]}  ' +
                      I.risk + ': %{customdata[3]}<extra>' + I.close + '</extra>'},
      {x: d.dd_date, y: d.dd_close, name: I.dd, mode: 'markers',
       marker: {color: '#c2445a', size: 6, symbol: 'triangle-down'}},
      {x: d.ftd_date, y: d.ftd_close, name: I.ftd, mode: 'markers', customdata: ftdCustom,
       marker: {color: '#1f8a7a', size: 11, symbol: 'star'},
       hovertemplate: 'FTD %{x}  ' + I.detected + ' %{customdata[0]}  ' + I.gain +
                      ' %{customdata[1]}%<extra></extra>'},
      {x: d.date, y: d.d25, name: I.active_dd, type: 'bar', yaxis: 'y2',
       marker: {color: 'rgba(194,68,90,.35)'}}
    ];
    var layout = {
      margin: {l: 56, r: 12, t: 64, b: 30}, shapes: shapes,
      xaxis: {range: [start.toISOString().slice(0, 10), end],
        rangeselector: {buttons: [
          {count: 6, label: I.range_6m, step: 'month', stepmode: 'backward'},
          {count: 1, label: I.range_1y, step: 'year', stepmode: 'backward'},
          {count: 5, label: I.range_5y, step: 'year', stepmode: 'backward'},
          {step: 'all', label: I.range_all}]}},
      yaxis: {domain: PRICE_DOMAIN, title: I.y_close},
      yaxis2: {domain: [0, 0.22], title: I.y_dd},
      legend: {orientation: 'h', y: 1.16, x: 0}, hovermode: 'x unified',
      paper_bgcolor: '#fff', plot_bgcolor: '#fff'
    };
    Plotly.newPlot(el, traces, layout, {responsive: true}).then(function () {
      fitY(el, d);
      el.setAttribute('data-ready', '1');
    });
    el.on('plotly_relayout', function (ev) { if (!('yaxis.range' in ev)) fitY(el, d); });
    if (toggle && levShapes.length) {
      toggle.addEventListener('change', function () {
        Plotly.relayout(el, {shapes: toggle.checked ? riskShapes.concat(levShapes) : riskShapes});
      });
    }
  });
})();
