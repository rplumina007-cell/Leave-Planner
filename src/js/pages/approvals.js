/* Manager queue. Two levels of separation, both explicit:
   Pending vs Decided (tabs), and within each, Leave vs WFH (separate cards). */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, el = U.el;

  var view = { tab: 'pending', person: 'all', selection: {} };

  function selKey(kind, id) { return kind + ':' + id; }
  /* Decided requests this user could act on - a manager's own reports, or all for an admin. */
  function mine(kind) { return S.decided(kind).filter(function (r) { return r.canDecide; }); }
  function selected() { return Object.keys(view.selection).filter(function (k) { return view.selection[k]; }); }

  function render(root) {
    var nPend = S.actionable('leave').length + S.actionable('wfh').length;
    var nDone = mine('leave').length + mine('wfh').length;

    var tabs = el('div', { class: 'tabs' }, [
      tabBtn('pending', 'Pending approval', nPend),
      tabBtn('decided', 'Approved & declined', nDone)
    ]);

    var filter = el('select', {
      onchange: function (e) { view.person = e.target.value; W.app.rerender(); }
    }, [{ v: 'all', t: 'All team members' }].concat(S.state.people.map(function (p) { return { v: p.name, t: p.name }; }))
      .map(function (o) { return el('option', { value: o.v, selected: view.person === o.v || null, text: o.t }); }));

    var bar = el('div', { class: 'row', style: 'margin-bottom:16px' }, [
      tabs,
      S.is('manager') ? el('span', { class: 'sub', text: 'Requests from people assigned to you' }) : null,
      el('span', { style: 'flex:1' }),
      el('label', { class: 'f', style: 'font-size:11px' }, ['Filter', filter])
    ]);

    var body = el('div', { class: 'grid' });

    if (view.tab === 'pending') {
      var picked = selected();
      if (picked.length) {
        body.appendChild(el('div', {
          class: 'card', style: 'border-color:var(--accent)'
        }, [
          el('div', { class: 'card-h', style: 'border-bottom:0' }, [
            el('b', { text: picked.length + ' request' + (picked.length > 1 ? 's' : '') + ' selected' }),
            el('span', { style: 'flex:1' }),
            el('button', { class: 'btn ok', text: '✓ Approve selected', onclick: function () { bulk('Approved'); } }),
            el('button', { class: 'btn bad', text: '✕ Decline selected', onclick: function () { bulk('Declined'); } }),
            el('button', { class: 'btn sm', text: 'Clear', onclick: function () { view.selection = {}; W.app.rerender(); } })
          ])
        ]));
      }
      body.appendChild(queue('leave', S.actionable('leave')));
      body.appendChild(queue('wfh', S.actionable('wfh')));
      if (!nPend) {
        body.appendChild(el('div', { class: 'card' }, [
          el('div', { class: 'empty', html: '<div style="font-size:26px">✅</div>Queue is clear — no requests waiting on you.' })
        ]));
      }
    } else {
      body.appendChild(history('leave', mine('leave')));
      body.appendChild(history('wfh', mine('wfh')));
    }

    return el('div', {}, [bar, body]);
  }

  function tabBtn(id, label, n) {
    return el('button', {
      class: view.tab === id ? 'on' : '',
      onclick: function () { view.tab = id; view.selection = {}; W.app.rerender(); }
    }, [label, el('span', { class: 'count', text: String(n) })]);
  }

  function byPerson(list) {
    return view.person === 'all' ? list : list.filter(function (r) { return r.name === view.person; });
  }

  /* ---- pending queue ---- */
  function queue(kind, listRaw) {
    var list = byPerson(listRaw);
    var title = kind === 'leave' ? '🌴 Leave requests' : '🏠 Work-from-home requests';
    var rows = list.map(function (rec) {
      var key = selKey(kind, rec.id);
      var others = S.clashes(rec).filter(function (o) { return o.rec.status !== 'Pending'; });
      var names = W.analytics.uniq(others.map(function (o) { return o.rec.name; }));
      var isPlanned = kind === 'leave' ? S.isPlanned(rec) : true;
      var notice = U.parse(rec.from) - new Date();
      var noticeDays = Math.round(notice / 86400000);

      return el('tr', {}, [
        el('td', { style: 'width:34px' }, [
          el('input', {
            type: 'checkbox', checked: view.selection[key] || null,
            onchange: function (e) { view.selection[key] = e.target.checked; W.app.rerender(); }
          })
        ]),
        el('td', {}, [
          el('div', { class: 'row', style: 'gap:9px;flex-wrap:nowrap' }, [
            U.avatar(rec.name),
            el('div', {}, [
              el('div', { style: 'font-weight:600', text: rec.name }),
              el('div', { class: 'sub mono', text: rec.empId })
            ])
          ])
        ]),
        el('td', {}, [
          el('div', { style: 'font-weight:600', text: U.fmtRange(rec.from, rec.to) }),
          el('div', { class: 'sub', text: rec.duration })
        ]),
        el('td', { class: 'num', style: 'font-weight:700', text: U.n1(rec.days) + 'd' }),
        el('td', {}, [
          el('span', { class: 'pill ' + (isPlanned ? 'ghost' : 'Pending'), text: rec.reason }),
          rec.comment ? el('div', { class: 'sub', style: 'font-style:italic;margin-top:3px', text: '“' + rec.comment + '”' }) : null
        ]),
        el('td', {}, [
          noticeDays >= 0
            ? el('span', { class: 'sub', text: noticeDays + ' days notice' })
            : el('span', { class: 'pill Declined', text: 'retrospective' }),
          names.length
            ? el('div', { class: 'sub', style: 'color:var(--warn);font-weight:600;margin-top:3px', text: '👥 ' + names.join(', ') + ' also out' })
            : el('div', { class: 'sub', style: 'color:var(--ok);margin-top:3px', text: '✓ no clash' })
        ]),
        el('td', { style: 'text-align:right;white-space:nowrap' }, [
          el('button', { class: 'btn ok sm', text: 'Approve', onclick: function () { decide(kind, rec, 'Approved'); } }),
          ' ',
          el('button', { class: 'btn bad sm', text: 'Decline', onclick: function () { askDecline(kind, rec); } })
        ])
      ]);
    });

    var allKeys = list.map(function (r) { return selKey(kind, r.id); });
    var allOn = allKeys.length && allKeys.every(function (k) { return view.selection[k]; });

    return el('div', { class: 'card' }, [
      el('div', { class: 'card-h' }, [
        el('h3', { text: title }),
        el('span', { class: 'count', text: String(list.length) }),
        el('span', { style: 'flex:1' }),
        el('span', { class: 'sub', text: U.n1(U.sum(list, function (r) { return r.days; })) + ' days awaiting decision' })
      ]),
      list.length ? el('div', { class: 'scroll' }, [
        el('table', {}, [
          el('thead', {}, [el('tr', {}, [
            el('th', {}, [el('input', {
              type: 'checkbox', checked: allOn || null, title: 'Select all',
              onchange: function (e) {
                allKeys.forEach(function (k) { view.selection[k] = e.target.checked; });
                W.app.rerender();
              }
            })]),
            el('th', { text: 'Team member' }), el('th', { text: 'Dates' }), el('th', { text: 'Days' }),
            el('th', { text: 'Reason' }), el('th', { text: 'Notice / conflicts' }), el('th', {})
          ])]),
          el('tbody', {}, rows)
        ])
      ]) : el('div', { class: 'empty', text: 'No pending ' + (kind === 'leave' ? 'leave' : 'WFH') + ' requests.' })
    ]);
  }

  /* ---- decided history ---- */
  function history(kind, listRaw) {
    var list = byPerson(listRaw);
    var approved = list.filter(function (r) { return r.status === 'Approved'; });
    var declined = list.filter(function (r) { return r.status === 'Declined'; });

    return el('div', { class: 'card' }, [
      el('div', { class: 'card-h' }, [
        el('h3', { text: kind === 'leave' ? '🌴 Leave history' : '🏠 WFH history' }),
        el('span', { style: 'flex:1' }),
        el('span', { class: 'pill Approved', text: approved.length + ' approved' }),
        declined.length ? el('span', { class: 'pill Declined', text: declined.length + ' declined' }) : null
      ]),
      list.length ? el('div', { class: 'scroll', style: 'max-height:520px;overflow-y:auto' }, [
        el('table', {}, [
          el('thead', {}, [el('tr', {}, [
            el('th', { text: 'Team member' }), el('th', { text: 'Dates' }), el('th', { text: 'Days' }),
            el('th', { text: 'Duration' }), el('th', { text: 'Reason' }), el('th', { text: 'Status' }), el('th', {})
          ])]),
          el('tbody', {}, list.slice(0, 200).map(function (rec) {
            return el('tr', {}, [
              el('td', {}, [el('div', { class: 'row', style: 'gap:9px;flex-wrap:nowrap' }, [
                U.avatar(rec.name), el('span', { style: 'font-weight:600', text: rec.name })
              ])]),
              el('td', { text: U.fmtRange(rec.from, rec.to) }),
              el('td', { class: 'num', text: U.n1(rec.days) + 'd' }),
              el('td', { class: 'sub', text: rec.duration }),
              el('td', {}, [el('span', { class: 'pill ghost', text: rec.reason })]),
              el('td', {}, [
                el('span', { class: 'pill ' + rec.status, text: rec.status }),
                rec.autoLogged ? el('div', { class: 'sub', style: 'margin-top:2px', text: 'logged after the fact' }) : null
              ]),
              el('td', { style: 'text-align:right' }, [
                el('button', {
                  class: 'btn sm', text: rec.status === 'Approved' ? 'Revoke' : 'Approve',
                  onclick: function () { decide(kind, rec, rec.status === 'Approved' ? 'Declined' : 'Approved'); }
                })
              ])
            ]);
          }))
        ])
      ]) : el('div', { class: 'empty', text: 'Nothing decided yet.' })
    ]);
  }

  /* ---- actions ---- */
  function decide(kind, rec, status, note) {
    return S.decide(kind, rec.id, status, note).then(function (res) {
      if (res) U.toast(rec.name + ' · ' + (kind === 'leave' ? 'Leave' : 'WFH') + ' ' + status.toLowerCase());
    });
  }

  function bulk(status) {
    var picked = selected();
    view.selection = {};
    /* one at a time, so each server response builds on the last */
    picked.reduce(function (chain, k) {
      var p = k.split(':');
      return chain.then(function (ok) {
        return S.decide(p[0], p[1], status, null).then(function (res) { return ok + (res ? 1 : 0); });
      });
    }, Promise.resolve(0)).then(function (ok) {
      U.toast(ok + ' request' + (ok === 1 ? '' : 's') + ' ' + status.toLowerCase());
      W.app.rerender();
    });
  }

  function askDecline(kind, rec) {
    var note = el('textarea', { rows: 3, placeholder: 'Reason for declining (visible to ' + rec.name.split(' ')[0] + ')' });
    var overlay = el('div', { class: 'modal' }, [
      el('div', { class: 'box' }, [
        el('div', { class: 'card-h' }, [el('h3', { text: 'Decline request' })]),
        el('div', { class: 'card-b', style: 'display:grid;gap:12px' }, [
          el('div', { class: 'sub' }, [
            el('b', { text: rec.name }), ' — ' + (kind === 'leave' ? 'Leave' : 'WFH') + ' · ' +
            U.fmtRange(rec.from, rec.to) + ' · ' + U.n1(rec.days) + ' day(s)'
          ]),
          el('label', { class: 'f' }, ['Note', note])
        ]),
        el('div', { class: 'card-h', style: 'border-top:1px solid var(--line);border-bottom:0;justify-content:flex-end' }, [
          el('button', { class: 'btn', text: 'Cancel', onclick: close }),
          el('button', {
            class: 'btn bad', text: 'Decline request', onclick: function () {
              decide(kind, rec, 'Declined', note.value.trim() || 'Declined by ' + S.me().name);
              close();
            }
          })
        ])
      ])
    ]);
    overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
    function close() { overlay.remove(); }
    document.body.appendChild(overlay);
    note.focus();
  }

  W.pages = W.pages || {};
  W.pages.approvals = { id: 'approvals', label: 'Approvals', render: render, badge: function () {
    return S.actionable('leave').length + S.actionable('wfh').length;
  } };
})(window.WFS);
