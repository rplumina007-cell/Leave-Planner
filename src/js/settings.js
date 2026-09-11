/* Admin settings page (#/settings, opened from the profile menu):
   Users, Teams, Public holidays, Dropdowns, Cleanup, Data.
   Only admins get the page; the server refuses these calls from anyone else.
   Every saved change re-renders the page from the server's fresh state, so what
   you see is always what is stored. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, el = U.el, I = W.icon;
  var ROLES = [['employee', 'Employee'], ['manager', 'Manager'], ['admin', 'Admin']];
  var TABS = [['users', 'Users', 'users'], ['teams', 'Teams', 'team'], ['holidays', 'Public holidays', 'holiday'],
    ['lists', 'Dropdowns', 'sliders'], ['clean', 'Cleanup', 'broom'], ['data', 'Data', 'database']];
  var NO_TEAM = '__none__';

  var view = { tab: 'users', team: 'all', q: '', picked: {} };

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
  function errBox(box, msg) { U.clear(box); box.appendChild(el('div', { class: 'err', text: msg })); }

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

  function render() {
    var nav = el('div', { class: 'card snav', role: 'tablist', 'aria-label': 'Settings sections' }, TABS.map(function (t) {
      return el('button', {
        type: 'button', role: 'tab', class: view.tab === t[0] ? 'on' : '', 'aria-selected': view.tab === t[0] ? 'true' : 'false',
        onclick: function () { view.tab = t[0]; W.app.rerender(); }
      }, [I(t[2]), t[1]]);
    }));
    var body = view.tab === 'users' ? usersTab() : view.tab === 'teams' ? teamsTab() : view.tab === 'holidays' ? holidaysTab()
      : view.tab === 'lists' ? listsTab() : view.tab === 'clean' ? cleanTab() : dataTab();
    return el('div', {}, [
      el('div', { class: 'section-title', style: 'margin-top:0' }, [el('h2', { text: 'Settings' }), el('span', { class: 'rule' })]),
      el('div', { class: 'settings' }, [nav, el('div', { class: 'card', role: 'tabpanel' }, [body])])
    ]);
  }

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

    function create() {
      U.clear(err);
      var body = {
        empId: emp.value.trim(), name: name.value.trim(), password: pw.value, role: role.value,
        managerEmpId: mgr.value || null, teams: teamPick.value()
      };
      if (!body.empId || !body.name || !body.password) {
        errBox(err, 'Employee ID, full name and password are all required.');
        return;
      }
      S.createUser(body).then(function () { U.toast('Login created for ' + body.name); },
        function (e) { errBox(err, e.message); });
    }

    var unregistered = S.state.unregistered || [];
    var teams = S.state.teams || [];
    function inFilter(u) {
      var t = u.teams || [];
      return view.team === 'all' ? true : view.team === NO_TEAM ? !t.length : t.indexOf(view.team) !== -1;
    }
    var shownUsers = team().filter(inFilter);
    Object.keys(view.picked).forEach(function (k) {
      if (!shownUsers.some(function (u) { return u.empId === k; })) delete view.picked[k];
    });

    /* ---- bulk team changes for the ticked people ---- */
    var bulkTeam = el('select', { 'aria-label': 'Team' }, [S.ALL_TEAMS].concat(teams).map(function (t) {
      return el('option', { value: t, text: t });
    }));
    var bulkCount = el('b', {});
    var bulkbar = el('div', { class: 'bulkbar' }, [
      bulkCount,
      el('span', { style: 'flex:1' }),
      bulkTeam,
      el('button', { class: 'btn sm', type: 'button', onclick: function () { bulkTeams('add', bulkTeam.value); } }, [I('plus'), 'Add to team']),
      el('button', { class: 'btn sm', type: 'button', onclick: function () { bulkTeams('remove', bulkTeam.value); } }, [I('x'), 'Remove from team']),
      el('button', { class: 'btn sm', type: 'button', text: 'Clear', onclick: function () { view.picked = {}; W.app.rerender(); } })
    ]);
    function pickedIds() { return Object.keys(view.picked).filter(function (k) { return view.picked[k]; }); }
    function syncBar() {
      var n = pickedIds().length;
      bulkbar.hidden = !n;
      bulkCount.textContent = n + (n === 1 ? ' person selected' : ' people selected');
    }
    function bulkTeams(mode, teamName) {
      var byId = {};
      team().forEach(function (u) { byId[u.empId] = u; });
      var todo = pickedIds().map(function (id) { return byId[id]; }).filter(Boolean).map(function (u) {
        var cur = (u.teams || []).slice(), next;
        if (mode === 'add') {
          next = teamName === S.ALL_TEAMS ? [S.ALL_TEAMS]
            : cur.indexOf(S.ALL_TEAMS) !== -1 || cur.indexOf(teamName) !== -1 ? cur : cur.concat([teamName]);
        } else {
          next = cur.filter(function (t) { return t !== teamName; });
        }
        return { u: u, next: next, changed: next.join('|') !== cur.join('|') };
      }).filter(function (x) { return x.changed; });
      if (!todo.length) { U.toast('Nothing to change'); return; }
      view.picked = {};
      todo.reduce(function (chain, x) {
        return chain.then(function (n) {
          return S.updateUser({ empId: x.u.empId, teams: x.next }).then(function (res) { return n + (res ? 1 : 0); });
        });
      }, Promise.resolve(0)).then(function (n) {
        U.toast(n + (n === 1 ? ' person ' : ' people ') + (mode === 'add' ? 'added to ' : 'removed from ') + teamName);
      });
    }

    var rows = shownUsers.map(function (u) {
      var self = u.empId === S.me().empId;
      var reports = team().filter(function (x) { return x.managerEmpId === u.empId; }).length;
      var roleSel = el('select', {
        disabled: self || null, title: self ? 'You cannot change your own role' : null, 'aria-label': 'Role for ' + u.name,
        onchange: function (e) {
          var msg = 'Change ' + u.name + ' to ' + e.target.value + '? They will be signed out.';
          if (u.role !== 'employee' && e.target.value === 'employee' && reports) {
            msg += '\n\nTheir ' + reports + ' assigned report(s) will be left without a manager.';
          }
          if (!confirm(msg)) { e.target.value = u.role; return; }
          S.updateUser({ empId: u.empId, role: e.target.value }).then(function (res) {
            if (res) U.toast(u.name + ' is now ' + e.target.value + (res.unassigned ? ' — ' + res.unassigned + ' report(s) unassigned' : ''));
          });
        }
      }, ROLES.map(function (r) { return el('option', { value: r[0], selected: u.role === r[0] || null, text: r[1] }); }));
      var mgrSel = el('select', {
        'aria-label': 'Manager for ' + u.name,
        onchange: function (e) {
          S.updateUser({ empId: u.empId, managerEmpId: e.target.value || null }).then(function (res) {
            if (res) U.toast(u.name + (e.target.value ? ' assigned to a new manager' : ' has no manager now'));
          });
        }
      }, [el('option', { value: '', text: '— none —' })].concat(managers(u.empId).map(function (m) {
        return el('option', { value: m.empId, selected: u.managerEmpId === m.empId || null, text: m.name });
      })));

      var tr = el('tr', { style: u.active ? null : 'opacity:.6', data: { search: (u.name + ' ' + u.empId).toLowerCase() } }, [
        el('td', { style: 'width:30px' }, [el('input', {
          type: 'checkbox', checked: view.picked[u.empId] || null, 'aria-label': 'Select ' + u.name,
          onchange: function (e) { view.picked[u.empId] = e.target.checked; syncBar(); }
        })]),
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
          el('button', { class: 'btn sm', type: 'button', style: 'margin-top:5px', text: 'Edit teams', onclick: function () { askTeams(u); } })
        ]),
        el('td', {}, [
          el('span', { class: 'pill ' + (u.active ? 'Approved' : 'Declined'), text: u.active ? 'Active' : 'Inactive' }),
          u.mustChange ? el('div', { class: 'sub', style: 'font-size:11px;margin-top:2px', text: 'awaiting first sign-in' }) : null
        ]),
        el('td', { style: 'text-align:right;white-space:nowrap' }, [
          el('button', { class: 'btn sm', type: 'button', text: 'Reset password', onclick: function () { askReset(u); } }), ' ',
          self ? null : el('button', {
            class: 'btn sm', type: 'button', text: u.active ? 'Deactivate' : 'Activate', onclick: function () {
              if (u.active && !confirm('Deactivate ' + u.name + '? They will be signed out and unable to sign in.')) return;
              S.updateUser({ empId: u.empId, active: !u.active });
            }
          }), ' ',
          self ? null : el('button', {
            class: 'btn bad sm', type: 'button', text: 'Delete', onclick: function () {
              if (!confirm('Delete the login for ' + u.name + '?\n\nTheir leave and WFH records are kept.')) return;
              S.deleteUser(u.empId).then(function (res) { if (res) U.toast('Login deleted'); });
            }
          })
        ])
      ]);
      return tr;
    });

    /* search filters the rows in place, so typing never loses focus */
    var countNote = el('span', { class: 'sub' });
    function applySearch() {
      var q = view.q.trim().toLowerCase(), n = 0;
      rows.forEach(function (tr) {
        var hit = !q || tr.dataset.search.indexOf(q) !== -1;
        tr.hidden = !hit;
        if (hit) n++;
      });
      countNote.textContent = n + ' of ' + team().length + ' logins';
    }
    var search = el('input', {
      type: 'search', placeholder: 'Search name or ID', value: view.q, 'aria-label': 'Search logins',
      oninput: function (e) { view.q = e.target.value; applySearch(); }
    });
    var teamFilter = el('select', {
      'aria-label': 'Filter by team', onchange: function (e) { view.team = e.target.value; W.app.rerender(); }
    }, [['all', 'All teams'], [S.ALL_TEAMS, 'In All Teams']].concat(teams.map(function (t) { return [t, t]; }), [[NO_TEAM, 'No team']])
      .map(function (o) { return el('option', { value: o[0], selected: view.team === o[0] || null, text: o[1] }); }));
    var selectAll = el('input', {
      type: 'checkbox', title: 'Select everyone shown', 'aria-label': 'Select everyone shown',
      onchange: function (e) {
        rows.forEach(function (tr, i) {
          if (tr.hidden) return;
          view.picked[shownUsers[i].empId] = e.target.checked;
          tr.querySelector('input[type=checkbox]').checked = e.target.checked;
        });
        syncBar();
      }
    });

    var panel = el('div', { class: 'card-b', style: 'display:grid;gap:16px' }, [
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
          el('button', { class: 'btn primary', type: 'button', onclick: create }, [I('plus'), 'Create login'])
        ]),
        err,
        unregistered.length ? el('div', { style: 'margin-top:10px' }, [
          el('div', { class: 'sub', style: 'font-size:12px;margin-bottom:6px', text: 'People in the records without a login — click to fill in:' }),
          el('div', { class: 'row', style: 'gap:6px' }, unregistered.map(function (p) {
            return el('button', {
              class: 'pill ghost', type: 'button', style: 'cursor:pointer', text: p.name + ' · ' + p.empId,
              onclick: function () { emp.value = p.empId; name.value = p.name; pw.focus(); }
            });
          }))
        ]) : null
      ]),
      el('div', { style: 'border-top:1px solid var(--line);padding-top:14px;display:grid;gap:10px' }, [
        el('div', { class: 'row', style: 'gap:10px' }, [
          el('h3', { style: 'font-size:13px', text: 'Logins (' + team().length + ')' }),
          countNote,
          el('span', { style: 'flex:1' }),
          el('label', { class: 'searchbox' }, [I('search'), search]),
          teamFilter
        ]),
        bulkbar,
        el('div', { class: 'scroll', style: 'max-height:520px;overflow-y:auto' }, [
          el('table', {}, [
            el('thead', {}, [el('tr', {}, [el('th', {}, [selectAll])].concat(['Person', 'Role', 'Manager', 'Teams', 'Status', ''].map(function (t) { return el('th', { text: t }); })))]),
            el('tbody', {}, rows)
          ])
        ]),
        shownUsers.length ? null : el('div', { class: 'empty', text: 'No logins in this team.' })
      ])
    ]);
    applySearch();
    syncBar();
    return panel;
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
      class: 'btn primary', type: 'button', text: 'Reset password', onclick: function () {
        U.clear(err);
        S.resetPassword(u.empId, input.value).then(function () {
          U.clear(content);
          content.appendChild(el('div', {}, ['Give ', el('b', { text: u.name }), ' this one-time password:']));
          content.appendChild(el('div', { class: 'mono', style: 'font-size:18px;font-weight:700;padding:10px;background:var(--surface-2);border-radius:var(--r-xs)', text: input.value }));
          go.remove();
        }, function (e) { errBox(err, e.message); });
      }
    });
    box.appendChild(el('div', { class: 'box' }, [
      el('div', { class: 'card-h' }, [el('h3', { text: 'Reset password — ' + u.name })]),
      content,
      el('div', { class: 'card-h', style: 'border-top:1px solid var(--line);border-bottom:0;justify-content:flex-end' }, [
        el('button', { class: 'btn', type: 'button', text: 'Close', onclick: shut }), go
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
        el('button', { class: 'btn', type: 'button', text: 'Cancel', onclick: shut }),
        el('button', {
          class: 'btn primary', type: 'button', text: 'Save teams', onclick: function () {
            S.updateUser({ empId: u.empId, teams: pick.value() }).then(function (res) {
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
      S.createTeam(input.value).then(function () { U.toast('Team created'); }, function (e) { errBox(err, e.message); });
    }
    input.addEventListener('keydown', function (e) { if (e.key === 'Enter') add(); });
    var teams = S.state.teams || [];
    return el('div', { class: 'card-b', style: 'display:grid;gap:16px' }, [
      el('div', {
        class: 'sub', text: 'People see only those who share one of their teams — managers in full, employees as dates ' +
          'without reasons. Someone with no team sees only themselves; admins always see everyone. Assign teams in Users.'
      }),
      el('div', {}, [line(S.ALL_TEAMS, true, 'access to everyone\'s data', null)].concat(teams.map(function (t) {
        return line(t, false, '', el('button', {
          class: 'btn bad sm', type: 'button', text: 'Remove', onclick: function () {
            var n = members(t).length;
            if (!confirm('Remove the team "' + t + '"?' +
              (n ? '\n\n' + n + (n === 1 ? ' person' : ' people') + ' will be taken out of it and may lose access to teammates.' : ''))) return;
            S.removeTeam(t).then(function (res) { if (res) U.toast('Team removed'); });
          }
        }));
      }))),
      teams.length ? null : el('div', { class: 'sub', text: 'No teams yet — create the first one below.' }),
      el('div', { class: 'row', style: 'align-items:flex-end' }, [
        el('label', { class: 'f', style: 'flex:1' }, ['New team name', input]),
        el('button', { class: 'btn primary', type: 'button', onclick: add }, [I('plus'), 'Create team'])
      ]),
      err
    ]);
  }

  /* ================= Public holidays ================= */
  function holidaysTab() {
    var date = el('input', { type: 'date' });
    var name = el('input', { type: 'text', placeholder: 'e.g. New Year’s Day', maxlength: '60' });
    var err = el('div', {});
    var today = U.todayISO();
    var list = S.state.holidays || [];
    var upcoming = list.filter(function (h) { return h.date >= today; });
    var past = list.filter(function (h) { return h.date < today; }).reverse();

    function row(h) {
      var d = U.parse(h.date);
      return el('div', { class: 'row', style: 'gap:10px;flex-wrap:nowrap;padding:8px 0;border-bottom:1px solid var(--line)' }, [
        el('span', { class: 'mono', style: 'min-width:112px', text: U.fmt(h.date) }),
        el('span', { class: 'sub', style: 'min-width:34px', text: U.DOW[d.getDay()] }),
        el('span', { style: 'font-weight:600;flex:1;min-width:0', text: h.name }),
        U.isWeekend(d) ? el('span', { class: 'pill ghost', title: 'Weekends already don’t count', text: 'on a weekend' }) : null,
        el('button', {
          class: 'btn bad sm', type: 'button', text: 'Remove', onclick: function () {
            if (!confirm('Remove ' + h.name + ' (' + U.fmt(h.date) + ')?')) return;
            S.removeHoliday(h.date).then(function (res) { if (res) U.toast('Holiday removed'); });
          }
        })
      ]);
    }
    function add() {
      U.clear(err);
      if (!date.value) { errBox(err, 'Pick a date.'); return; }
      S.addHoliday(date.value, name.value).then(function () { U.toast('Holiday added'); }, function (e) { errBox(err, e.message); });
    }
    name.addEventListener('keydown', function (e) { if (e.key === 'Enter') add(); });

    return el('div', { class: 'card-b', style: 'display:grid;gap:16px' }, [
      el('div', {
        class: 'sub', text: 'Public holidays are shaded on the calendar and don’t count as working days — in new requests, ' +
          'day counts and dashboard figures. Requests already recorded keep the day counts they were made with.'
      }),
      el('div', { class: 'row', style: 'align-items:flex-end' }, [
        el('label', { class: 'f' }, ['Date', date]),
        el('label', { class: 'f', style: 'flex:1;min-width:200px' }, ['Name', name]),
        el('button', { class: 'btn primary', type: 'button', onclick: add }, [I('plus'), 'Add holiday'])
      ]),
      err,
      el('div', {}, [
        el('h3', { style: 'font-size:13px;margin-bottom:4px', text: 'Upcoming (' + upcoming.length + ')' }),
        upcoming.length ? el('div', {}, upcoming.map(row)) : el('div', { class: 'sub', text: 'None yet.' })
      ]),
      past.length ? el('div', {}, [
        el('h3', { style: 'font-size:13px;margin-bottom:4px', text: 'Past (' + past.length + ')' }),
        el('div', {}, past.slice(0, 24).map(row))
      ]) : null
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
            onchange: function (e) { S.setPlannedReason(v, e.target.checked); }
          }), 'counts as planned'
        ]) : null,
        el('span', { style: 'flex:1' }),
        el('button', {
          class: 'btn bad sm', type: 'button', text: 'Remove', onclick: function () {
            if (n && !confirm('"' + v + '" is used by ' + n + ' record(s). They keep the value; it just disappears from the dropdown. Continue?')) return;
            S.removeOption(key, v);
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
          class: 'btn', type: 'button', text: 'Add', onclick: function () {
            U.clear(err);
            S.addOption(key, input.value, isLeave && planned.checked).then(function () { }, function (e) { errBox(err, e.message); });
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
          keepPending.checked
            ? el('div', { class: 'sub', style: 'color:var(--ok);display:flex;gap:6px;align-items:center' }, [I('check'), 'Pending requests are protected'])
            : el('div', { class: 'err', style: 'display:flex;gap:6px;align-items:center' }, [I('alert'), 'Pending requests will be deleted too'])
        ])
      ]));
    }
    [amount, unit, doLeave, doWfh, keepPending].forEach(function (f) {
      f.addEventListener('change', refresh); f.addEventListener('input', refresh);
    });
    var chk = function (box, kids) {
      return el('label', { class: 'row', style: 'gap:7px;font-size:13px;font-weight:600;cursor:pointer' }, [box].concat(kids));
    };
    var panel = el('div', { class: 'card-b', style: 'display:grid;gap:14px' }, [
      el('div', { class: 'sub', text: 'Permanently deletes finished records older than the age you set, for everyone. Export a backup first (Data).' }),
      el('div', { class: 'row', style: 'align-items:flex-end' }, [
        el('label', { class: 'f' }, ['Delete records older than', amount]), el('label', { class: 'f' }, ['Unit', unit])
      ]),
      el('div', { class: 'row', style: 'gap:18px' }, [
        chk(doLeave, [I('leave'), 'Leave']), chk(doWfh, [I('home'), 'WFH']), chk(keepPending, ['Keep pending requests'])
      ]),
      out,
      el('div', { class: 'row', style: 'justify-content:flex-end' }, [
        el('button', {
          class: 'btn bad', type: 'button', text: 'Delete permanently', onclick: function () {
            var p = S.previewPurge(S.cutoffFor(n(), unit.value), opts());
            if (!p.total) { U.toast('Nothing matches — nothing deleted'); return; }
            if (!confirm('Permanently delete ' + p.total + ' records for everyone?\n\nThis cannot be undone.')) return;
            S.purge(n(), unit.value, opts()).then(function (res) {
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
          class: 'btn primary', type: 'button', onclick: function () {
            S.syncFromSeed().then(function (res) {
              if (res) U.toast('Sheet data loaded — ' + res.summary.added + ' new, ' + res.summary.updated + ' updated, ' + res.summary.removed + ' removed');
            });
          }
        }, [I('refresh'), 'Load latest sheet data']),
        el('button', {
          class: 'btn', type: 'button', onclick: function () {
            S.exportJSON().then(function (text) { download(text, 'leave-planner-' + U.todayISO() + '.json', 'application/json'); },
              function (e) { U.toast(e.message); });
          }
        }, [I('download'), 'Export backup (JSON)'])
      ]),
      st.audit.length ? el('div', {}, [
        el('h3', { style: 'font-size:13px;margin-bottom:8px', text: 'Recent activity' }),
        el('div', { style: 'display:grid;gap:4px;max-height:320px;overflow:auto' }, st.audit.slice(0, 80).map(function (a) {
          return el('div', { class: 'sub mono' }, [
            a.at.slice(0, 16).replace('T', ' ') + '  ' + a.action + (a.name ? '  ' + a.name : '') +
            (a.id ? '  ' + a.id : '') + (a.actor ? '  — by ' + a.actor : '')
          ]);
        }))
      ]) : null
    ]);
  }

  W.pages = W.pages || {};
  W.pages.settings = { id: 'settings', label: 'Settings', nav: false, render: render };
  W.settings = { open: function () { location.hash = '#/settings'; } };
})(window.WFS);
