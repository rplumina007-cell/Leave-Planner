/* Fake W.api for tools/ui-smoke.html. Mimics server/api.py's shapes and the
   role view rules (redaction, canDecide) closely enough to exercise the UI.
   Uses a local data/seed.json if one has been imported, otherwise generates
   sample records - so it also works on a fresh clone with no data. */
window.WFS = window.WFS || {};
(function (W) {
  'use strict';
  var role = new URLSearchParams(location.search).get('role') || 'employee';
  var ADMIN = { empId: 'A1001', name: 'Sample Admin' };
  var EMP = { empId: 'E1001', name: 'Sample Employee' };
  var ME = role === 'employee' || role === 'mustchange'
    ? { empId: EMP.empId, name: EMP.name, role: 'employee', managerEmpId: ADMIN.empId, mustChange: role === 'mustchange', active: true }
    : { empId: ADMIN.empId, name: ADMIN.name, role: role, managerEmpId: null, mustChange: false, active: true };

  var VALIDATION = {
    leaveReasons: ['Planned Leave', 'Compoff', 'My Day', 'Unplanned'],
    durations: ['Full Day', 'Half Day - 1st Half', 'Half Day - 2nd Half'],
    statuses: ['Pending', 'Approved', 'Declined'],
    wfhReasons: ['Personal Work', 'Not Feeling Well', 'Travel (Out of City)', 'Emergency', 'Other']
  };

  function sampleSeed() {
    var names = ['Avery Lee', 'Jordan Patel', 'Riley Chen', 'Morgan Diaz', 'Casey Kim', 'Taylor Brooks', 'Quinn Rao', 'Skyler Das'];
    var people = names.map(function (n, i) { return { empId: 'E' + (2001 + i), name: n }; }).concat([EMP, ADMIN]);
    var today = new Date(), leave = [], wfh = [], n = 0;
    function day(offset) { var d = new Date(today); d.setDate(d.getDate() + offset); return W.util.iso(d); }
    people.forEach(function (p, i) {
      [-20, -6, 3, 11].forEach(function (off, j) {
        var d = day(off + i);
        if (W.util.isWeekend(W.util.parse(d))) return;
        var rec = { id: 'L' + String(++n).padStart(4, '0'), empId: p.empId, name: p.name, from: d, to: d,
          duration: j === 1 ? 'Half Day - 1st Half' : 'Full Day', days: j === 1 ? 0.5 : 1,
          reason: j % 2 ? 'Unplanned' : 'Planned Leave', status: off > 0 && j === 3 ? 'Pending' : 'Approved', comment: '' };
        if (j === 2) wfh.push(Object.assign({}, rec, { id: 'W' + rec.id.slice(1), reason: 'Personal Work', duration: 'Full Day', days: 1 }));
        else leave.push(rec);
      });
    });
    return { people: people, validation: VALIDATION, leave: leave, wfh: wfh, generatedAt: W.util.todayISO() };
  }

  var seed = null, team = null;
  function load() {
    if (seed) return Promise.resolve();
    return fetch('../data/seed.json').then(function (r) {
      if (!r.ok) throw new Error('no seed');
      return r.json();
    }).catch(sampleSeed).then(function (s) {
      seed = s;
      var people = s.people.slice();
      [ADMIN, EMP].forEach(function (p) {
        if (!people.some(function (x) { return x.empId === p.empId; })) people.push(p);
      });
      team = people.map(function (p) {
        var isAdmin = p.empId === ADMIN.empId;
        return { empId: p.empId, name: p.name, role: isAdmin ? (role === 'manager' ? 'manager' : 'admin') : 'employee',
          managerEmpId: isAdmin ? null : ADMIN.empId, mustChange: false, active: true };
      });
      /* one person with a pending request is assigned to someone else, so a
         manager's queue is visibly filtered */
      var pend = s.leave.concat(s.wfh).filter(function (r) { return r.status === 'Pending' && r.empId !== ME.empId; })[0];
      if (pend) team.forEach(function (u) { if (u.empId === pend.empId) u.managerEmpId = 'X0000'; });
    });
  }

  function owner(emp) { return team.filter(function (u) { return u.empId === emp; })[0]; }
  function canDecide(r) {
    if (ME.role === 'admin') return true;
    if (ME.role !== 'manager' || r.empId === ME.empId) return false;
    var o = owner(r.empId);
    return !!(o && o.managerEmpId === ME.empId);
  }

  function view() {
    var st = {
      validation: seed.validation, plannedReasons: ['Planned Leave', 'Compoff', 'My Day'],
      seedGeneratedAt: seed.generatedAt, people: team.map(function (u) { return { empId: u.empId, name: u.name }; }),
      me: ME
    };
    ['leave', 'wfh'].forEach(function (k) {
      st[k] = seed[k].map(function (r) {
        if (ME.role === 'employee' && r.empId !== ME.empId) {
          return { id: r.id, empId: r.empId, name: r.name, from: r.from, to: r.to, duration: r.duration,
            days: r.days, status: r.status, reason: null, comment: '', redacted: true };
        }
        return Object.assign({}, r, { canDecide: canDecide(r) });
      });
    });
    if (ME.role !== 'employee') st.team = team;
    if (ME.role === 'admin') { st.audit = []; st.seedIsNewer = false; st.unregistered = []; }
    return st;
  }

  function fail(status, payload) {
    var e = new Error(payload.error || 'error'); e.status = status; e.payload = payload;
    return Promise.reject(e);
  }

  W.api = {
    get: function (path) {
      return load().then(function () {
        if (path === '/api/me') return { user: ME };
        if (ME.mustChange) return fail(403, { error: 'Please set a new password first', mustChange: true });
        return view();
      });
    },
    post: function (path, body) {
      return load().then(function () {
        if (path === '/api/requests/decide') {
          ['leave', 'wfh'].forEach(function (k) {
            seed[k].forEach(function (r) { if (r.id === body.id && k === body.kind) r.status = body.status; });
          });
        }
        if (path === '/api/me/password') { ME.mustChange = false; return { ok: true }; }
        if (path === '/api/logout') return { ok: true };
        return { state: view() };
      });
    }
  };
})(window.WFS);
