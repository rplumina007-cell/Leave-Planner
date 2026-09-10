/* Admin settings: Users, Dropdowns, Cleanup, Data.
   Only admins get the button; the server refuses these calls from anyone else. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, el = U.el;
  var ROLES = [['employee', 'Employee'], ['manager', 'Manager'], ['admin', 'Admin']];

  function randomPassword(emp, name) {
    var chars = 'ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz23456789';
    for (;;) {
      var a = new Uint32Array(14), pw = '';
      crypto.getRandomValues(a);
      for (var i = 0; i < a.length; i++) pw += chars[a[i] % chars.length];
      var low = pw.toLowerCase();
      var clash = (emp && low.indexOf(String(emp).toLowerCase()) !== -1) ||
        String(name || '').split(/\s+/).some(function (p) { return p.length > 2 && low.indexOf(p.toLowerCase()) !== -1; });
      if (!clash) return pw;
    }
  }

  function download(text, filename, type) {
    var a = document.createElement('a');
    a.href = URL.createObjectURL(new Blob([text], { type: type }));
    a.download = filename;
    a.click();
    setTimeout(function () { URL.revokeObjectURL(a.href); }, 1000);
  }

  function team() { return S.state.team || []; }
  function managers(exceptEmp) {
    return team().filter(function (u) {
      return u.active && (u.role === 'manager' || u.role === 'admin') && u.empId !== exceptEmp;
    });
  }

  /* Checkboxes for All Teams plus every team the admin created. All Teams already
     covers every team, so ticking it greys the rest out. */
  function teamChecks(selected) {
    var names = [S.ALL_TEAMS].concat(S.state.teams || []);
    var boxes = names.map(function (t) {
      return el('input', { type: 'checkbox', value: t, checked: selected.indexOf(t) !== -1 || null });
    });
    function sync() {
      boxes.slice(1).forEach(function (b) { b.disabled = boxes[0].checked; });
    }
    boxes.forEach(function (b) { b.addEventListener('change', sync); });
    sync();
    return {
      node: el('div', { class: 'row', style: 'gap:6px 16px' }, names.map(function (t, i) {
        return el('label', { class: 'row', style: 'gap:6px;flex-wrap:nowrap;font-size:12.5px;font-weight:600;cursor:pointer' },
          [boxes[i], i ? t : 'All Teams — everyone']);
      }).concat(names.length > 1 ? [] : [
        el('span', { class: 'sub', style: 'font-size:12px', text: 'Create teams in the Teams tab.' })
      ])),
      value: function () {
        return boxes[0].checked ? [S.ALL_TEAMS]
          : boxes.filter(function (b) { return b.checked; }).map(function (b) { return b.value; });
      }
    };
  }

  function open() {
    var tab = 'users';
    var TABS = [['users', 'Users'], ['teams', 'Teams'], ['lists', 'Dropdowns'], ['clean', 'Cleanup'], ['data', 'Data']];
    var tabStrip = el('div', { class: 'tabs' });
    var body = el('div', {});
    var overlay = el('div', { class: 'modal' }, [
      el('div', { class: 'box wide' }, [
        el('div', { class: 'card-h', style: 'border-bottom:0;padding-bottom:0' }, [
          el('h3', { text: 'Settings' }), el('span', { style: 'flex:1' }),
          el('button', { class: 'iconbtn', text: '✕', title: 'Close', onclick: close })
        ]),
        el('div', { class: 'card-h', style: 'padding-top:12px' }, [tabStrip]),
        body
      ])
    ]);

    function draw() {
      U.clear(tabStrip);
      TABS.forEach(function (t) {
        tabStrip.appendChild(el('button', {
          class: tab === t[0] ? 'on' : '', onclick: function () { tab = t[0]; draw(); }
        }, [t[1]]));
      });
      U.clear(body);
      body.appendChild(tab === 'users' ? usersTab() : tab === 'teams' ? teamsTab() : tab === 'lists' ? listsTab() : tab === 'clean' ? cleanTab() : dataTab());
    }
    function after(p) { return p.then(function (res) { draw(); return res; }); }
    function close() { overlay.remove(); W.app.rerender(); }

    /* ================= Users ================= */
    function usersTab() {
      var emp = el('input', { type: 'text', placeholder: 'e.g. 106767', autocomplete: 'off' });
      var name = el('input', { type: 'text', placeholder: 'Full name', autocomplete: 'off' });
      var pw = el('input', { type: 'text', placeholder: 'One-time password', autocomplete: 'off', spellcheck: 'false' });
      var role = el('select', {}, ROLES.map(function (r) { return el('option', { value: r[0], text: r[1] }); }));
      var mgr = el('select', {}, [el('option', { value: '', text: '— none —' })].concat(managers().map(function (m) {
        return el('option', { value: m.empId, text: m.name + ' (' + m.role + ')' });
      })));
      var teamPick = teamChecks([]);
      var err = el('div', {});
      var done = el('div', {});

      function create() {
        U.clear(err); U.clear(done);
        var body = {
          empId: emp.value.trim(), name: name.value.trim(), password: pw.value, role: role.value,
          managerEmpId: mgr.value || null, teams: teamPick.value()
        };
        if (!body.empId || !body.name || !body.password) {
          err.appendChild(el('div', { class: 'err', text: 'Employee ID, full name and password are all required.' }));
          return;
        }
        S.createUser(body).then(function () {
          U.toast('Login created for ' + body.name);
          draw();
        }, function (e) { err.appendChild(el('div', { class: 'err', text: e.message })); });
      }

      var unregistered = S.state.unregistered || [];
      var rows = team().map(function (u) {
        var self = u.empId === S.me().empId;
        var reports = team().filter(function (x) { return x.managerEmpId === u.empId; }).length;
        var roleSel = el('select', {
          disabled: self || null, title: self ? 'You cannot change your own role' : null,
          onchange: function (e) {
            var msg = 'Change ' + u.name + ' to ' + e.target.value + '? They will be signed out.';
            if (u.role !== 'employee' && e.target.value === 'employee' && reports) {
              msg += '\n\nTheir ' + reports + ' assigned report(s) will be left without a manager.';
            }
            if (!confirm(msg)) { e.target.value = u.role; return; }
            after(S.updateUser({ empId: u.empId, role: e.target.value })).then(function (res) {
              if (res) U.toast(u.name + ' is now ' + e.target.value + (res.unassigned ? ' — ' + res.unassigned + ' report(s) unassigned' : ''));
            });
          }
        }, ROLES.map(function (r) { return el('option', { value: r[0], selected: u.role === r[0] || null, text: r[1] }); }));
        var mgrSel = el('select', {
          onchange: function (e) {
            after(S.updateUser({ empId: u.empId, managerEmpId: e.target.value || null })).then(function (res) {
              if (res) U.toast(u.name + (e.target.value ? ' assigned to a new manager' : ' has no manager now'));
            });
          }
        }, [el('option', { value: '', text: '— none —' })].concat(managers(u.empId).map(function (m) {
          return el('option', { value: m.empId, selected: u.managerEmpId === m.empId || null, text: m.name });
        })));

        return el('tr', { style: u.active ? null : 'opacity:.55' }, [
          el('td', {}, [el('div', { class: 'row', style: 'gap:9px;flex-wrap:nowrap' }, [
            U.avatar(u.name),
            el('div', {}, [
              el('div', { style: 'font-weight:600', text: u.name + (self ? ' (you)' : '') }),
              el('div', { class: 'sub mono', text: u.empId + (reports ? ' · manages ' + reports : '') })
            ])
          ])]),
          el('td', {}, [roleSel]),
          el('td', {}, [mgrSel]),
          el('td', { style: 'min-width:150px' }, [
            el('div', { class: 'row', style: 'gap:4px' }, (u.teams || []).length
              ? u.teams.map(function (t) { return el('span', { class: 'pill ghost', text: t }); })
              : [el('span', { class: 'sub', style: 'font-size:11.5px', text: u.role === 'admin' ? 'Admins see everyone' : 'No team — sees only themselves' })]),
            el('button', { class: 'btn sm', style: 'margin-top:5px', text: 'Edit teams', onclick: function () { askTeams(u); } })
          ]),
          el('td', {}, [
            el('span', { class: 'pill ' + (u.active ? 'Approved' : 'Declined'), text: u.active ? 'Active' : 'Inactive' }),
            u.mustChange ? el('div', { class: 'sub', style: 'font-size:11px;margin-top:2px', text: 'awaiting first sign-in' }) : null
          ]),
          el('td', { style: 'text-align:right;white-space:nowrap' }, [
            el('button', { class: 'btn sm', text: 'Reset password', onclick: function () { askReset(u); } }), ' ',
            self ? null : el('button', {
              class: 'btn sm', text: u.active ? 'Deactivate' : 'Activate', onclick: function () {
                if (u.active && !confirm('Deactivate ' + u.name + '? They will be signed out and unable to sign in.')) return;
                after(S.updateUser({ empId: u.empId, active: !u.active }));
              }
            }), ' ',
            self ? null : el('button', {
              class: 'btn bad sm', text: 'Delete', onclick: function () {
                if (!confirm('Delete the login for ' + u.name + '?\n\nTheir leave and WFH records are kept.')) return;
                after(S.deleteUser(u.empId)).then(function (res) { if (res) U.toast('Login deleted'); });
              }
            })
          ])
        ]);
      });

      return el('div', { class: 'card-b', style: 'display:grid;gap:16px' }, [
        el('div', {}, [
          el('h3', { style: 'font-size:13px;margin-bottom:10px', text: 'Create a login' }),
          el('div', { class: 'f2' }, [
            el('label', { class: 'f' }, ['Employee ID', emp]),
            el('label', { class: 'f' }, ['User name (full name)', name])
          ]),
          el('div', { class: 'f2', style: 'margin-top:12px' }, [
            el('label', { class: 'f' }, ['Password', el('div', { class: 'row', style: 'gap:6px;flex-wrap:nowrap' }, [
              pw, el('button', {
                class: 'btn sm', type: 'button', text: 'Generate',
                onclick: function () { pw.value = randomPassword(emp.value, name.value); }
              })
            ])]),
            el('div', { class: 'f2' }, [el('label', { class: 'f' }, ['Role', role]), el('label', { class: 'f' }, ['Manager', mgr])])
          ]),
          el('div', { class: 'f', style: 'margin-top:12px' }, ['Teams — decide who they can see', teamPick.node]),
          el('div', { class: 'row', style: 'margin-top:12px;justify-content:space-between' }, [
            el('div', { class: 'sub', style: 'font-size:12px', text: 'They must choose their own password at first sign-in.' }),
            el('button', { class: 'btn primary', text: 'Create login', onclick: create })
          ]),
          err, done,
          unregistered.length ? el('div', { style: 'margin-top:10px' }, [
            el('div', { class: 'sub', style: 'font-size:12px;margin-bottom:6px', text: 'People in the records without a login — click to fill in:' }),
            el('div', { class: 'row', style: 'gap:6px' }, unregistered.map(function (p) {
              return el('button', {
                class: 'pill ghost', style: 'cursor:pointer', text: p.name + ' · ' + p.empId,
                onclick: function () { emp.value = p.empId; name.value = p.name; pw.focus(); }
              });
            }))
          ]) : null
        ]),
        el('div', { style: 'border-top:1px solid var(--line);padding-top:14px' }, [
          el('h3', { style: 'font-size:13px;margin-bottom:8px', text: 'Logins (' + team().length + ')' }),
          el('div', { class: 'scroll', style: 'max-height:340px;overflow-y:auto' }, [
            el('table', {}, [
              el('thead', {}, [el('tr', {}, ['Person', 'Role', 'Manager', 'Teams', 'Status', ''].map(function (t) { return el('th', { text: t }); }))]),
              el('tbody', {}, rows)
            ])
          ])
        ])
      ]);
    }

    function askReset(u) {
      var input = el('input', { type: 'text', value: randomPassword(u.empId, u.name), spellcheck: 'false' });
      var err = el('div', {});
      var box = el('div', { class: 'modal' });
      function shut() { box.remove(); }
      var content = el('div', { class: 'card-b', style: 'display:grid;gap:12px' }, [
        el('div', { class: 'sub', text: 'Sets a one-time password and signs ' + u.name + ' out everywhere. They must choose their own at next sign-in.' }),
        el('label', { class: 'f' }, ['One-time password', input]),
        err
      ]);
      var go = el('button', {
        class: 'btn primary', text: 'Reset password', onclick: function () {
          U.clear(err);
          S.resetPassword(u.empId, input.value).then(function () {
            U.clear(content);
            content.appendChild(el('div', {}, ['Give ', el('b', { text: u.name }), ' this one-time password:']));
            content.appendChild(el('div', { class: 'mono', style: 'font-size:18px;font-weight:700;padding:10px;background:var(--surface-2);border-radius:9px', text: input.value }));
            go.remove();
            draw();
          }, function (e) { err.appendChild(el('div', { class: 'err', text: e.message })); });
        }
      });
      box.appendChild(el('div', { class: 'box' }, [
        el('div', { class: 'card-h' }, [el('h3', { text: 'Reset password — ' + u.name })]),
        content,
        el('div', { class: 'card-h', style: 'border-top:1px solid var(--line);border-bottom:0;justify-content:flex-end' }, [
          el('button', { class: 'btn', text: 'Close', onclick: shut }), go
        ])
      ]));
      box.addEventListener('mousedown', function (e) { if (e.target === box) shut(); });
      document.body.appendChild(box);
      input.select();
    }

    function askTeams(u) {
      var pick = teamChecks(u.teams || []);
      var box = el('div', { class: 'modal' });
      function shut() { box.remove(); }
      box.appendChild(el('div', { class: 'box' }, [
        el('div', { class: 'card-h' }, [el('h3', { text: 'Teams — ' + u.name })]),
        el('div', { class: 'card-b', style: 'display:grid;gap:12px' }, [
          el('div', { class: 'sub', text: u.name + ' will see only people who share one of these teams. All Teams gives access to everyone.' }),
          pick.node
        ]),
        el('div', { class: 'card-h', style: 'border-top:1px solid var(--line);border-bottom:0;justify-content:flex-end' }, [
          el('button', { class: 'btn', text: 'Cancel', onclick: shut }),
          el('button', {
            class: 'btn primary', text: 'Save teams', onclick: function () {
              after(S.updateUser({ empId: u.empId, teams: pick.value() })).then(function (res) {
                if (res) { U.toast('Teams updated for ' + u.name); shut(); }
              });
            }
          })
        ])
      ]));
      box.addEventListener('mousedown', function (e) { if (e.target === box) shut(); });
      document.body.appendChild(box);
    }

    /* ================= Teams ================= */
    function teamsTab() {
      var input = el('input', { type: 'text', placeholder: 'e.g. Data Engineering', maxlength: '40' });
      var err = el('div', {});
      function members(t) { return team().filter(function (u) { return (u.teams || []).indexOf(t) !== -1; }); }
      function line(name, builtIn, note, action) {
        var n = members(name).length;
        return el('div', { class: 'row', style: 'gap:8px;flex-wrap:nowrap;padding:7px 0;border-bottom:1px solid var(--line)' }, [
          el('span', { style: 'font-weight:600;min-width:170px', text: name }),
          builtIn ? el('span', { class: 'pill ghost', text: 'built in' }) : null,
          el('span', { class: 'sub', text: n + (n === 1 ? ' person' : ' people') + (note ? ' · ' + note : '') }),
          el('span', { style: 'flex:1' }),
          action
        ]);
      }
      function add() {
        U.clear(err);
        S.createTeam(input.value).then(function () { U.toast('Team created'); draw(); },
          function (e) { err.appendChild(el('div', { class: 'err', text: e.message })); });
      }
      input.addEventListener('keydown', function (e) { if (e.key === 'Enter') add(); });
      var teams = S.state.teams || [];
      return el('div', { class: 'card-b', style: 'display:grid;gap:16px' }, [
        el('div', {
          class: 'sub', text: 'People see only those who share one of their teams — managers in full, employees as dates ' +
            'without reasons. Someone with no team sees only themselves; admins always see everyone. Assign teams in the Users tab.'
        }),
        el('div', {}, [line(S.ALL_TEAMS, true, 'access to everyone\'s data', null)].concat(teams.map(function (t) {
          return line(t, false, '', el('button', {
            class: 'btn bad sm', text: 'Remove', onclick: function () {
              var n = members(t).length;
              if (!confirm('Remove the team "' + t + '"?' +
                (n ? '\n\n' + n + (n === 1 ? ' person' : ' people') + ' will be taken out of it and may lose access to teammates.' : ''))) return;
              after(S.removeTeam(t)).then(function (res) { if (res) U.toast('Team removed'); });
            }
          }));
        }))),
        teams.length ? null : el('div', { class: 'sub', text: 'No teams yet — create the first one below.' }),
        el('div', { class: 'row', style: 'align-items:flex-end' }, [
          el('label', { class: 'f', style: 'flex:1' }, ['New team name', input]),
          el('button', { class: 'btn primary', text: 'Create team', onclick: add })
        ]),
        err
      ]);
    }

    /* ================= Dropdowns ================= */
    function listsTab() {
      return el('div', { class: 'card-b', style: 'display:grid;gap:18px' }, [
        el('div', { class: 'sub', text: 'These drive every dropdown on the request forms. Removing a value never edits existing records.' }),
        listEditor('leaveReasons', 'Leave reasons', true),
        listEditor('wfhReasons', 'WFH reasons', false),
        listEditor('durations', 'Durations', false),
        el('div', {}, [
          el('h3', { style: 'font-size:13px;margin-bottom:6px' }, ['Statuses ', el('span', { class: 'pill ghost', text: 'fixed' })]),
          el('div', { class: 'row', style: 'gap:5px' }, S.state.validation.statuses.map(function (v) { return el('span', { class: 'pill ' + v, text: v }); })),
          el('div', { class: 'sub', style: 'margin-top:6px', text: 'Not editable — the approval workflow keys off these three values.' })
        ])
      ]);
    }

    function listEditor(key, label, isLeave) {
      var input = el('input', { type: 'text', placeholder: 'New value' });
      var planned = el('input', { type: 'checkbox', checked: true });
      var err = el('div', {});
      var items = (S.state.validation[key] || []).map(function (v) {
        var n = S.optionUsage(key, v);
        return el('div', { class: 'row', style: 'gap:8px;flex-wrap:nowrap;padding:6px 0;border-bottom:1px solid var(--line)' }, [
          el('span', { style: 'font-weight:600;min-width:150px', text: v }),
          el('span', { class: 'sub', style: 'min-width:74px', text: n + (n === 1 ? ' record' : ' records') }),
          isLeave ? el('label', { class: 'row', style: 'gap:6px;font-size:12px;font-weight:600;color:var(--muted);cursor:pointer' }, [
            el('input', {
              type: 'checkbox', checked: S.state.plannedReasons.indexOf(v) !== -1 || null,
              onchange: function (e) { after(S.setPlannedReason(v, e.target.checked)); }
            }), 'counts as planned'
          ]) : null,
          el('span', { style: 'flex:1' }),
          el('button', {
            class: 'btn bad sm', text: 'Remove', onclick: function () {
              if (n && !confirm('"' + v + '" is used by ' + n + ' record(s). They keep the value; it just disappears from the dropdown. Continue?')) return;
              after(S.removeOption(key, v));
            }
          })
        ]);
      });
      return el('div', {}, [
        el('h3', { style: 'font-size:13px;margin-bottom:4px', text: label }),
        el('div', {}, items),
        el('div', { class: 'row', style: 'margin-top:10px;align-items:flex-end' }, [
          el('label', { class: 'f', style: 'flex:1' }, ['Add to ' + label.toLowerCase(), input]),
          isLeave ? el('label', { class: 'row', style: 'gap:6px;font-size:12px;font-weight:600;color:var(--muted);padding-bottom:9px' }, [planned, 'planned']) : null,
          el('button', {
            class: 'btn', text: 'Add', onclick: function () {
              U.clear(err);
              S.addOption(key, input.value, isLeave && planned.checked).then(function () { draw(); },
                function (e) { err.appendChild(el('div', { class: 'err', text: e.message })); });
            }
          })
        ]),
        err
      ]);
    }

    /* ================= Cleanup ================= */
    function cleanTab() {
      var amount = el('input', { type: 'number', min: '1', value: '12', style: 'width:90px' });
      var unit = el('select', {}, ['days', 'weeks', 'months', 'years'].map(function (u) {
        return el('option', { value: u, selected: u === 'months' || null, text: u });
      }));
      var doLeave = el('input', { type: 'checkbox', checked: true });
      var doWfh = el('input', { type: 'checkbox', checked: true });
      var keepPending = el('input', { type: 'checkbox', checked: true });
      var out = el('div', {});
      function opts() { return { leave: doLeave.checked, wfh: doWfh.checked, keepPending: keepPending.checked }; }
      function n() { return Math.max(1, parseInt(amount.value, 10) || 1); }
      function refresh() {
        var cutoff = S.cutoffFor(n(), unit.value), p = S.previewPurge(cutoff, opts());
        U.clear(out);
        out.appendChild(el('div', { class: 'card', style: 'box-shadow:none;background:var(--surface-2)' }, [
          el('div', { class: 'card-b', style: 'display:grid;gap:6px' }, [
            el('div', {}, [el('b', { style: 'font-size:19px', text: String(p.total) }),
              el('span', { class: 'sub', text: ' records would be deleted — ' + p.leave.length + ' leave, ' + p.wfh.length + ' WFH, ' + U.n1(p.days) + ' days' })]),
            el('div', { class: 'sub' }, ['Everything ending before ', el('b', { text: U.fmt(cutoff) }), '.']),
            keepPending.checked ? el('div', { class: 'sub', style: 'color:var(--ok)', text: '✓ Pending requests are protected' })
              : el('div', { class: 'err', text: '⚠ Pending requests will be deleted too' })
          ])
        ]));
      }
      [amount, unit, doLeave, doWfh, keepPending].forEach(function (f) {
        f.addEventListener('change', refresh); f.addEventListener('input', refresh);
      });
      var chk = function (box, text) {
        return el('label', { class: 'row', style: 'gap:7px;font-size:13px;font-weight:600;cursor:pointer' }, [box, text]);
      };
      var panel = el('div', { class: 'card-b', style: 'display:grid;gap:14px' }, [
        el('div', { class: 'sub', text: 'Permanently deletes finished records older than the age you set, for everyone. Export a backup first (Data tab).' }),
        el('div', { class: 'row', style: 'align-items:flex-end' }, [
          el('label', { class: 'f' }, ['Delete records older than', amount]), el('label', { class: 'f' }, ['Unit', unit])
        ]),
        el('div', { class: 'row', style: 'gap:18px' }, [chk(doLeave, '🌴 Leave'), chk(doWfh, '🏠 WFH'), chk(keepPending, 'Keep pending requests')]),
        out,
        el('div', { class: 'row', style: 'justify-content:flex-end' }, [
          el('button', {
            class: 'btn bad', text: 'Delete permanently', onclick: function () {
              var p = S.previewPurge(S.cutoffFor(n(), unit.value), opts());
              if (!p.total) { U.toast('Nothing matches — nothing deleted'); return; }
              if (!confirm('Permanently delete ' + p.total + ' records for everyone?\n\nThis cannot be undone.')) return;
              after(S.purge(n(), unit.value, opts())).then(function (res) {
                if (res) U.toast((res.removed.leave + res.removed.wfh) + ' records deleted');
              });
            }
          })
        ])
      ]);
      refresh();
      return panel;
    }

    /* ================= Data ================= */
    function dataTab() {
      var st = S.state;
      return el('div', { class: 'card-b', style: 'display:grid;gap:14px' }, [
        el('div', { class: 'sub' }, [
          el('b', { text: st.leave.length + ' leave' }), ' and ', el('b', { text: st.wfh.length + ' WFH' }),
          ' records, ', el('b', { text: team().length + ' logins' }), '. Shared by everyone signed in to this server.'
        ]),
        el('div', { class: 'sub mono', style: 'color:var(--faint)', text: 'Build ' + W.app.build + (st.seedGeneratedAt ? ' · sheet import ' + st.seedGeneratedAt : '') }),
        el('div', { class: 'row' }, [
          el('button', {
            class: 'btn primary', text: '⟳ Load latest sheet data', onclick: function () {
              after(S.syncFromSeed()).then(function (res) {
                if (res) U.toast('Sheet data loaded — ' + res.summary.added + ' new, ' + res.summary.updated + ' updated, ' + res.summary.removed + ' removed');
              });
            }
          }),
          el('button', {
            class: 'btn', text: '⬇ Export backup (JSON)', onclick: function () {
              S.exportJSON().then(function (text) { download(text, 'leave-planner-' + U.todayISO() + '.json', 'application/json'); },
                function (e) { U.toast(e.message); });
            }
          })
        ]),
        st.audit.length ? el('div', {}, [
          el('h3', { style: 'font-size:13px;margin-bottom:8px', text: 'Recent activity' }),
          el('div', { style: 'display:grid;gap:4px;max-height:240px;overflow:auto' }, st.audit.slice(0, 50).map(function (a) {
            return el('div', { class: 'sub mono' }, [
              a.at.slice(0, 16).replace('T', ' ') + '  ' + a.action + (a.name ? '  ' + a.name : '') +
              (a.id ? '  ' + a.id : '') + (a.actor ? '  — by ' + a.actor : '')
            ]);
          }))
        ]) : null
      ]);
    }

    overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
    document.body.appendChild(overlay);
    draw();
  }

  W.settings = { open: open };
})(window.WFS);
