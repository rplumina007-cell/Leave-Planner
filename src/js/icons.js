/* Line icons, drawn as inline SVG so they look the same on every device and take
   the surrounding text colour. One style: 24px grid, round 1.9px strokes.
   Usage: W.icon('home') -> <svg class="ico">. Sized by font-size (1em). */
(function (W) {
  'use strict';
  var NS = 'http://www.w3.org/2000/svg';

  var CAL = [['rect', { x: 3, y: 5, width: 18, height: 16, rx: 2 }], ['path', { d: 'M3 10h18M8 3v4M16 3v4' }]];
  var ICONS = {
    leave: [['circle', { cx: 12, cy: 12, r: 4 }], ['path', { d: 'M12 2.5v2M12 19.5v2M2.5 12h2M19.5 12h2M5.3 5.3l1.4 1.4M17.3 17.3l1.4 1.4M5.3 18.7l1.4-1.4M17.3 6.7l1.4-1.4' }]],
    home: [['path', { d: 'M3.5 11L12 4l8.5 7M5.5 9.5V20h13V9.5M10 20v-5.5h4V20' }]],
    planned: CAL.concat([['path', { d: 'M9 15.2l2.1 2.1 4-4' }]]),
    unplanned: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7.5v5.5M12 16.4v.1' }]],
    calendar: CAL,
    holiday: [['path', { d: 'M5.5 21V3.5M5.5 4h11.5l-2.3 4 2.3 4H5.5' }]],
    key: [['circle', { cx: 8, cy: 15, r: 4 }], ['path', { d: 'M10.9 12.1L20 3M16 7l3 3M13.5 9.5l2 2' }]],
    settings: [['circle', { cx: 12, cy: 12, r: 3 }], ['path', { d: 'M12 2.5v3M12 18.5v3M2.5 12h3M18.5 12h3M5.3 5.3l2.1 2.1M16.6 16.6l2.1 2.1M5.3 18.7l2.1-2.1M16.6 7.4l2.1-2.1' }]],
    logout: [['path', { d: 'M10 4H5.5v16H10M14.5 8l4 4-4 4M18.5 12H9' }]],
    check: [['path', { d: 'M5 12.5l4.5 4.5L19 7' }]],
    x: [['path', { d: 'M6 6l12 12M18 6L6 18' }]],
    users: [['circle', { cx: 9, cy: 8, r: 3.2 }], ['path', { d: 'M3 20c0-3.3 2.7-6 6-6s6 2.7 6 6M15.5 4.8a3 3 0 0 1 0 6.2M17.8 14.3c1.9.8 3.2 2.8 3.2 5.7' }]],
    user: [['circle', { cx: 12, cy: 8, r: 4 }], ['path', { d: 'M4 21c0-4 3.6-7 8-7s8 3 8 7' }]],
    alert: [['path', { d: 'M12 3.5l9.5 16.5h-19zM12 10v4.5M12 17.2v.1' }]],
    chevL: [['path', { d: 'M15 5l-7 7 7 7' }]],
    chevR: [['path', { d: 'M9 5l7 7-7 7' }]],
    chevD: [['path', { d: 'M6 9l6 6 6-6' }]],
    plus: [['path', { d: 'M12 5v14M5 12h14' }]],
    undo: [['path', { d: 'M9 14L4 9l5-5M4 9h10.5a5.5 5.5 0 0 1 0 11H11' }]],
    download: [['path', { d: 'M12 4v11M7 10.5l5 5 5-5M5 20h14' }]],
    refresh: [['path', { d: 'M20 12a8 8 0 1 1-2.4-5.7M20 4.5V9.5h-5' }]],
    scale: [['path', { d: 'M12 4v16M7 20h10M4.5 8h15M4.5 8L2 14a2.5 2.5 0 0 0 5 0zM19.5 8L17 14a2.5 2.5 0 0 0 5 0z' }]],
    clock: [['circle', { cx: 12, cy: 12, r: 9 }], ['path', { d: 'M12 7v5l3.2 2' }]],
    list: [['path', { d: 'M9 6h11M9 12h11M9 18h11M4.5 6h.1M4.5 12h.1M4.5 18h.1' }]],
    team: [['circle', { cx: 12, cy: 7.5, r: 3 }], ['circle', { cx: 5, cy: 10, r: 2.2 }], ['circle', { cx: 19, cy: 10, r: 2.2 }], ['path', { d: 'M7 20c0-2.8 2.2-5 5-5s5 2.2 5 5M1.5 19c0-2 1.5-3.6 3.5-3.6M22.5 19c0-2-1.5-3.6-3.5-3.6' }]],
    sliders: [['path', { d: 'M4 6h10M18 6h2M4 12h4M12 12h8M4 18h12M20 18h0' }], ['circle', { cx: 16, cy: 6, r: 2 }], ['circle', { cx: 10, cy: 12, r: 2 }], ['circle', { cx: 18, cy: 18, r: 2 }]],
    broom: [['path', { d: 'M14.5 3.5l-4 8M6.5 12.5h8.5l2 8H4.5z M9 16.5v4M12.5 16.5v4' }]],
    database: [['ellipse', { cx: 12, cy: 5.5, rx: 7.5, ry: 2.8 }], ['path', { d: 'M4.5 5.5v13c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8v-13M4.5 12c0 1.5 3.4 2.8 7.5 2.8s7.5-1.3 7.5-2.8' }]],
    search: [['circle', { cx: 11, cy: 11, r: 6.5 }], ['path', { d: 'M20 20l-4.3-4.3' }]]
  };

  function icon(name, attrs) {
    var svg = document.createElementNS(NS, 'svg');
    svg.setAttribute('viewBox', '0 0 24 24');
    svg.setAttribute('class', 'ico' + (attrs && attrs.class ? ' ' + attrs.class : ''));
    svg.setAttribute('aria-hidden', 'true');
    svg.setAttribute('focusable', 'false');
    (ICONS[name] || ICONS.x).forEach(function (part) {
      var node = document.createElementNS(NS, part[0]);
      Object.keys(part[1]).forEach(function (k) { node.setAttribute(k, part[1][k]); });
      svg.appendChild(node);
    });
    if (attrs && attrs.style) svg.setAttribute('style', attrs.style);
    return svg;
  }

  W.icon = icon;
  W.icon.names = Object.keys(ICONS);
})(window.WFS);
