/* Shared helpers. Everything hangs off the WFS namespace so the app runs from file://
   with no bundler and no module/CORS restrictions. */
window.WFS = window.WFS || {};
(function (W) {
  'use strict';

  var MONTHS = ['January', 'February', 'March', 'April', 'May', 'June',
    'July', 'August', 'September', 'October', 'November', 'December'];
  var DOW = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat'];

  /* ---- dates: ISO 'YYYY-MM-DD' strings everywhere, parsed as local time ---- */
  function parse(iso) {
    var p = String(iso).slice(0, 10).split('-');
    return new Date(+p[0], +p[1] - 1, +p[2]);
  }
  function iso(d) {
    return d.getFullYear() + '-' + pad(d.getMonth() + 1) + '-' + pad(d.getDate());
  }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function addDays(d, n) { var x = new Date(d.getTime()); x.setDate(x.getDate() + n); return x; }
  function isWeekend(d) { var g = d.getDay(); return g === 0 || g === 6; }

  /* Public holidays, installed by the store from the server's list: {date -> name}. */
  var HOLIDAYS = {};
  function setHolidays(list) {
    HOLIDAYS = {};
    (list || []).forEach(function (h) { HOLIDAYS[h.date] = h.name; });
  }
  function holidayName(isoStr) { return HOLIDAYS[String(isoStr).slice(0, 10)] || null; }
  function isWorkingDay(d) { return !isWeekend(d) && !HOLIDAYS[iso(d)]; }
  function monthKey(isoStr) { return String(isoStr).slice(0, 7); }
  function monthLabel(key) {
    var p = key.split('-');
    return MONTHS[+p[1] - 1].slice(0, 3) + " '" + String(p[0]).slice(2);
  }
  function fmt(isoStr) {
    var d = parse(isoStr);
    return d.getDate() + ' ' + MONTHS[d.getMonth()].slice(0, 3) + ' ' + d.getFullYear();
  }
  function fmtRange(from, to) {
    if (!to || from === to) return fmt(from);
    var a = parse(from), b = parse(to);
    if (a.getMonth() === b.getMonth() && a.getFullYear() === b.getFullYear()) {
      return a.getDate() + '–' + fmt(to);
    }
    return fmt(from) + ' – ' + fmt(to);
  }
  function todayISO() { return iso(new Date()); }

  /* Working days (Mon-Fri, not public holidays) between two ISO dates, inclusive. */
  function workingDaysBetween(fromISO, toISO) {
    var out = [], d = parse(fromISO), end = parse(toISO), guard = 0;
    while (d <= end && guard++ < 800) {
      if (isWorkingDay(d)) out.push(iso(d));
      d = addDays(d, 1);
    }
    return out;
  }
  /* Count of working days in a calendar month key 'YYYY-MM'. */
  function workingDaysInMonth(key) {
    var p = key.split('-'), y = +p[0], m = +p[1] - 1;
    var d = new Date(y, m, 1), n = 0;
    while (d.getMonth() === m) { if (isWorkingDay(d)) n++; d = addDays(d, 1); }
    return n;
  }

  /* ---- numbers ---- */
  function n1(v) { return Math.round(v * 10) / 10; }
  function pct(a, b) { return b ? Math.round((a / b) * 1000) / 10 : 0; }

  /* ---- people ---- */
  function initials(name) {
    var p = String(name).trim().split(/\s+/).filter(Boolean);
    var a = p[0] ? p[0][0] : '?';
    var b = p.length > 1 ? p[p.length - 1][0] : '';
    return (a + b).toUpperCase();
  }
  var AV = ['#4f46e5', '#0891b2', '#ea580c', '#be123c', '#65a30d', '#7c3aed',
    '#0d9488', '#c2410c', '#2563eb', '#db2777', '#059669', '#9333ea'];
  function colorFor(key) {
    var h = 0, s = String(key);
    for (var i = 0; i < s.length; i++) h = (h * 31 + s.charCodeAt(i)) >>> 0;
    return AV[h % AV.length];
  }

  /* ---- dom ---- */
  function el(tag, attrs, kids) {
    var e = document.createElement(tag);
    if (attrs) Object.keys(attrs).forEach(function (k) {
      var v = attrs[k];
      if (v === null || v === undefined || v === false) return;
      if (k === 'class') e.className = v;
      else if (k === 'html') e.innerHTML = v;
      else if (k === 'text') e.textContent = v;
      else if (k.slice(0, 2) === 'on') e.addEventListener(k.slice(2), v);
      else if (k === 'style') e.setAttribute('style', v);
      else if (k === 'data') Object.keys(v).forEach(function (d) { e.dataset[d] = v[d]; });
      else e.setAttribute(k, v);
    });
    (kids || []).forEach(function (k) {
      if (k === null || k === undefined || k === false) return;
      e.appendChild(typeof k === 'string' ? document.createTextNode(k) : k);
    });
    return e;
  }
  function esc(s) {
    return String(s === null || s === undefined ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }
  function clear(node) { while (node.firstChild) node.removeChild(node.firstChild); return node; }

  function avatar(name) {
    return el('div', {
      class: 'avatar', title: name,
      style: 'background:' + colorFor(name)
    }, [initials(name)]);
  }

  /* A short message. With opts.action (e.g. 'Undo') and opts.onAction it carries a
     button and stays up longer. A new toast replaces the one on screen. */
  var shown = null;
  function toast(msg, opts) {
    opts = opts || {};
    if (shown) shown.remove();
    var t = el('div', { class: 'toast', role: 'status' }, [el('span', { text: msg })]);
    if (opts.action) {
      t.appendChild(el('button', {
        type: 'button', class: 'toast-action', text: opts.action,
        onclick: function () { t.remove(); opts.onAction(); }
      }));
    }
    document.body.appendChild(t);
    shown = t;
    setTimeout(function () { t.remove(); if (shown === t) shown = null; }, opts.ms || (opts.action ? 7000 : 2600));
  }

  /* ---- grouping ---- */
  function groupBy(arr, fn) {
    var m = {};
    arr.forEach(function (x) { var k = fn(x); (m[k] = m[k] || []).push(x); });
    return m;
  }
  function sum(arr, fn) {
    return arr.reduce(function (a, x) { return a + (fn ? fn(x) : x); }, 0);
  }

  W.util = {
    MONTHS: MONTHS, DOW: DOW,
    parse: parse, iso: iso, pad: pad, addDays: addDays, isWeekend: isWeekend,
    monthKey: monthKey, monthLabel: monthLabel, fmt: fmt, fmtRange: fmtRange, todayISO: todayISO,
    workingDaysBetween: workingDaysBetween, workingDaysInMonth: workingDaysInMonth,
    setHolidays: setHolidays, holidayName: holidayName, isWorkingDay: isWorkingDay,
    n1: n1, pct: pct, initials: initials, colorFor: colorFor,
    el: el, esc: esc, clear: clear, avatar: avatar, toast: toast,
    groupBy: groupBy, sum: sum
  };
})(window.WFS);
