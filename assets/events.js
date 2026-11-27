// Sort (click a header) and filter (index / year) for the event tables. Vanilla JS.
(function () {
  var fIndex = document.getElementById('f-index');
  var fYear = document.getElementById('f-year');
  var tables = [
    {el: document.getElementById('tbl-ftd'), count: document.getElementById('count-ftd')},
    {el: document.getElementById('tbl-dd'), count: document.getElementById('count-dd')}
  ];

  function applyFilter() {
    tables.forEach(function (t) {
      if (!t.el) return;
      var shown = 0, rows = t.el.tBodies[0].rows;
      for (var i = 0; i < rows.length; i++) {
        var r = rows[i];
        var ok = (!fIndex.value || r.dataset.index === fIndex.value) &&
                 (!fYear.value || r.dataset.year === fYear.value);
        r.style.display = ok ? '' : 'none';
        if (ok) shown++;
      }
      if (t.count) t.count.textContent = '(' + shown + ' / ' + rows.length + ')';
    });
  }

  function cellValue(row, col, type) {
    var cell = row.cells[col];
    var v = cell.dataset.v !== undefined ? cell.dataset.v : cell.textContent;
    if (type === 'num') {
      var n = parseFloat(v);
      return isNaN(n) ? -Infinity : n;
    }
    return v;
  }

  function makeSortable(table) {
    if (!table) return;
    var heads = table.tHead.rows[0].cells;
    Array.prototype.forEach.call(heads, function (th, col) {
      th.addEventListener('click', function () {
        var asc = !th.classList.contains('asc');
        Array.prototype.forEach.call(heads, function (h) { h.classList.remove('asc', 'desc'); });
        th.classList.add(asc ? 'asc' : 'desc');
        var type = th.dataset.type || 'text';
        var body = table.tBodies[0];
        var rows = Array.prototype.slice.call(body.rows);
        rows.sort(function (a, b) {
          var x = cellValue(a, col, type), y = cellValue(b, col, type);
          if (x < y) return asc ? -1 : 1;
          if (x > y) return asc ? 1 : -1;
          return 0;
        });
        rows.forEach(function (r) { body.appendChild(r); });
      });
    });
  }

  tables.forEach(function (t) { makeSortable(t.el); });
  if (fIndex) fIndex.addEventListener('change', applyFilter);
  if (fYear) fYear.addEventListener('change', applyFilter);
  applyFilter();
})();
