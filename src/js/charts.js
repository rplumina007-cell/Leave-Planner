/* Tiny dependency-free chart set. Everything is plain DOM or inline SVG so it
   inherits the theme tokens and stays readable in light and dark. */
(function (W) {
  'use strict';
  var U = W.util, el = U.el;
  var NS = 'http://www.w3.org/2000/svg';

  function s(tag, attrs, kids) {
    var e = document.createElementNS(NS, tag);
    Object.keys(attrs || {}).forEach(function (k) {
      if (attrs[k] === null || attrs[k] === undefined) return;
      e.setAttribute(k, attrs[k]);
    });
    (kids || []).forEach(function (k) {
      e.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
    });
    return e;
  }

  /* Horizontal stacked bars: rows = [{label, parts:[{value,color,label}], total}] */
  function stackedBars(rows, opts) {
    opts = opts || {};
    var max = opts.max || Math.max.apply(null, rows.map(function (r) { return r.total; }).concat([1]));
    var wrap = el('div', {});
    if (!rows.length) return el('div', { class: 'empty', text: 'No data in this window' });
    rows.forEach(function (r) {
      var bar = el('div', { class: 'bar' });
      r.parts.forEach(function (p) {
        if (!p.value) return;
        bar.appendChild(el('i', {
          style: 'width:' + (p.value / max * 100) + '%;background:' + p.color,
          title: r.label + ' — ' + p.label + ': ' + U.n1(p.value) + (opts.unit || ' d')
        }));
      });
      wrap.appendChild(el('div', { class: 'bar-row' }, [
        el('span', { class: 'nm', title: r.label, text: r.label }),
        bar,
        el('span', { class: 'num', style: 'text-align:right;font-weight:650', text: U.n1(r.total) + (opts.unit || '') })
      ]));
    });
    if (opts.legend) wrap.appendChild(legend(opts.legend));
    return wrap;
  }

  function legend(items) {
    return el('div', { class: 'lg' }, items.map(function (i) {
      return el('span', {}, [el('b', { style: 'background:' + i.color }), i.label]);
    }));
  }

  /* Grouped/stacked column chart over months.
     series = [{key, label, color}], data = [{label, values:{key:number}}] */
  function columns(data, series, opts) {
    opts = opts || {};
    var W_ = 720, H = opts.height || 190, padL = 34, padB = 26, padT = 10;
    var totals = data.map(function (d) {
      return series.reduce(function (a, sr) { return a + (d.values[sr.key] || 0); }, 0);
    });
    var max = Math.max.apply(null, totals.concat([1]));
    var step = niceStep(max);
    max = Math.ceil(max / step) * step;
    var plotH = H - padB - padT, plotW = W_ - padL - 6;
    var bw = Math.min(46, (plotW / Math.max(data.length, 1)) * 0.62);

    var g = [];
    for (var v = 0; v <= max; v += step) {
      var y = padT + plotH - (v / max) * plotH;
      g.push(s('line', { x1: padL, x2: W_ - 6, y1: y, y2: y, stroke: 'var(--line)', 'stroke-width': 1 }));
      g.push(s('text', {
        x: padL - 7, y: y + 3.5, 'text-anchor': 'end', fill: 'var(--faint)',
        'font-size': 10, 'font-weight': 700
      }, [String(v)]));
    }
    data.forEach(function (d, i) {
      var cx = padL + (plotW / data.length) * (i + 0.5);
      var yCursor = padT + plotH;
      series.forEach(function (sr) {
        var val = d.values[sr.key] || 0;
        if (!val) return;
        var h = (val / max) * plotH;
        yCursor -= h;
        g.push(s('rect', {
          x: cx - bw / 2, y: yCursor, width: bw, height: Math.max(h, 1),
          fill: sr.color, rx: 4
        }, [s('title', {}, [d.label + ' — ' + sr.label + ': ' + U.n1(val) + (opts.unit || ' d')])]));
      });
      if (opts.showTotals && totals[i]) {
        g.push(s('text', {
          x: cx, y: yCursor - 5, 'text-anchor': 'middle', fill: 'var(--muted)',
          'font-size': 10.5, 'font-weight': 800
        }, [String(U.n1(totals[i]))]));
      }
      g.push(s('text', {
        x: cx, y: H - 8, 'text-anchor': 'middle', fill: 'var(--faint)',
        'font-size': 10, 'font-weight': 700
      }, [d.label]));
    });

    var svg = s('svg', { class: 'spark', viewBox: '0 0 ' + W_ + ' ' + H, role: 'img',
      "font-family": "'SF Pro Text', -apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', sans-serif" }, g);
    var wrap = el('div', {}, [svg]);
    if (opts.legend !== false) wrap.appendChild(legend(series));
    return wrap;
  }

  /* Line chart for rates over time. lines = [{key,label,color}] */
  function lines(data, series, opts) {
    opts = opts || {};
    var W_ = 720, H = opts.height || 170, padL = 36, padB = 24, padT = 12;
    var max = 1;
    data.forEach(function (d) {
      series.forEach(function (sr) { max = Math.max(max, d.values[sr.key] || 0); });
    });
    var step = niceStep(max); max = Math.ceil(max / step) * step;
    var plotH = H - padB - padT, plotW = W_ - padL - 10;
    var g = [];
    for (var v = 0; v <= max; v += step) {
      var y = padT + plotH - (v / max) * plotH;
      g.push(s('line', { x1: padL, x2: W_ - 10, y1: y, y2: y, stroke: 'var(--line)' }));
      g.push(s('text', { x: padL - 7, y: y + 3.5, 'text-anchor': 'end', fill: 'var(--faint)', 'font-size': 10, 'font-weight': 700 },
        [v + (opts.suffix || '')]));
    }
    function px(i) { return padL + (data.length === 1 ? plotW / 2 : (plotW / (data.length - 1)) * i); }
    function py(v) { return padT + plotH - (v / max) * plotH; }
    series.forEach(function (sr) {
      var pts = data.map(function (d, i) { return px(i) + ',' + py(d.values[sr.key] || 0); }).join(' ');
      g.push(s('polyline', {
        points: pts, fill: 'none', stroke: sr.color, 'stroke-width': 2.5,
        'stroke-linejoin': 'round', 'stroke-linecap': 'round'
      }));
      data.forEach(function (d, i) {
        g.push(s('circle', { cx: px(i), cy: py(d.values[sr.key] || 0), r: 3.5, fill: sr.color },
          [s('title', {}, [d.label + ' — ' + sr.label + ': ' + U.n1(d.values[sr.key] || 0) + (opts.suffix || '')])]));
      });
    });
    data.forEach(function (d, i) {
      g.push(s('text', { x: px(i), y: H - 6, 'text-anchor': 'middle', fill: 'var(--faint)', 'font-size': 10, 'font-weight': 700 }, [d.label]));
    });
    var wrap = el('div', {}, [s('svg', { class: 'spark', viewBox: '0 0 ' + W_ + ' ' + H, role: 'img',
      "font-family": "'SF Pro Text', -apple-system, BlinkMacSystemFont, system-ui, 'Segoe UI', sans-serif" }, g)]);
    if (opts.legend !== false) wrap.appendChild(legend(series));
    return wrap;
  }

  /* Proportion bar + ranked list, used for reason mixes. */
  function mixBar(items, palette, unit) {
    var total = U.sum(items, function (i) { return i.days; }) || 1;
    var bar = el('div', { class: 'bar', style: 'height:22px' });
    items.forEach(function (i, idx) {
      bar.appendChild(el('i', {
        style: 'width:' + (i.days / total * 100) + '%;background:' + palette[idx % palette.length],
        title: i.label + ': ' + U.n1(i.days) + (unit || ' d')
      }));
    });
    var list = el('div', { style: 'margin-top:12px;display:grid;gap:6px' },
      items.map(function (i, idx) {
        return el('div', { style: 'display:grid;grid-template-columns:11px 1fr auto auto;gap:9px;align-items:center;font-size:12.5px' }, [
          el('b', { style: 'width:10px;height:10px;border-radius:3px;background:' + palette[idx % palette.length] }),
          el('span', { text: i.label }),
          el('span', { class: 'num', style: 'color:var(--muted)', text: i.count + '×' }),
          el('span', { class: 'num', style: 'font-weight:650;min-width:52px;text-align:right', text: U.n1(i.days) + (unit || ' d') })
        ]);
      }));
    return el('div', {}, [bar, list]);
  }

  /* Weekday intensity strip. rows = [{label,a,b,total}] Mon..Fri */
  function weekdayStrip(rows, colorA, colorB, labelA, labelB) {
    var max = Math.max.apply(null, rows.map(function (r) { return r.total; }).concat([1]));
    var grid = el('div', { style: 'display:grid;grid-template-columns:repeat(5,1fr);gap:8px' },
      rows.map(function (r) {
        var h = 74;
        var col = el('div', { style: 'display:flex;flex-direction:column;justify-content:flex-end;height:' + h + 'px;gap:2px' });
        if (r.b) col.appendChild(el('div', {
          style: 'height:' + (r.b / max * h) + 'px;background:' + colorB + ';border-radius:5px 5px 0 0',
          title: labelB + ': ' + U.n1(r.b) + ' d'
        }));
        if (r.a) col.appendChild(el('div', {
          style: 'height:' + (r.a / max * h) + 'px;background:' + colorA + (r.b ? ';border-radius:0 0 5px 5px' : ';border-radius:5px'),
          title: labelA + ': ' + U.n1(r.a) + ' d'
        }));
        return el('div', { style: 'display:flex;flex-direction:column;gap:5px;align-items:center' }, [
          el('span', { class: 'num', style: 'font-size:12px;font-weight:800', text: U.n1(r.total) }),
          col,
          el('span', { style: 'font-size:11px;color:var(--faint);font-weight:800;letter-spacing:.06em', text: r.label })
        ]);
      }));
    var wrap = el('div', {}, [grid]);
    if (labelA) wrap.appendChild(legend([{ label: labelA, color: colorA }, { label: labelB, color: colorB }]));
    return wrap;
  }

  /* Simple meter used for rate KPIs. */
  function meter(value, max, color) {
    return el('div', { class: 'bar', style: 'height:6px;margin-top:8px' }, [
      el('i', { style: 'width:' + Math.min(100, (value / (max || 1)) * 100) + '%;background:' + color })
    ]);
  }

  function niceStep(max) {
    var raw = max / 4;
    var mag = Math.pow(10, Math.floor(Math.log(raw) / Math.LN10));
    var n = raw / mag;
    var mult = n <= 1 ? 1 : n <= 2 ? 2 : n <= 5 ? 5 : 10;
    return Math.max(mult * mag, 1);
  }

  W.charts = {
    svg: s, stackedBars: stackedBars, columns: columns, lines: lines,
    mixBar: mixBar, weekdayStrip: weekdayStrip, meter: meter, legend: legend
  };
})(window.WFS);
