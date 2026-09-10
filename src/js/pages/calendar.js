/* Team calendar: every day an employee has applied for Leave or WFH is marked.
   Chips carry three colours - planned leave (ochre), unplanned leave (wine) and
   WFH (teal) - so planned-ness is readable without opening a day. Leave vs WFH
   stays legible through the leaf/house icon and the legend regardless. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, TF = W.teamFilter, el = U.el;

  var view = { month: null, person: 'all', kind: 'both', status: 'all', selected: null };
  var inTeam = function () { return true; };     // refreshed from the team filter on every render

  function shortReason(rec, kind) {
    if (kind === 'wfh') return rec.reason;
    return rec.reason === 'Planned Leave' ? 'Planned' : rec.reason;
  }
  function halfTag(rec) {
    if (!S.isHalf(rec)) return null;
    return rec.duration.indexOf('1st') !== -1 ? '½AM' : '½PM';
  }

  function passes(rec, kind) {
    if (!inTeam(rec)) return false;
    if (view.kind !== 'both' && view.kind !== kind) return false;
    if (view.person !== 'all' && rec.name !== view.person) return false;
    if (view.status === 'all' && rec.status === 'Declined') return false;
    if (view.status !== 'all' && rec.status !== view.status) return false;
    return true;
  }

  function render(root) {
    if (!view.month) view.month = U.todayISO().slice(0, 7);
    inTeam = TF.matches();
    var members = S.state.people.filter(inTeam);
    if (view.person !== 'all' && !members.some(function (x) { return x.name === view.person; })) view.person = 'all';
    var index = S.byDate(passes);

    var p = view.month.split('-'), year = +p[0], mon = +p[1] - 1;
    var first = new Date(year, mon, 1);
    var start = U.addDays(first, -((first.getDay() + 6) % 7));   // grid starts Monday
    var cells = [];
    for (var i = 0; i < 42; i++) cells.push(U.addDays(start, i));
    while (cells.length > 35 && cells[35].getMonth() !== mon) cells = cells.slice(0, 35);

    /* ---- header controls ---- */
    function shift(n) {
      var d = new Date(year, mon + n, 1);
      view.month = d.getFullYear() + '-' + U.pad(d.getMonth() + 1);
      view.selected = null; W.app.rerender();
    }
    function ctl(label, opts, key) {
      var sel = el('select', {
        onchange: function (e) { view[key] = e.target.value; view.selected = null; W.app.rerender(); }
      }, opts.map(function (o) {
        return el('option', { value: o.v, selected: view[key] === o.v || null, text: o.t });
      }));
      return el('label', { class: 'f', style: 'font-size:11px' }, [label, sel]);
    }

    var head = el('div', { class: 'card-h' }, [
      el('button', { class: 'iconbtn', text: '‹', title: 'Previous month', onclick: function () { shift(-1); } }),
      el('button', { class: 'iconbtn', text: '›', title: 'Next month', onclick: function () { shift(1); } }),
      el('h3', { style: 'min-width:150px;font-size:15px', text: U.MONTHS[mon] + ' ' + year }),
      el('button', {
        class: 'btn sm', text: 'Today', onclick: function () {
          view.month = U.todayISO().slice(0, 7); view.selected = U.todayISO(); W.app.rerender();
        }
      }),
      el('span', { style: 'flex:1' }),
      TF.control(function () { view.selected = null; }),
      ctl('Member', [{ v: 'all', t: TF.value() !== 'all' ? 'Whole team' : S.seesEveryone() ? 'Everyone' : 'My team' }]
        .concat(members.map(function (x) { return { v: x.name, t: x.name }; })), 'person'),
      ctl('Type', [{ v: 'both', t: 'Leave + WFH' }, { v: 'leave', t: 'Leave only' }, { v: 'wfh', t: 'WFH only' }], 'kind'),
      ctl('Status', [{ v: 'all', t: 'Approved + Pending' }, { v: 'Approved', t: 'Approved' }, { v: 'Pending', t: 'Pending' }], 'status')
    ]);

    /* ---- grid ---- */
    var grid = el('div', { class: 'calgrid' });
    var today = U.todayISO();
    cells.forEach(function (d) {
      var key = U.iso(d);
      var entries = (index[key] || []).slice().sort(function (a, b) {
        return (a.kind === b.kind) ? (a.rec.name < b.rec.name ? -1 : 1) : (a.kind === 'leave' ? -1 : 1);
      });
      /* Colleagues' leave reaches employees without a reason (server-side
         redaction), so it gets the neutral leave colour rather than being
         guessed into planned/unplanned. */
      var nPlanned = entries.filter(function (e) {
        return e.kind === 'leave' && !e.rec.redacted && S.isPlanned(e.rec);
      }).length;
      var nUnplanned = entries.filter(function (e) {
        return e.kind === 'leave' && !e.rec.redacted && !S.isPlanned(e.rec);
      }).length;
      var nHidden = entries.filter(function (e) { return e.kind === 'leave' && e.rec.redacted; }).length;
      var nWfh = entries.filter(function (e) { return e.kind === 'wfh'; }).length;

      var cls = 'day';
      if (d.getMonth() !== mon) cls += ' out';
      if (U.isWeekend(d)) cls += ' we';
      if (key === today) cls += ' today';
      if (key === view.selected) cls += ' sel';

      var kids = [];
      if (entries.length) {
        kids.push(el('div', { class: 'daybar' }, [
          nPlanned ? el('i', { style: 'background:var(--bk-p);flex:' + nPlanned }) : null,
          nUnplanned ? el('i', { style: 'background:var(--bk-u);flex:' + nUnplanned }) : null,
          nHidden ? el('i', { style: 'background:var(--leave);flex:' + nHidden }) : null,
          nWfh ? el('i', { style: 'background:var(--wfh);flex:' + nWfh }) : null
        ]));
      }
      kids.push(el('div', { class: 'dnum' }, [
        el('b', { text: String(d.getDate()) }),
        entries.length ? el('span', { class: 'more', text: entries.length + '' }) : null
      ]));

      entries.slice(0, 3).forEach(function (e) {
        var tag = halfTag(e.rec);
        kids.push(el('div', {
          class: 'chip ' + e.kind +
            (e.kind === 'leave' && !e.rec.redacted ? (S.isPlanned(e.rec) ? ' planned' : ' unplanned') : '') +
            (e.rec.status === 'Pending' ? ' pend' : ''),
          title: e.rec.name + ' · ' + (e.kind === 'leave' ? 'Leave' : 'WFH') + ' · ' +
            (e.rec.redacted ? '' : e.rec.reason + ' · ') + e.rec.duration + ' · ' + e.rec.status
        }, [
          el('span', { text: e.kind === 'leave' ? '🌴' : '🏠', style: 'font-size:9px' }),
          el('span', { style: 'overflow:hidden;text-overflow:ellipsis', text: e.rec.name.split(' ')[0] }),
          tag ? el('span', { class: 'half', text: tag }) : null
        ]));
      });
      if (entries.length > 3) kids.push(el('div', { class: 'more', text: '+' + (entries.length - 3) + ' more' }));

      grid.appendChild(el('button', {
        class: cls, type: 'button',
        onclick: function () { view.selected = (view.selected === key ? null : key); W.app.rerender(); }
      }, kids));
    });

    var calCard = el('div', { class: 'card' }, [
      head,
      el('div', { class: 'calscroll' }, [
        el('div', { class: 'calhead' }, ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(function (n) {
          return el('div', { text: n });
        })),
        grid
      ]),
      el('div', { class: 'card-h', style: 'border-bottom:0;border-top:1px solid var(--line)' }, [
        el('div', { class: 'legend' }, [
          el('span', {}, [el('b', { style: 'background:var(--bk-p)' }), '🌴 Planned leave']),
          el('span', {}, [el('b', { style: 'background:var(--bk-u)' }), '🌴 Unplanned leave']),
          S.is('employee') ? el('span', {}, [el('b', { style: 'background:var(--leave)' }), '🌴 Colleague on leave']) : null,
          el('span', {}, [el('b', { style: 'background:var(--wfh)' }), '🏠 Work from home']),
          el('span', { style: 'color:var(--faint)', text: '⌐ dashed border = awaiting approval' }),
          el('span', { style: 'color:var(--faint)', text: '½AM / ½PM = half day' })
        ])
      ])
    ]);

    return el('div', { class: 'calwrap' }, [calCard, sidebar(index, year, mon)]);
  }

  /* ---- right-hand panel: selected day, or the month at a glance ---- */
  function sidebar(index, year, mon) {
    var out = el('div', { class: 'grid' });

    /* With a member picked in the filter, the form opens for them - if this user may file for them. */
    function prefill() {
      var p = S.applyPeople().filter(function (x) { return x.name === view.person; })[0];
      return { from: view.selected || undefined, empId: p ? p.empId : undefined };
    }
    out.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'card-b', style: 'display:flex;gap:9px' }, [
        el('button', {
          class: 'btn primary', style: 'flex:1', text: '🌴 Apply leave',
          onclick: function () { W.form.open('leave', prefill()); }
        }),
        el('button', {
          class: 'btn', style: 'flex:1', text: '🏠 Apply WFH',
          onclick: function () { W.form.open('wfh', prefill()); }
        })
      ])
    ]));

    if (view.selected) {
      var entries = index[view.selected] || [];
      var body = el('div', { class: 'side' });
      if (!entries.length) {
        body.appendChild(el('div', { class: 'empty', text: 'Nobody is away or remote on this day.' }));
      } else {
        ['leave', 'wfh'].forEach(function (kind) {
          var group = entries.filter(function (e) { return e.kind === kind; });
          if (!group.length) return;
          body.appendChild(el('div', {
            class: 'card-h',
            style: 'background:var(--surface-2);padding:7px 14px;border-bottom:1px solid var(--line)'
          }, [
            el('span', { class: 'pill ' + kind, text: (kind === 'leave' ? '🌴 Leave' : '🏠 WFH') + ' · ' + group.length })
          ]));
          group.forEach(function (e) {
            body.appendChild(el('div', { class: 'item' }, [
              U.avatar(e.rec.name),
              el('div', { style: 'flex:1;min-width:0' }, [
                el('div', { style: 'font-weight:600;font-size:13px', text: e.rec.name }),
                el('div', { class: 'sub', text: (e.rec.redacted ? (kind === 'leave' ? 'Leave' : 'WFH') : e.rec.reason) + ' · ' + e.rec.duration }),
                el('div', { class: 'sub mono', text: U.fmtRange(e.rec.from, e.rec.to) + ' · ' + U.n1(e.rec.days) + 'd' }),
                e.rec.comment ? el('div', { class: 'sub', style: 'font-style:italic', text: '“' + e.rec.comment + '”' }) : null
              ]),
              el('span', { class: 'pill ' + e.rec.status, text: e.rec.status })
            ]));
          });
        });
      }
      out.appendChild(el('div', { class: 'card' }, [
        el('div', { class: 'card-h' }, [
          el('h3', { text: U.fmt(view.selected) }),
          el('span', { style: 'flex:1' }),
          el('button', { class: 'iconbtn', text: '✕', title: 'Clear selection', onclick: function () { view.selected = null; W.app.rerender(); } })
        ]),
        body
      ]));
    }

    /* month at a glance */
    var mk = year + '-' + U.pad(mon + 1);
    var days = Object.keys(index).filter(function (k) { return k.slice(0, 7) === mk; });
    var leaveDays = 0, wfhDays = 0, peopleOut = {}, busiest = null;
    days.forEach(function (k) {
      var e = index[k];
      e.forEach(function (x) {
        if (x.kind === 'leave') leaveDays += x.fraction; else wfhDays += x.fraction;
        peopleOut[x.rec.name] = 1;
      });
      var l = e.filter(function (x) { return x.kind === 'leave'; }).length;
      if (!busiest || l > busiest.n) busiest = { date: k, n: l };
    });
    var wd = U.workingDaysInMonth(mk);

    out.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'card-h' }, [el('h3', { text: 'This month' }), el('span', { class: 'sub', text: wd + ' working days' })]),
      el('div', { class: 'card-b', style: 'display:grid;gap:10px' }, [
        statRow('🌴', 'Leave days taken', U.n1(leaveDays), 'var(--leave)'),
        statRow('🏠', 'WFH days', U.n1(wfhDays), 'var(--wfh)'),
        statRow('👥', 'People with time off', Object.keys(peopleOut).length + ' of ' + S.state.people.filter(inTeam).length, 'var(--muted)'),
        busiest && busiest.n > 1
          ? statRow('⚠️', 'Thinnest cover', U.fmt(busiest.date) + ' (' + busiest.n + ' out)', 'var(--warn)')
          : null
      ])
    ]));

    /* upcoming */
    var today = U.todayISO();
    var upcoming = S.both().filter(function (r) {
      return inTeam(r) && r.status !== 'Declined' && (r.to || r.from) >= today &&
        (view.person === 'all' || r.name === view.person);
    }).sort(function (a, b) { return a.from < b.from ? -1 : 1; }).slice(0, 6);

    out.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'card-h' }, [el('h3', { text: 'Coming up' })]),
      upcoming.length ? el('div', { class: 'side' }, upcoming.map(function (r) {
        return el('div', { class: 'item' }, [
          el('span', { style: 'font-size:15px', text: r.kind === 'leave' ? '🌴' : '🏠' }),
          el('div', { style: 'flex:1;min-width:0' }, [
            el('div', { style: 'font-weight:600;font-size:13px', text: r.name }),
            el('div', { class: 'sub', text: U.fmtRange(r.from, r.to) + ' · ' + U.n1(r.days) + 'd' })
          ]),
          el('span', { class: 'pill ' + r.status, text: r.status })
        ]);
      })) : el('div', { class: 'empty', text: 'Nothing scheduled ahead.' })
    ]));

    return out;
  }

  function statRow(icon, label, value, color) {
    return el('div', { class: 'row', style: 'justify-content:space-between;gap:8px' }, [
      el('span', { class: 'sub', text: icon + '  ' + label }),
      el('span', { class: 'num', style: 'font-weight:700;color:' + color, text: String(value) })
    ]);
  }

  W.pages = W.pages || {};
  W.pages.calendar = { id: 'calendar', label: 'Calendar', render: render };
})(window.WFS);
