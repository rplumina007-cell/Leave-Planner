/* Two dashboards on one page - Leave and WFH - switched by tab and sharing one
   filter window, so changing the date range or person carries across both.
   Leave is always broken out as the four buckets:
   Planned Leave / Unplanned Leave / Planned Half Day / Unplanned Half Day. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, C = W.charts, A = W.analytics, TF = W.teamFilter, el = U.el;

  var view = { tab: 'leave', from: null, to: null, team: 'all', person: 'all', includePending: true };

  /* Reason mixes are palette-matched to their dashboard: leave reasons lead with
     the leave category colours, WFH reasons lead with the WFH teal. Sharing one
     generic ramp put 'Planned Leave' in teal, which reads as WFH. */
  var LEAVE_MIX = ['var(--bk-p)', 'var(--bk-u)', 'var(--bk-ph)', 'var(--bk-uh)',
    'var(--c2)', 'var(--c6)'];
  var WFH_MIX = ['var(--wfh)', 'var(--c2)', 'var(--c6)', 'var(--c5)',
    'var(--c4)', 'var(--c3)'];

  function defaults() {
    var all = S.visible('leave').concat(S.visible('wfh')).map(function (r) { return U.monthKey(r.from); }).sort();
    view.from = view.from || all[0] || U.todayISO().slice(0, 7);
    view.to = view.to || all[all.length - 1] || U.todayISO().slice(0, 7);
  }

  /* Take the shared team choice (teamfilter.js) and keep the chosen person inside it. */
  function scopeToTeam() {
    view.team = TF.value();
    if (view.person !== 'all' && !TF.people().some(function (p) { return p.name === view.person; })) view.person = 'all';
  }

  function render(root) {
    defaults();
    if (view.from > view.to) view.to = view.from;
    scopeToTeam();
    var d = A.compute(view);

    return el('div', {}, [
      el('div', { class: 'row', style: 'margin-bottom:14px' }, [
        el('div', { class: 'tabs' }, [
          tabBtn('leave', '🌴 Leave dashboard', d.leave.length),
          tabBtn('wfh', '🏠 WFH dashboard', d.wfh.length),
          tabBtn('combined', '⚖️ Combined', d.leave.length + d.wfh.length)
        ]),
        el('span', { style: 'flex:1' }),
        el('span', {
          class: 'sub',
          text: (view.team === 'all' ? '' : TF.label() + ' · ') +
            (view.person === 'all' ? d.names.length + (d.names.length === 1 ? ' person' : ' people') : view.person) + ' · ' +
            d.workingDays + ' working days · ' + U.monthLabel(view.from) +
            (view.from === view.to ? '' : ' – ' + U.monthLabel(view.to))
        })
      ]),
      filterBar(d),
      view.tab === 'leave' ? leavePart(d) : view.tab === 'wfh' ? wfhPart(d) : combinedPart(d)
    ]);
  }

  function tabBtn(id, label, n) {
    return el('button', {
      class: view.tab === id ? 'on' : '',
      onclick: function () { view.tab = id; W.app.rerender(); }
    }, [label, el('span', { class: 'count', text: String(n) })]);
  }

  /* ---------- part 1: leave only ---------- */
  function leavePart(d) {
    return el('div', {}, [leaveOverview(d), leaveSection(d)]);
  }

  /* ---------- part 2: wfh only ---------- */
  function wfhPart(d) {
    return el('div', {}, [wfhSection(d)]);
  }

  /* ---------- part 3: leave + wfh together ---------- */
  function combinedPart(d) {
    var offDays = d.leaveDays + d.wfhDays;
    var offRate = U.pct(offDays, d.capacity);
    var officeDays = Math.max(0, d.capacity - offDays);

    var kpis = el('div', { class: 'kpis', style: 'margin-bottom:16px' }, [
      kpi('Off-site pressure', offRate + '<small>%</small>',
        'leave + WFH against ' + d.capacity + ' person-days',
        C.meter(offRate, 25, offRate > 15 ? 'var(--bad)' : 'var(--c6)')),
      kpi('Total days off-site', U.n1(offDays) + ' <small>d</small>',
        U.n1(d.leaveDays) + ' leave + ' + U.n1(d.wfhDays) + ' WFH'),
      kpi('Leave share', U.pct(d.leaveDays, offDays) + '<small>%</small>',
        'of all off-site time is leave', C.meter(d.leaveDays, offDays || 1, 'var(--leave)')),
      kpi('WFH share', U.pct(d.wfhDays, offDays) + '<small>%</small>',
        'of all off-site time is remote work', C.meter(d.wfhDays, offDays || 1, 'var(--wfh)')),
      kpi('Desk-side days', U.n1(officeDays) + ' <small>d</small>',
        U.pct(officeDays, d.capacity) + '% of capacity spent in office'),
      kpi('Awaiting approval', String(d.pendingLeave + d.pendingWfh),
        d.pendingLeave + ' leave · ' + d.pendingWfh + ' WFH')
    ]);

    /* per person: leave and WFH stacked, ranked by total time away from the desk */
    var rows = d.byPerson.slice()
      .map(function (p) {
        return {
          label: p.name, total: p.total + p.wfhDays,
          parts: [
            { value: p.total, color: 'var(--leave)', label: 'Leave' },
            { value: p.wfhDays, color: 'var(--wfh)', label: 'WFH' }
          ]
        };
      })
      .filter(function (r) { return r.total > 0; })
      .sort(function (a, z) { return z.total - a.total; });

    var monthCols = d.byMonth.map(function (m) {
      return { label: m.label, values: { leave: m.leaveDays, wfh: m.wfhDays } };
    });
    var kindSeries = [
      { key: 'leave', label: 'Leave', color: 'var(--leave)' },
      { key: 'wfh', label: 'WFH', color: 'var(--wfh)' }
    ];

    return el('div', {}, [
      kpis,
      el('div', { class: 'g2' }, [
        card('Total time off-site per person', 'leave and WFH stacked, ranked by total',
          rows.length ? C.stackedBars(rows, { legend: kindSeries, unit: 'd' })
            : el('div', { class: 'empty', text: 'Nothing in this window.' })),
        card('Off-site days by month', 'leave and WFH side by side',
          C.columns(monthCols, kindSeries, { showTotals: true }))
      ]),
      el('div', { class: 'g2', style: 'margin-top:16px' }, [
        timeSplitCard(d),
        rateCard(d)
      ]),
      sectionTitle('Coverage risk', 'when both kinds of absence land on the same day'),
      thinnestCard(d)
    ]);
  }

  function sectionTitle(t, tag) {
    return el('div', { class: 'section-title' }, [
      el('h2', { text: t }), el('span', { class: 'tag', text: tag }), el('span', { class: 'rule' })
    ]);
  }

  function filterBar(d) {
    function monthInput(key) {
      return el('input', {
        type: 'month', value: view[key],
        onchange: function (e) { view[key] = e.target.value; W.app.rerender(); }
      });
    }
    var person = el('select', {
      onchange: function (e) { view.person = e.target.value; W.app.rerender(); }
    }, [{ v: 'all', t: view.team !== 'all' ? 'Whole team' : S.seesEveryone() ? 'Everyone' : 'My team' }]
      .concat(TF.people().map(function (p) { return { v: p.name, t: p.name }; }))
      .map(function (o) { return el('option', { value: o.v, selected: view.person === o.v || null, text: o.t }); }));

    return el('div', { class: 'card', style: 'margin-bottom:18px' }, [
      el('div', { class: 'card-h', style: 'border-bottom:0' }, [
        el('label', { class: 'f', style: 'font-size:11px' }, ['From', monthInput('from')]),
        el('label', { class: 'f', style: 'font-size:11px' }, ['To', monthInput('to')]),
        TF.control(),
        el('label', { class: 'f', style: 'font-size:11px' }, ['Scope', person]),
        el('span', { style: 'flex:1' }),
        el('label', {
          class: 'row', style: 'gap:7px;font-size:12.5px;font-weight:600;color:var(--muted);cursor:pointer'
        }, [
          el('input', {
            type: 'checkbox', checked: view.includePending || null,
            onchange: function (e) { view.includePending = e.target.checked; W.app.rerender(); }
          }),
          'Count pending requests'
        ]),
        el('button', {
          class: 'btn sm', text: 'Reset', onclick: function () {
            view.from = null; view.to = null; TF.reset(); view.person = 'all'; view.includePending = true; W.app.rerender();
          }
        })
      ])
    ]);
  }

  /* value and detail are HTML (for the <small> units) - pass recorded text through U.esc. */
  function kpi(label, value, detail, extra) {
    return el('div', { class: 'kpi' }, [
      el('div', { class: 'k', text: label }),
      el('div', { class: 'v', html: value }),
      detail ? el('div', { class: 'd', html: detail }) : null,
      extra || null
    ]);
  }

  /* ---------- leave headline numbers ---------- */
  function leaveOverview(d) {
    return el('div', { class: 'kpis', style: 'margin-bottom:16px' }, [
      kpi('Total leave days', U.n1(d.leaveDays) + ' <small>d</small>',
        d.absenceRate + '% of ' + d.capacity + ' person-days', C.meter(d.absenceRate, 20, 'var(--leave)')),
      kpi('Planned', U.n1(d.plannedDays) + ' <small>d</small>',
        U.pct(d.plannedDays, d.leaveDays) + '% of all leave · booked ahead',
        C.meter(d.plannedDays, d.leaveDays || 1, 'var(--bk-p)')),
      kpi('Unplanned', U.n1(d.unplannedDays) + ' <small>d</small>',
        d.unplannedShare + '% of all leave · called in',
        C.meter(d.unplannedShare, 100, d.unplannedShare > 40 ? 'var(--bad)' : 'var(--bk-u)')),
      kpi('Half days', String(d.halfDays),
        U.n1(d.buckets.plannedHalf + d.buckets.unplannedHalf) + ' days across both categories'),
      kpi('Awaiting approval', String(d.pendingLeave),
        d.pendingLeave ? 'sitting in the manager queue' : 'queue is clear'),
      kpi('Clean records', d.zeroUnplanned + ' <small>/ ' + d.names.length + '</small>',
        'people with zero unplanned leave')
    ]);
  }

  /* ---------- leave ---------- */
  function leaveSection(d) {
    var b = d.buckets, c = d.bucketCounts;
    var bucketCards = el('div', { class: 'kpis quad', style: 'margin-bottom:16px' }, S.BUCKETS.map(function (bk) {
      var days = b[bk.id], cnt = c[bk.id];
      return el('div', { class: 'kpi', style: 'border-left:3px solid ' + bk.color }, [
        el('div', { class: 'k', text: bk.label }),
        el('div', { class: 'v', html: U.n1(days) + ' <small>d</small>' }),
        el('div', { class: 'd', text: cnt + ' request' + (cnt === 1 ? '' : 's') + ' · ' + U.pct(days, d.leaveDays) + '% of all leave' }),
        C.meter(days, d.leaveDays || 1, bk.color)
      ]);
    }));

    /* per-person stacked by the four buckets */
    var rows = d.byPerson.slice().sort(function (a, z) { return z.total - a.total; })
      .filter(function (p) { return p.total > 0; })
      .map(function (p) {
        return {
          label: p.name, total: p.total,
          parts: S.BUCKETS.map(function (bk) {
            return { value: p.buckets[bk.id], color: bk.color, label: bk.label };
          })
        };
      });

    var monthCols = d.byMonth.map(function (m) {
      return { label: m.label, values: m.buckets };
    });
    var bucketSeries = S.BUCKETS.map(function (bk) {
      return { key: bk.id, label: bk.label, color: bk.color };
    });

    /* reliability ranking - the insight the sheet never had */
    var reliability = d.byPerson.slice()
      .filter(function (p) { return p.episodes > 0; })
      .sort(function (a, z) { return z.unplannedPct - a.unplannedPct || z.unplannedDays - a.unplannedDays; });

    return el('div', {}, [
      bucketCards,
      el('div', { class: 'g2' }, [
        card('Leave days per person', 'split by the four leave categories',
          C.stackedBars(rows, { legend: bucketSeries, unit: 'd' })),
        card('Month-by-month leave', 'stacked by category, working days only',
          C.columns(monthCols, bucketSeries, { showTotals: true }))
      ]),
      el('div', { class: 'g2', style: 'margin-top:16px' }, [
        card('Leave reason mix', 'what people actually book', C.mixBar(d.leaveMix, LEAVE_MIX)),
        card('Which weekday absence lands on', 'unplanned leave clustering on Mondays or Fridays is worth a conversation',
          C.weekdayStrip(d.leaveWeekday, 'var(--bk-u)', 'var(--bk-p)', 'Unplanned', 'Planned'))
      ]),
      el('div', { style: 'margin-top:16px' }, [
        card('Planning discipline by person', 'share of each person\'s leave that was unplanned',
          reliability.length ? el('div', { class: 'scroll' }, [
            el('table', {}, [
              el('thead', {}, [el('tr', {}, [
                el('th', { text: 'Team member' }), el('th', { text: 'Total' }), el('th', { text: 'Planned' }),
                el('th', { text: 'Unplanned' }), el('th', { text: 'Half days' }),
                el('th', { text: 'Requests' }), el('th', { text: 'Unplanned share' })
              ])]),
              el('tbody', {}, reliability.map(function (p) {
                var half = p.buckets.plannedHalf + p.buckets.unplannedHalf;
                var tone = p.unplannedPct >= 60 ? 'Declined' : p.unplannedPct >= 30 ? 'Pending' : 'Approved';
                return el('tr', {}, [
                  el('td', {}, [el('div', { class: 'row', style: 'gap:9px;flex-wrap:nowrap' }, [
                    U.avatar(p.name), el('span', { style: 'font-weight:600', text: p.name })
                  ])]),
                  el('td', { class: 'num', style: 'font-weight:700', text: U.n1(p.total) + 'd' }),
                  el('td', { class: 'num', text: U.n1(p.plannedDays) + 'd' }),
                  el('td', { class: 'num', text: U.n1(p.unplannedDays) + 'd' }),
                  el('td', { class: 'num', text: half ? U.n1(half) + 'd' : '—' }),
                  el('td', { class: 'num', text: p.episodes + ' (' + p.unplannedEpisodes + ' unplanned)' }),
                  el('td', {}, [
                    el('div', { class: 'row', style: 'gap:9px;flex-wrap:nowrap' }, [
                      el('div', { class: 'bar', style: 'width:90px' }, [
                        el('i', { style: 'width:' + p.unplannedPct + '%;background:var(--bk-u)' })
                      ]),
                      el('span', { class: 'pill ' + tone, text: p.unplannedPct + '%' })
                    ])
                  ])
                ]);
              }))
            ])
          ]) : el('div', { class: 'empty', text: 'No leave recorded in this window.' }))
      ])
    ]);
  }

  /* ---------- wfh ---------- */
  function wfhSection(d) {
    var top = d.wfhMix[0];
    var peak = d.byMonth.slice().sort(function (a, z) { return z.wfhDays - a.wfhDays; })[0];
    var perPersonAvg = d.names.length ? d.wfhDays / d.names.length : 0;

    var kpis = el('div', { class: 'kpis', style: 'margin-bottom:16px' }, [
      kpi('Total WFH days', U.n1(d.wfhDays) + ' <small>d</small>', d.wfhEpisodes + ' requests logged'),
      kpi('Adoption', d.wfhAdoption + '<small>%</small>',
        d.wfhUsers.length + ' of ' + d.names.length + ' people worked remotely', C.meter(d.wfhAdoption, 100, 'var(--wfh)')),
      kpi('Remote rate', d.wfhRate + '<small>%</small>', 'of all available working time', C.meter(d.wfhRate, 20, 'var(--wfh)')),
      kpi('Avg per person', U.n1(perPersonAvg) + ' <small>d</small>', 'across the whole team'),
      kpi('Top reason', top ? U.esc(top.label) : '—', top ? U.n1(top.days) + ' d · ' + U.pct(top.days, d.wfhDays) + '% of WFH' : 'no data'),
      kpi('Busiest month', peak && peak.wfhDays ? U.esc(peak.label) : '—', peak && peak.wfhDays ? U.n1(peak.wfhDays) + ' WFH days' : 'no data')
    ]);

    var rows = d.byPerson.slice().sort(function (a, z) { return z.wfhDays - a.wfhDays; })
      .filter(function (p) { return p.wfhDays > 0; })
      .map(function (p) {
        return {
          label: p.name, total: p.wfhDays,
          parts: [{ value: p.wfhDays, color: 'var(--wfh)', label: 'WFH days' }]
        };
      });

    var monthCols = d.byMonth.map(function (m) { return { label: m.label, values: { wfh: m.wfhDays } }; });

    return el('div', {}, [
      kpis,
      el('div', { class: 'g2' }, [
        card('WFH days per person', 'who is using the policy',
          rows.length ? C.stackedBars(rows, { unit: 'd' }) : el('div', { class: 'empty', text: 'No WFH in this window.' })),
        card('WFH by month', 'volume trend',
          C.columns(monthCols, [{ key: 'wfh', label: 'WFH days', color: 'var(--wfh)' }], { showTotals: true, legend: false }))
      ]),
      el('div', { class: 'g2', style: 'margin-top:16px' }, [
        card('WFH reason mix', 'straight from the Validation list', C.mixBar(d.wfhMix, WFH_MIX)),
        card('WFH by weekday', 'heavy Monday/Friday skew signals a pattern, not an exception',
          C.weekdayStrip(d.wfhWeekday, 'var(--wfh)', 'transparent', '', ''))
      ])
    ]);
  }

  /* ---------- shared coverage cards ---------- */
  function timeSplitCard(d) {
    var cols = d.byMonth.map(function (m) {
      return { label: m.label, values: { onsite: m.onsite, wfh: m.wfhDays, leave: m.leaveDays } };
    });
    return card('Where the team\'s time went', 'person-days per month, split three ways',
      C.columns(cols, [
        { key: 'onsite', label: 'In office', color: 'var(--c5)' },
        { key: 'wfh', label: 'Working from home', color: 'var(--wfh)' },
        { key: 'leave', label: 'On leave', color: 'var(--leave)' }
      ], { height: 210 }));
  }

  function rateCard(d) {
    var rateLines = d.byMonth.map(function (m) {
      return { label: m.label, values: { wfh: m.wfhRate, leave: m.absenceRate } };
    });
    return card('Leave vs WFH rate over time',
      'both as a % of available working time — shows whether remote days substitute for leave',
      C.lines(rateLines, [
        { key: 'leave', label: 'Leave rate', color: 'var(--leave)' },
        { key: 'wfh', label: 'WFH rate', color: 'var(--wfh)' }
      ], { suffix: '%' }));
  }

  function thinnestCard(d) {
    var thin = d.coverage.filter(function (c) { return c.leave.length >= 2; }).slice(0, 10);
    return card('Thinnest coverage days', 'days with the most people on leave at once',
        thin.length ? el('div', { class: 'scroll' }, [
          el('table', {}, [
            el('thead', {}, [el('tr', {}, [
              el('th', { text: 'Date' }), el('th', { text: 'On leave' }),
              el('th', { text: 'WFH' }), el('th', { text: 'In office' })
            ])]),
            el('tbody', {}, thin.map(function (c) {
              return el('tr', {}, [
                el('td', {}, [
                  el('div', { style: 'font-weight:600', text: U.fmt(c.date) }),
                  el('div', { class: 'sub', text: U.DOW[U.parse(c.date).getDay()] })
                ]),
                el('td', {}, [
                  el('div', { class: 'row', style: 'gap:4px' }, c.leave.map(function (n) { return U.avatar(n); }))
                ]),
                el('td', { class: 'num', text: c.wfh.length ? c.wfh.length + '' : '—' }),
                el('td', {}, [
                  el('span', {
                    class: 'pill ' + (c.coverPct < 60 ? 'Declined' : c.coverPct < 80 ? 'Pending' : 'Approved'),
                    text: c.onsite + ' of ' + d.names.length
                  })
                ])
              ]);
            }))
          ])
        ]) : el('div', { class: 'empty', text: 'No day had two or more people on leave together.' }));
  }

  function card(title, sub, body) {
    return el('div', { class: 'card' }, [
      el('div', { class: 'card-h' }, [
        el('div', {}, [
          el('h3', { text: title }),
          sub ? el('div', { class: 'sub', text: sub }) : null
        ])
      ]),
      el('div', { class: 'card-b' }, [body])
    ]);
  }

  W.pages = W.pages || {};
  W.pages.insights = { id: 'insights', label: 'Dashboards', render: render };
})(window.WFS);
