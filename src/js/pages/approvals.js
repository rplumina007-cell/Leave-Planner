/* Manager queue. Two levels of separation, both explicit:
   Pending vs Decided (tabs), and within each, Leave vs WFH (separate cards).
   Decisions apply at once, with Undo in the confirmation; requests that don't
   clash with anyone already off can be approved in one go. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, TF = W.teamFilter, el = U.el, I = W.icon;

  var view = { tab: 'pending', person: 'all', selection: {} };
  var inTeam = function () { return true; };     // refreshed from the team filter on every render

  function selKey(kind, id) { return kind + ':' + id; }
  /* Decided requests this user could act on - a manager's own reports, or all for an admin. */
  function mine(kind) { return S.decided(kind).filter(function (r) { return r.canDecide; }); }
  function selected() { return Object.keys(view.selection).filter(function (k) { return view.selection[k]; }); }
  /* No clash with anyone already approved off on the same days. */
  function clashFree(rec) {
    return !S.clashes(rec).some(function (o) { return o.rec.status !== 'Pending'; });
  }

  function render(root) {
    inTeam = TF.matches();
    var people = TF.people().filter(function (p) {
      return S.is('admin') || p.empId !== S.me().empId;    // a manager never decides their own
    });
    if (view.person !== 'all' && !people.some(function (p) { return p.name === view.person; })) view.person = 'all';
    var nPend = S.actionable('leave').filter(inTeam).length + S.actionable('wfh').filter(inTeam).length;
    var nDone = mine('leave').filter(inTeam).length + mine('wfh').filter(inTeam).length;

    var tabs = el('div', { class: 'tabs' }, [
      tabBtn('pending', 'Pending approval', nPend),
      tabBtn('decided', 'Approved & declined', nDone)
    ]);

    var filter = el('select', {
      onchange: function (e) { view.person = e.target.value; view.selection = {}; W.app.rerender(); }
    }, [{ v: 'all', t: 'All team members' }].concat(people.map(function (p) { return { v: p.name, t: p.name }; }))
      .map(function (o) { return el('option', { value: o.v, selected: view.person === o.v || null, text: o.t }); }));

    var eligible = [];
    if (view.tab === 'pending') {
      ['leave', 'wfh'].forEach(function (kind) {
        byPerson(S.actionable(kind)).forEach(function (r) { if (clashFree(r)) eligible.push(selKey(kind, r.id)); });
      });
    }

    var bar = el('div', { class: 'row', style: 'margin-bottom:16px' }, [
      tabs,
      S.is('manager') ? el('span', { class: 'sub', text: 'Requests from your team' }) : null,
      el('span', { style: 'flex:1' }),
      eligible.length ? el('button', {
        class: 'btn ok', type: 'button', title: 'Approves only requests that don’t overlap anyone already approved to be off',
        onclick: function () {
          if (confirm('Approve ' + eligible.length + ' request' + (eligible.length === 1 ? '' : 's') +
            ' that don’t clash with anyone already off?\n\nEach is re-checked just before approving.')) bulk('Approved', eligible, true);
        }
      }, [I('check'), 'Approve ' + eligible.length + ' without clashes']) : null,
      TF.control(function () { view.selection = {}; }),     // never bulk-act on requests the filter hides
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
            el('button', { class: 'btn ok', type: 'button', onclick: function () { bulk('Approved', selected()); } }, [I('check'), 'Approve selected']),
            el('button', { class: 'btn bad', type: 'button', onclick: function () { bulk('Declined', selected()); } }, [I('x'), 'Decline selected']),
            el('button', { class: 'btn sm', type: 'button', text: 'Clear', onclick: function () { view.selection = {}; W.app.rerender(); } })
          ])
        ]));
      }
      body.appendChild(queue('leave', S.actionable('leave')));
      body.appendChild(queue('wfh', S.actionable('wfh')));
      if (!nPend) {
        body.appendChild(el('div', { class: 'card' }, [
          el('div', { class: 'empty' }, [I('check'), 'Queue is clear — no requests waiting on you.'])
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
      class: view.tab === id ? 'on' : '', type: 'button',
      onclick: function () { view.tab = id; view.selection = {}; W.app.rerender(); }
    }, [label, el('span', { class: 'count', text: String(n) })]);
  }

  function byPerson(list) {
    list = list.filter(inTeam);
    return view.person === 'all' ? list : list.filter(function (r) { return r.name === view.person; });
  }

  function cardTitle(kind, text) {
    return el('h3', { style: 'display:flex;align-items:center;gap:8px' }, [I(kind === 'leave' ? 'leave' : 'home'), text]);
  }

  /* ---- pending queue ---- */
  function queue(kind, listRaw) {
    var list = byPerson(listRaw);
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
            type: 'checkbox', checked: view.selection[key] || null, 'aria-label': 'Select ' + rec.name,
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
          el('span', { class: 'pill ' + (isPlanned ? 'ghost' : 'Pending') }, [kind === 'leave' ? I(isPlanned ? 'planned' : 'unplanned') : null, rec.reason]),
          rec.comment ? el('div', { class: 'sub', style: 'font-style:italic;margin-top:3px', text: '“' + rec.comment + '”' }) : null
        ]),
        el('td', {}, [
          noticeDays >= 0
            ? el('span', { class: 'sub', text: noticeDays + ' days notice' })
            : el('span', { class: 'pill Declined', text: 'retrospective' }),
          names.length
            ? el('div', { class: 'sub', style: 'color:var(--warn);font-weight:600;margin-top:3px;display:flex;gap:5px;align-items:center' }, [I('users'), names.join(', ') + ' also out'])
            : el('div', { class: 'sub', style: 'color:var(--ok);margin-top:3px;display:flex;gap:5px;align-items:center' }, [I('check'), 'no clash'])
        ]),
        el('td', { style: 'text-align:right;white-space:nowrap' }, [
          el('button', { class: 'btn ok sm', type: 'button', text: 'Approve', onclick: function () { decide(kind, rec, 'Approved'); } }),
          ' ',
          el('button', { class: 'btn bad sm', type: 'button', text: 'Decline', onclick: function () { askDecline(kind, rec); } })
        ])
      ]);
    });

    var allKeys = list.map(function (r) { return selKey(kind, r.id); });
    var allOn = allKeys.length && allKeys.every(function (k) { return view.selection[k]; });

    return el('div', { class: 'card' }, [
      el('div', { class: 'card-h' }, [
        cardTitle(kind, kind === 'leave' ? 'Leave requests' : 'Work-from-home requests'),
        el('span', { class: 'count', text: String(list.length) }),
        el('span', { style: 'flex:1' }),
        el('span', { class: 'sub', text: U.n1(U.sum(list, function (r) { return r.days; })) + ' days awaiting decision' })
      ]),
      list.length ? el('div', { class: 'scroll' }, [
        el('table', {}, [
          el('thead', {}, [el('tr', {}, [
            el('th', {}, [el('input', {
              type: 'checkbox', checked: allOn || null, title: 'Select all', 'aria-label': 'Select all',
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
        cardTitle(kind, kind === 'leave' ? 'Leave history' : 'WFH history'),
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
              el('td', { style: 'text-align:right;white-space:nowrap' }, [
                rec.decidedAt ? el('button', {
                  class: 'btn sm', type: 'button', title: 'Back to the pending queue', onclick: function () { undo([[kind, rec.id]]); }
                }, [I('undo'), 'Undo']) : null,
                ' ',
                el('button', {
                  class: 'btn sm', type: 'button', text: rec.status === 'Approved' ? 'Revoke' : 'Approve',
                  onclick: function () { decide(kind, rec, rec.status === 'Approved' ? 'Declined' : 'Approved', null, true); }
                })
              ])
            ]);
          }))
        ])
      ]) : el('div', { class: 'empty', text: 'Nothing decided yet.' })
    ]);
  }

  /* ---- actions ---- */
  function decide(kind, rec, status, note, noUndo) {
    return S.decide(kind, rec.id, status, note).then(function (res) {
      if (!res) return;
      var msg = rec.name + ' · ' + (kind === 'leave' ? 'Leave' : 'WFH') + ' ' + status.toLowerCase();
      if (noUndo) U.toast(msg);
      else U.toast(msg, { action: 'Undo', onAction: function () { undo([[kind, rec.id]]); } });
    });
  }

  function undo(items) {
    items.reduce(function (chain, it) {
      return chain.then(function (n) {
        return S.undoDecision(it[0], it[1]).then(function (res) { return n + (res ? 1 : 0); });
      });
    }, Promise.resolve(0)).then(function (n) {
      if (n) U.toast(n === 1 ? 'Undone — back to pending' : n + ' decisions undone — back to pending');
    });
  }

  /* One at a time, so each server response builds on the last. With onlyClashFree,
     each request is re-checked against the latest state just before approving -
     two pending requests for the same days never both go through unseen. */
  function bulk(status, keys, onlyClashFree) {
    view.selection = {};
    var done = [], skipped = 0;
    keys.reduce(function (chain, k) {
      var p = k.split(':');
      return chain.then(function () {
        var rec = (S.state[p[0]] || []).filter(function (r) { return r.id === p[1]; })[0];
        if (!rec || rec.status !== 'Pending' || (onlyClashFree && !clashFree(rec))) { skipped++; return null; }
        return S.decide(p[0], p[1], status, null).then(function (res) { if (res) done.push(p); });
      });
    }, Promise.resolve()).then(function () {
      W.app.rerender();
      if (!done.length) { U.toast(skipped ? 'Nothing changed — ' + skipped + ' now clash or were already decided' : 'Nothing changed'); return; }
      U.toast(done.length + ' request' + (done.length === 1 ? '' : 's') + ' ' + status.toLowerCase() +
        (skipped ? ' · ' + skipped + ' skipped' : ''), { action: 'Undo', onAction: function () { undo(done); } });
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
          el('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: close }),
          el('button', {
            class: 'btn bad', type: 'button', text: 'Decline request', onclick: function () {
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
