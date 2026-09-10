/* The register - the web equivalent of the sheet's Leave and WFH tabs.
   Sortable, filterable, exportable to CSV. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, el = U.el;

  var view = { kind: 'leave', person: 'all', status: 'all', q: '', sort: 'from', dir: -1 };

  function rows() {
    var list = S.visible(view.kind);
    if (view.person !== 'all') list = list.filter(function (r) { return r.name === view.person; });
    if (view.status !== 'all') list = list.filter(function (r) { return r.status === view.status; });
    if (view.q) {
      var q = view.q.toLowerCase();
      list = list.filter(function (r) {
        return (r.name + ' ' + r.reason + ' ' + r.comment + ' ' + r.empId).toLowerCase().indexOf(q) !== -1;
      });
    }
    return list.sort(function (a, b) {
      var x = a[view.sort], y = b[view.sort];
      if (x === y) return 0;
      return (x > y ? 1 : -1) * view.dir;
    });
  }

  function render() {
    var list = rows();
    var cols = [
      { k: 'name', t: 'Team member' }, { k: 'empId', t: 'Emp ID' },
      { k: 'from', t: 'From' }, { k: 'to', t: 'To' },
      { k: 'duration', t: 'Duration' }, { k: 'days', t: 'Days' },
      { k: 'reason', t: view.kind === 'leave' ? 'Leave reason' : 'WFH reason' },
      { k: 'status', t: 'Status' }, { k: 'comment', t: 'Comment' }
    ];

    function sel(key, opts) {
      return el('select', { onchange: function (e) { view[key] = e.target.value; W.app.rerender(); } },
        opts.map(function (o) { return el('option', { value: o.v, selected: view[key] === o.v || null, text: o.t }); }));
    }

    return el('div', {}, [
      el('div', { class: 'row', style: 'margin-bottom:16px' }, [
        el('div', { class: 'tabs' }, [
          el('button', {
            class: view.kind === 'leave' ? 'on' : '', onclick: function () { view.kind = 'leave'; W.app.rerender(); }
          }, ['🌴 Leave', el('span', { class: 'count', text: String(S.visible('leave').length) })]),
          el('button', {
            class: view.kind === 'wfh' ? 'on' : '', onclick: function () { view.kind = 'wfh'; W.app.rerender(); }
          }, ['🏠 WFH', el('span', { class: 'count', text: String(S.visible('wfh').length) })])
        ]),
        el('span', { style: 'flex:1' }),
        el('label', { class: 'f', style: 'font-size:11px' }, ['Search', el('input', {
          type: 'search', placeholder: 'name, reason, comment…', value: view.q,
          oninput: function (e) { view.q = e.target.value; W.app.rerender(); }
        })]),
        el('label', { class: 'f', style: 'font-size:11px' }, ['Member',
          sel('person', [{ v: 'all', t: S.seesEveryone() ? 'Everyone' : 'My team' }].concat(S.teamPeople().map(function (p) { return { v: p.name, t: p.name }; })))]),
        el('label', { class: 'f', style: 'font-size:11px' }, ['Status',
          sel('status', [{ v: 'all', t: 'All' }, { v: 'Approved', t: 'Approved' }, { v: 'Pending', t: 'Pending' }, { v: 'Declined', t: 'Declined' }])]),
        el('button', { class: 'btn', text: '⬇ CSV', onclick: function () { exportCSV(list, cols); } })
      ]),
      el('div', { class: 'card' }, [
        el('div', { class: 'card-h' }, [
          el('h3', { text: (view.kind === 'leave' ? 'Leave' : 'WFH') + ' register' }),
          el('span', { class: 'count', text: String(list.length) }),
          el('span', { style: 'flex:1' }),
          el('span', { class: 'sub', text: U.n1(U.sum(list, function (r) { return r.days; })) + ' days total' })
        ]),
        list.length ? el('div', { class: 'scroll' }, [
          el('table', {}, [
            el('thead', {}, [el('tr', {}, cols.map(function (c) {
              return el('th', {
                style: 'cursor:pointer;user-select:none',
                onclick: function () {
                  if (view.sort === c.k) view.dir = -view.dir; else { view.sort = c.k; view.dir = 1; }
                  W.app.rerender();
                },
                text: c.t + (view.sort === c.k ? (view.dir > 0 ? ' ↑' : ' ↓') : '')
              });
            }).concat([el('th', {})]))]),
            el('tbody', {}, list.map(function (r) {
              return el('tr', {}, [
                el('td', {}, [el('div', { class: 'row', style: 'gap:9px;flex-wrap:nowrap' }, [
                  U.avatar(r.name), el('span', { style: 'font-weight:600', text: r.name })
                ])]),
                el('td', { class: 'mono', text: r.empId }),
                el('td', { class: 'num', text: U.fmt(r.from) }),
                el('td', { class: 'num', text: U.fmt(r.to || r.from) }),
                el('td', { class: 'sub', text: r.duration }),
                el('td', { class: 'num', style: 'font-weight:700', text: U.n1(r.days) }),
                el('td', {}, [el('span', { class: 'pill ghost', text: r.reason })]),
                el('td', {}, [el('span', { class: 'pill ' + r.status, text: r.status })]),
                el('td', { class: 'sub', text: r.comment || '—' }),
                el('td', { style: 'text-align:right' }, [
                  r.canDelete ? el('button', {
                    class: 'btn sm', text: '✕', title: 'Delete record',
                    onclick: function () {
                      if (confirm('Delete this ' + view.kind + ' record for ' + r.name + '?')) {
                        S.withdraw(view.kind, r.id).then(function (res) { if (res) U.toast('Record deleted'); });
                      }
                    }
                  }) : null
                ])
              ]);
            }))
          ])
        ]) : el('div', { class: 'empty', text: 'No records match those filters.' })
      ])
    ]);
  }

  function exportCSV(list, cols) {
    var head = cols.map(function (c) { return c.t; }).join(',');
    var body = list.map(function (r) {
      return cols.map(function (c) {
        return '"' + String(r[c.k] === undefined || r[c.k] === null ? '' : r[c.k]).replace(/"/g, '""') + '"';
      }).join(',');
    }).join('\n');
    var blob = new Blob([head + '\n' + body], { type: 'text/csv;charset=utf-8' });
    var a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'leave-planner-' + view.kind + '-' + U.todayISO() + '.csv';
    a.click();
    URL.revokeObjectURL(a.href);
  }

  W.pages = W.pages || {};
  W.pages.records = { id: 'records', label: 'Register', render: render };
})(window.WFS);
