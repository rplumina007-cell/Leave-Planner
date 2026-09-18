/* Team calendar: every day someone has applied for Leave or WFH is marked.
   Chips carry a shape as well as a colour, so they read without colour vision:
   planned leave (calendar tick, ochre), unplanned leave (alert, wine), WFH
   (house, teal); a colleague's leave whose details are hidden shows a sun.
   Public holidays are shaded and named. Drag across days to apply for a range.
   On a phone the month becomes a day-by-day list with a pinned Apply bar. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, TF = W.teamFilter, el = U.el, I = W.icon;

  var view = { month: null, person: 'all', kind: 'both', status: 'all', selected: null };
  var inTeam = function () { return true; };     // refreshed from the team filter on every render

  var PHONE = window.matchMedia ? window.matchMedia('(max-width: 760px)') : null;
  if (PHONE) {
    var onPhoneChange = function () { if (S.state) W.app.rerender(); };
    if (PHONE.addEventListener) PHONE.addEventListener('change', onPhoneChange);
    else if (PHONE.addListener) PHONE.addListener(onPhoneChange);
  }

  /* drag-to-apply: one gesture at a time, finished wherever the pointer is released */
  var drag = null;          // { grid, anchor, last }
  var touchTap = false;
  document.addEventListener('pointerup', function () { if (drag) finishDrag(); });

  function halfTag(rec) {
    if (!S.isHalf(rec)) return null;
    return rec.duration.indexOf('1st') !== -1 ? '½AM' : '½PM';
  }
  function kindIcon(rec, kind) {
    if (kind === 'wfh') return 'home';
    if (rec.redacted) return 'leave';
    return S.isPlanned(rec) ? 'planned' : 'unplanned';
  }

  /* A day is "booked" when someone already has approved leave on it. Managers and
     admins see approved planned leave; employees - who aren't shown whether a
     colleague's leave was planned - see any approved leave, so the kind of leave
     stays private. Only the team filter applies: Type and Status filters never
     hide a booking. */
  function booksDay(rec, kind) {
    if (kind !== 'leave' || rec.status !== 'Approved' || !inTeam(rec)) return false;
    return S.is('employee') ? true : !rec.redacted && S.isPlanned(rec);
  }
  function bookedTag() { return el('span', { class: 'booked-tag' }, [I('planned'), 'Booked']); }
  function bookedTitle(bk) {
    return 'Booked — ' + W.analytics.uniq(bk.map(function (e) { return e.rec.name; })).join(', ') +
      (S.is('employee') ? ' on approved leave' : ' on approved planned leave');
  }

  function passes(rec, kind) {
    if (!inTeam(rec)) return false;
    if (view.kind !== 'both' && view.kind !== kind) return false;
    if (view.person !== 'all' && rec.name !== view.person) return false;
    if (view.status === 'all' && rec.status === 'Declined') return false;
    if (view.status !== 'all' && rec.status !== view.status) return false;
    return true;
  }

  function sorted(entries) {
    return (entries || []).slice().sort(function (a, b) {
      return (a.kind === b.kind) ? (a.rec.name < b.rec.name ? -1 : 1) : (a.kind === 'leave' ? -1 : 1);
    });
  }

  function chip(e, fullName) {
    var tag = halfTag(e.rec);
    return el('div', {
      class: 'chip ' + e.kind +
        (e.kind === 'leave' && !e.rec.redacted ? (S.isPlanned(e.rec) ? ' planned' : ' unplanned') : '') +
        (e.rec.status === 'Pending' ? ' pend' : ''),
      title: e.rec.name + ' · ' + (e.kind === 'leave' ? 'Leave' : 'WFH') + ' · ' +
        (e.rec.redacted ? '' : e.rec.reason + ' · ') + e.rec.duration + ' · ' + e.rec.status
    }, [
      I(kindIcon(e.rec, e.kind)),
      el('span', { style: 'overflow:hidden;text-overflow:ellipsis', text: fullName ? e.rec.name : e.rec.name.split(' ')[0] }),
      tag ? el('span', { class: 'half', text: tag }) : null,
      fullName && e.rec.status === 'Pending' ? el('span', { class: 'half', text: 'pending' }) : null
    ]);
  }

  /* With a member picked in the filter, the form opens for them - if this user may file for them. */
  function prefill(extra) {
    var p = S.applyPeople().filter(function (x) { return x.name === view.person; })[0];
    var out = { from: view.selected || undefined, empId: p ? p.empId : undefined };
    Object.keys(extra || {}).forEach(function (k) { out[k] = extra[k]; });
    return out;
  }
  function applyButtons() {
    return [
      el('button', { class: 'btn primary', style: 'flex:1', type: 'button', onclick: function () { W.form.open('leave', prefill()); } },
        [I('leave'), 'Apply leave']),
      el('button', { class: 'btn', style: 'flex:1', type: 'button', onclick: function () { W.form.open('wfh', prefill()); } },
        [I('home'), 'Apply WFH'])
    ];
  }

  function render(root) {
    if (!view.month) view.month = U.todayISO().slice(0, 7);
    inTeam = TF.matches();
    var members = S.state.people.filter(inTeam);
    if (view.person !== 'all' && !members.some(function (x) { return x.name === view.person; })) view.person = 'all';
    var index = S.byDate(passes);
    var booked = S.byDate(booksDay);
    var phone = !!(PHONE && PHONE.matches);

    var p = view.month.split('-'), year = +p[0], mon = +p[1] - 1;

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
      el('button', { class: 'iconbtn', type: 'button', title: 'Previous month', 'aria-label': 'Previous month', onclick: function () { shift(-1); } }, [I('chevL')]),
      el('button', { class: 'iconbtn', type: 'button', title: 'Next month', 'aria-label': 'Next month', onclick: function () { shift(1); } }, [I('chevR')]),
      el('h3', { style: 'min-width:150px;font-size:15px', text: U.MONTHS[mon] + ' ' + year }),
      el('button', {
        class: 'btn sm', type: 'button', text: 'Today', onclick: function () {
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

    var calCard = el('div', { class: 'card' }, [
      head,
      phone ? agenda(index, year, mon, booked) : monthGrid(index, year, mon, booked),
      legend(phone)
    ]);

    return el('div', {}, [
      el('div', { class: 'calwrap' }, [calCard, sidebar(index, year, mon)]),
      el('div', { class: 'mobile-apply' }, applyButtons())
    ]);
  }

  /* ---- desktop: the month grid ---- */
  function monthGrid(index, year, mon, booked) {
    var first = new Date(year, mon, 1);
    var start = U.addDays(first, -((first.getDay() + 6) % 7));   // grid starts Monday
    var cells = [];
    for (var i = 0; i < 42; i++) cells.push(U.addDays(start, i));
    while (cells.length > 35 && cells[35].getMonth() !== mon) cells = cells.slice(0, 35);

    var grid = el('div', { class: 'calgrid' });
    var today = U.todayISO();
    cells.forEach(function (d) {
      var key = U.iso(d);
      var entries = sorted(index[key]);
      var hol = U.holidayName(key);
      /* Colleagues' leave reaches employees without a reason (server-side
         redaction), so it gets the neutral leave colour rather than being
         guessed into planned/unplanned. */
      var nPlanned = entries.filter(function (e) { return e.kind === 'leave' && !e.rec.redacted && S.isPlanned(e.rec); }).length;
      var nUnplanned = entries.filter(function (e) { return e.kind === 'leave' && !e.rec.redacted && !S.isPlanned(e.rec); }).length;
      var nHidden = entries.filter(function (e) { return e.kind === 'leave' && e.rec.redacted; }).length;
      var nWfh = entries.filter(function (e) { return e.kind === 'wfh'; }).length;

      var cls = 'day';
      if (d.getMonth() !== mon) cls += ' out';
      if (U.isWeekend(d)) cls += ' we';
      if (hol) cls += ' hol';
      var bk = booked[key] || [];
      if (bk.length) cls += ' booked';
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
        entries.length ? el('span', { class: 'more', text: entries.length + '' }) : null,
        bk.length ? bookedTag() : null
      ]));
      if (hol) kids.push(el('div', { class: 'holname', title: hol }, [I('holiday'), el('span', { text: hol })]));

      var cap = hol ? 2 : 3;
      entries.slice(0, cap).forEach(function (e) { kids.push(chip(e)); });
      if (entries.length > cap) kids.push(el('div', { class: 'more', text: '+' + (entries.length - cap) + ' more' }));

      grid.appendChild(el('button', {
        class: cls, type: 'button', data: { date: key },
        'aria-label': U.fmt(key) + (hol ? ', ' + hol : '') + (bk.length ? ', booked' : '') +
          (entries.length ? ', ' + entries.length + ' away or remote' : ''),
        title: bk.length ? bookedTitle(bk) : null,
        /* keyboard (detail 0) and touch taps select the day; mouse selection goes through the drag handler */
        onclick: function (e) { if (e.detail === 0 || touchTap) { touchTap = false; toggleDay(key); } }
      }, kids));
    });
    enableDrag(grid);

    return el('div', { class: 'calscroll' }, [
      el('div', { class: 'calhead' }, ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun'].map(function (n) {
        return el('div', { text: n });
      })),
      grid
    ]);
  }

  function toggleDay(key) {
    view.selected = view.selected === key ? null : key;
    W.app.rerender();
  }

  /* Press on a day and drag across others to apply for that range; a plain click
     still selects the day. Mouse and pen only - touch keeps scrolling. */
  function enableDrag(grid) {
    function dayOf(target) {
      var b = target && target.closest ? target.closest('.day') : null;
      return b && grid.contains(b) ? b : null;
    }
    grid.addEventListener('pointerdown', function (e) {
      if (e.pointerType === 'touch') { touchTap = true; return; }
      touchTap = false;
      if (e.button !== 0) return;
      var b = dayOf(e.target);
      if (!b) return;
      drag = { grid: grid, anchor: b.dataset.date, last: b.dataset.date };
      e.preventDefault();
    });
    grid.addEventListener('pointerover', function (e) {
      if (!drag || drag.grid !== grid) return;
      var b = dayOf(e.target);
      if (!b || b.dataset.date === drag.last) return;
      drag.last = b.dataset.date;
      var lo = drag.anchor < drag.last ? drag.anchor : drag.last;
      var hi = drag.anchor < drag.last ? drag.last : drag.anchor;
      grid.querySelectorAll('.day').forEach(function (x) {
        var k = x.dataset.date;
        x.classList.toggle('rng', k >= lo && k <= hi);
      });
    });
  }

  function finishDrag() {
    var g = drag;
    drag = null;
    if (!g.grid.isConnected) return;
    if (g.anchor === g.last) { toggleDay(g.anchor); return; }
    g.grid.querySelectorAll('.day.rng').forEach(function (x) { x.classList.remove('rng'); });
    chooseKind(g.anchor < g.last ? g.anchor : g.last, g.anchor < g.last ? g.last : g.anchor);
  }

  function chooseKind(from, to) {
    var days = U.workingDaysBetween(from, to).length;
    var overlay = el('div', { class: 'modal' });
    function close() { overlay.remove(); document.removeEventListener('keydown', onKey); }
    function onKey(e) { if (e.key === 'Escape') close(); }
    function go(kind) { close(); W.form.open(kind, prefill({ from: from, to: to })); }
    overlay.appendChild(el('div', { class: 'box', style: 'width:min(420px,100%)' }, [
      el('div', { class: 'card-h' }, [
        el('h3', { text: 'Apply for ' + U.fmtRange(from, to) }),
        el('span', { style: 'flex:1' }),
        el('button', { class: 'iconbtn', type: 'button', title: 'Close', 'aria-label': 'Close', onclick: close }, [I('x')])
      ]),
      el('div', { class: 'card-b', style: 'display:grid;gap:14px' }, [
        el('div', {
          class: 'sub', text: days ? days + (days === 1 ? ' working day' : ' working days') + ' in this range'
            : 'No working days in this range — weekends and public holidays don’t count.'
        }),
        el('div', { class: 'row', style: 'gap:9px;flex-wrap:nowrap' }, [
          el('button', { class: 'btn primary', style: 'flex:1', type: 'button', disabled: !days || null, onclick: function () { go('leave'); } }, [I('leave'), 'Apply leave']),
          el('button', { class: 'btn', style: 'flex:1', type: 'button', disabled: !days || null, onclick: function () { go('wfh'); } }, [I('home'), 'Apply WFH'])
        ])
      ])
    ]));
    overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
    document.addEventListener('keydown', onKey);
    document.body.appendChild(overlay);
    var firstBtn = overlay.querySelector('.btn.primary');
    if (firstBtn && !firstBtn.disabled) firstBtn.focus();
  }

  /* ---- phone: the month as a list of days that have something on ---- */
  function agenda(index, year, mon, booked) {
    var list = el('div', { class: 'agenda' });
    var today = U.todayISO();
    var d = new Date(year, mon, 1), shown = 0;
    while (d.getMonth() === mon) {
      var key = U.iso(d), entries = sorted(index[key]), hol = U.holidayName(key), bk = booked[key] || [];
      if (entries.length || hol || bk.length || key === today) {
        list.appendChild(el('div', {
          class: 'arow' + (key === today ? ' today' : '') + (hol ? ' hol' : '') + (bk.length ? ' booked' : ''),
          title: bk.length ? bookedTitle(bk) : null
        }, [
          el('div', { class: 'adate' }, [el('span', { text: U.DOW[d.getDay()] }), el('b', { text: String(d.getDate()) })]),
          el('div', { class: 'achips' },
            (bk.length ? [bookedTag()] : [])
              .concat(hol ? [el('div', { class: 'holname' }, [I('holiday'), el('span', { text: hol })])] : [])
              .concat(entries.map(function (e) { return chip(e, true); }))
              .concat(!entries.length && !hol ? [el('div', { class: 'sub', text: 'Today — nobody away or remote' })] : []))
        ]));
        shown++;
      }
      d = U.addDays(d, 1);
    }
    if (!shown) list.appendChild(el('div', { class: 'empty', text: 'Nobody is away or remote this month.' }));
    return list;
  }

  function legend(phone) {
    return el('div', { class: 'card-h', style: 'border-bottom:0;border-top:1px solid var(--line)' }, [
      el('div', { class: 'legend' }, [
        el('span', {}, [el('b', { style: 'background:var(--bk-p)' }), I('planned'), 'Planned leave']),
        el('span', {}, [el('b', { style: 'background:var(--bk-u)' }), I('unplanned'), 'Unplanned leave']),
        S.is('employee') ? el('span', {}, [el('b', { style: 'background:var(--leave)' }), I('leave'), 'Colleague on leave']) : null,
        el('span', {}, [el('b', { style: 'background:var(--wfh)' }), I('home'), 'Work from home']),
        el('span', {}, [I('holiday'), 'Public holiday']),
        el('span', {}, [el('b', { class: 'booked-swatch' }),
          S.is('employee') ? 'Booked — a teammate is on approved leave' : 'Booked — approved planned leave']),
        el('span', { style: 'color:var(--faint)', text: 'Dashed border = awaiting approval' }),
        el('span', { style: 'color:var(--faint)', text: '½AM / ½PM = half day' }),
        phone ? null : el('span', { class: 'hint', text: 'Tip: drag across days to apply for a range' })
      ])
    ]);
  }

  /* ---- right-hand panel ---- */
  function sidebar(index, year, mon) {
    var out = el('div', { class: 'grid' });

    out.appendChild(el('div', { class: 'card apply-card' }, [
      el('div', { class: 'card-b', style: 'display:flex;gap:9px' }, applyButtons())
    ]));
    out.appendChild(myRequests());

    if (view.selected) {
      var entries = index[view.selected] || [];
      var hol = U.holidayName(view.selected);
      var body = el('div', { class: 'side' });
      if (hol) {
        body.appendChild(el('div', { class: 'item' }, [el('div', { class: 'holname', style: 'font-size:12.5px' }, [I('holiday'), el('span', { text: hol + ' — public holiday' })])]));
      }
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
            el('span', { class: 'pill ' + kind }, [I(kind === 'leave' ? 'leave' : 'home'), (kind === 'leave' ? 'Leave' : 'WFH') + ' · ' + group.length])
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
          el('button', { class: 'iconbtn', type: 'button', title: 'Clear selection', 'aria-label': 'Clear selection', onclick: function () { view.selected = null; W.app.rerender(); } }, [I('x')])
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
    var hols = (S.state.holidays || []).filter(function (h) { return h.date.slice(0, 7) === mk; }).length;

    out.appendChild(el('div', { class: 'card' }, [
      el('div', { class: 'card-h' }, [
        el('h3', { text: 'This month' }),
        el('span', { class: 'sub', text: wd + ' working days' + (hols ? ' · ' + hols + (hols === 1 ? ' public holiday' : ' public holidays') : '') })
      ]),
      el('div', { class: 'card-b', style: 'display:grid;gap:10px' }, [
        statRow('leave', 'Leave days taken', U.n1(leaveDays), 'var(--leave)'),
        statRow('home', 'WFH days', U.n1(wfhDays), 'var(--wfh)'),
        statRow('users', 'People with time off', Object.keys(peopleOut).length + ' of ' + S.state.people.filter(inTeam).length, 'var(--muted)'),
        busiest && busiest.n > 1
          ? statRow('alert', 'Thinnest cover', U.fmt(busiest.date) + ' (' + busiest.n + ' out)', 'var(--warn)')
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
          el('span', { style: 'font-size:17px;margin-top:2px;color:' + (r.kind === 'wfh' ? 'var(--wfh)' : 'var(--leave)') }, [I(kindIcon(r, r.kind))]),
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

  /* Your own requests that still matter: anything pending, and anything approved
     that hasn't finished yet. Pending ones can be withdrawn. */
  function myRequests() {
    var today = U.todayISO();
    var mine = S.myRequests().filter(function (r) {
      return r.status === 'Pending' || (r.status === 'Approved' && (r.to || r.from) >= today);
    }).sort(function (a, b) { return a.from < b.from ? -1 : 1; });
    var nPending = mine.filter(function (r) { return r.status === 'Pending'; }).length;

    return el('div', { class: 'card' }, [
      el('div', { class: 'card-h' }, [
        el('h3', { text: 'My requests' }),
        el('span', { style: 'flex:1' }),
        nPending ? el('span', { class: 'pill Pending', text: nPending + ' pending' }) : null
      ]),
      mine.length ? el('div', { class: 'side' }, mine.slice(0, 6).map(function (r) {
        return el('div', { class: 'item' }, [
          el('span', { style: 'font-size:17px;margin-top:2px;color:' + (r.kind === 'wfh' ? 'var(--wfh)' : 'var(--leave)') }, [I(kindIcon(r, r.kind))]),
          el('div', { style: 'flex:1;min-width:0' }, [
            el('div', { style: 'font-weight:600;font-size:13px', text: U.fmtRange(r.from, r.to) }),
            el('div', { class: 'sub', text: (r.kind === 'wfh' ? 'WFH' : 'Leave') + ' · ' + r.reason + ' · ' + U.n1(r.days) + 'd' })
          ]),
          el('div', { style: 'display:flex;flex-direction:column;align-items:flex-end;gap:6px' }, [
            el('span', { class: 'pill ' + r.status, text: r.status }),
            r.status === 'Pending' && r.canDelete
              ? el('button', { class: 'btn sm', type: 'button', onclick: function () { withdraw(r); } }, [I('undo'), 'Withdraw'])
              : null
          ])
        ]);
      }).concat(mine.length > 6 ? [el('div', { class: 'item sub', text: '+' + (mine.length - 6) + ' more' })] : []))
        : el('div', { class: 'empty', style: 'padding:24px 18px', text: 'Nothing pending or coming up.' })
    ]);
  }

  function withdraw(r) {
    if (!confirm('Withdraw your ' + (r.kind === 'wfh' ? 'WFH' : 'leave') + ' request for ' + U.fmtRange(r.from, r.to) + '?')) return;
    S.withdraw(r.kind, r.id).then(function (res) { if (res) U.toast('Request withdrawn'); });
  }

  function statRow(icon, label, value, color) {
    return el('div', { class: 'stat' }, [
      el('span', { class: 'sub' }, [I(icon), label]),
      el('span', { class: 'num', style: 'font-weight:700;color:' + color, text: String(value) })
    ]);
  }

  W.pages = W.pages || {};
  W.pages.calendar = { id: 'calendar', label: 'Calendar', render: render };
})(window.WFS);
