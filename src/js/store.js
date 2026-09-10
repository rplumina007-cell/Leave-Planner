/* Client-side view of the shared tracker state.

   The server owns the data and every rule (server/api.py). This module loads the
   slice the signed-in user may see, answers read-only questions about it for the
   UI (day maths, calendar index, previews), and forwards every change to the
   server - replacing its copy with the fresh state the server returns. */
(function (W) {
  'use strict';
  var U = W.util;

  var subs = [];
  var state = null;

  function install(next) {
    next.audit = next.audit || [];
    next.plannedReasons = next.plannedReasons || PLANNED_REASONS.slice();
    next.people = next.people || [];
    state = next;
    subs.forEach(function (fn) { fn(state); });
    return state;
  }

  function init() { return W.api.get('/api/state').then(install); }
  function reload() { return init(); }

  /* Send a change; on success adopt the returned state. Errors are toasted and
     resolve to null, unless opts.raise asks for them (forms show them inline). */
  function call(path, body, opts) {
    return W.api.post(path, body).then(function (res) {
      if (res && res.state) install(res.state);
      return res;
    }, function (e) {
      if (opts && opts.raise) throw e;
      U.toast(e.message);
      return null;
    });
  }

  /* ---------- who is signed in ---------- */
  function me() { return state.me; }
  function role() { return state.me.role; }
  function is() {
    var r = role();
    return [].slice.call(arguments).indexOf(r) !== -1;
  }
  var ALL_TEAMS = 'All Teams';
  /* Admins and anyone in All Teams see everyone; everyone else only people sharing a team. */
  function seesEveryone() { return is('admin') || (state.me.teams || []).indexOf(ALL_TEAMS) !== -1; }
  /* Employee IDs (as keys) of people explicitly in a team - admin view, which has
     everyone's teams. NO_TEAM: people with no team, including those with no login.
     All Teams members only count under "all teams", so they don't skew team figures. */
  var NO_TEAM = '__no_team__';
  function teamMemberIds(teamName) {
    var teamsOf = {}, ids = {};
    (state.team || []).forEach(function (u) { teamsOf[u.empId] = u.teams || []; });
    state.people.forEach(function (p) {
      var t = teamsOf[p.empId] || [];
      if (teamName === NO_TEAM ? !t.length : t.indexOf(teamName) !== -1) ids[p.empId] = true;
    });
    return ids;
  }

  /* ---------- domain rules (display only; the server re-checks) ---------- */
  var PLANNED_REASONS = ['Planned Leave', 'Compoff', 'My Day'];

  function isPlanned(rec) { return state.plannedReasons.indexOf(rec.reason) !== -1; }
  function isHalf(rec) { return String(rec.duration).indexOf('Half') === 0; }

  /* Two families, two weights: planned reads ochre, unplanned reads wine, the
     lighter tint is the half day. Never teal - that means WFH. */
  var BUCKETS = [
    { id: 'planned', label: 'Planned Leave', short: 'Planned', color: 'var(--bk-p)' },
    { id: 'unplanned', label: 'Unplanned Leave', short: 'Unplanned', color: 'var(--bk-u)' },
    { id: 'plannedHalf', label: 'Planned Half Day', short: 'Planned ½', color: 'var(--bk-ph)' },
    { id: 'unplannedHalf', label: 'Unplanned Half Day', short: 'Unplanned ½', color: 'var(--bk-uh)' }
  ];
  function bucketOf(rec) {
    var p = isPlanned(rec), h = isHalf(rec);
    return p ? (h ? 'plannedHalf' : 'planned') : (h ? 'unplannedHalf' : 'unplanned');
  }

  function expand(rec) {
    if (isHalf(rec)) {
      var d = U.parse(rec.from);
      return U.isWeekend(d) ? [] : [{ date: rec.from, fraction: 0.5 }];
    }
    return U.workingDaysBetween(rec.from, rec.to || rec.from).map(function (day) {
      return { date: day, fraction: 1 };
    });
  }
  function computeDays(from, to, duration) {
    if (String(duration).indexOf('Half') === 0) return 0.5;
    return U.workingDaysBetween(from, to || from).length;
  }

  /* ---------- queries ---------- */
  function all(kind) { return state[kind].slice(); }
  /* Records this user has full access to - everyone for an admin, a manager's own
     team, an employee's own. The rest arrive redacted and belong on the calendar only. */
  function visible(kind) { return state[kind].filter(function (r) { return !r.redacted; }); }
  /* People this user has full access to, and people they may file requests for. */
  function teamPeople() { return state.people.filter(function (p) { return p.full; }); }
  function applyPeople() { return state.people.filter(function (p) { return p.canApply; }); }
  function both() {
    return state.leave.map(tagKind('leave')).concat(state.wfh.map(tagKind('wfh')));
  }
  function tagKind(kind) { return function (r) { var c = Object.create(r); c.kind = kind; return c; }; }

  function byDate(filter) {
    var map = {};
    ['leave', 'wfh'].forEach(function (kind) {
      state[kind].forEach(function (rec) {
        if (filter && !filter(rec, kind)) return;
        expand(rec).forEach(function (d) {
          (map[d.date] = map[d.date] || []).push({ rec: rec, kind: kind, fraction: d.fraction });
        });
      });
    });
    return map;
  }

  function pending(kind) {
    return state[kind].filter(function (r) { return r.status === 'Pending'; })
      .sort(function (a, b) { return a.from < b.from ? -1 : 1; });
  }
  function decided(kind) {
    return state[kind].filter(function (r) { return r.status !== 'Pending'; })
      .sort(function (a, b) { return a.from < b.from ? 1 : -1; });
  }
  /* Pending requests this user is allowed to decide - the server's own verdict. */
  function actionable(kind) {
    return pending(kind).filter(function (r) { return r.canDecide; });
  }

  function clashes(rec) {
    var days = {}; expand(rec).forEach(function (d) { days[d.date] = 1; });
    var hits = [];
    both().forEach(function (other) {
      if (other.id === rec.id || other.status === 'Declined' || other.name === rec.name) return;
      var overlap = expand(other).filter(function (d) { return days[d.date]; });
      if (overlap.length) hits.push({ rec: other, kind: other.kind, days: overlap.length });
    });
    return hits;
  }
  function selfConflict(rec) {
    var days = {}; expand(rec).forEach(function (d) { days[d.date] = 1; });
    return both().filter(function (o) {
      return o.id !== rec.id && o.name === rec.name && o.status !== 'Declined' &&
        expand(o).some(function (d) { return days[d.date]; });
    });
  }

  function usageOf(name) {
    return {
      leave: state.leave.filter(function (r) { return r.name === name; }).length,
      wfh: state.wfh.filter(function (r) { return r.name === name; }).length
    };
  }

  var EDITABLE_LISTS = ['leaveReasons', 'wfhReasons', 'durations'];
  function optionUsage(listKey, value) {
    var field = listKey === 'durations' ? 'duration' : 'reason';
    var sets = listKey === 'wfhReasons' ? ['wfh'] : listKey === 'leaveReasons' ? ['leave'] : ['leave', 'wfh'];
    return sets.reduce(function (n, k) {
      return n + state[k].filter(function (r) { return r[field] === value; }).length;
    }, 0);
  }

  /* Preview only - mirrors domain.cutoff_for / purge on the server. */
  var UNITS = { days: 1, weeks: 7, months: 30, years: 365 };
  function cutoffFor(amount, unit) {
    var d = new Date();
    if (unit === 'months') d.setMonth(d.getMonth() - amount);
    else if (unit === 'years') d.setFullYear(d.getFullYear() - amount);
    else d = U.addDays(d, -amount * (UNITS[unit] || 1));
    return U.iso(d);
  }
  function previewPurge(cutoff, opts) {
    var out = { cutoff: cutoff, leave: [], wfh: [], days: 0 };
    ['leave', 'wfh'].forEach(function (kind) {
      if (!opts[kind]) return;
      out[kind] = state[kind].filter(function (r) {
        return (r.to || r.from) < cutoff && !(opts.keepPending && r.status === 'Pending');
      });
      out.days += U.sum(out[kind], function (r) { return r.days; });
    });
    out.total = out.leave.length + out.wfh.length;
    return out;
  }

  /* ---------- changes (all go through the server) ---------- */
  function submit(kind, rec) {
    return call('/api/requests', {
      kind: kind, forEmpId: rec.empId, from: rec.from, to: rec.to,
      duration: rec.duration, reason: rec.reason, comment: rec.comment
    }, { raise: true });
  }
  function decide(kind, id, status, note) {
    return call('/api/requests/decide', { kind: kind, id: id, status: status, note: note || '' });
  }
  function withdraw(kind, id) { return call('/api/requests/delete', { kind: kind, id: id }); }

  function addOption(key, value, planned) {
    return call('/api/options/add', { key: key, value: value, planned: !!planned }, { raise: true });
  }
  function removeOption(key, value) { return call('/api/options/remove', { key: key, value: value }); }
  function setPlannedReason(reason, planned) {
    return call('/api/options/planned', { reason: reason, planned: planned });
  }
  function purge(amount, unit, opts) {
    return call('/api/purge', { amount: amount, unit: unit, leave: opts.leave, wfh: opts.wfh, keepPending: opts.keepPending });
  }
  function syncFromSeed() { return call('/api/sync', {}); }

  function createUser(u) { return call('/api/users', u, { raise: true }); }
  function updateUser(u) { return call('/api/users/update', u); }
  function resetPassword(empId, password) {
    return call('/api/users/reset-password', { empId: empId, password: password }, { raise: true });
  }
  function deleteUser(empId) { return call('/api/users/delete', { empId: empId }); }
  function createTeam(name) { return call('/api/teams/add', { name: name }, { raise: true }); }
  function removeTeam(name) { return call('/api/teams/remove', { name: name }); }

  function changePassword(current, next) {
    return W.api.post('/api/me/password', { current: current, next: next });
  }
  function logout() {
    return W.api.post('/api/logout', {}).catch(function () { }).then(function () {
      location.replace('/login.html');
    });
  }
  function exportJSON() {
    return W.api.get('/api/export').then(function (data) { return JSON.stringify(data, null, 1); });
  }

  W.store = {
    init: init, reload: reload,
    get state() { return state; },
    subscribe: function (fn) { subs.push(fn); },
    me: me, role: role, is: is, seesEveryone: seesEveryone, ALL_TEAMS: ALL_TEAMS,
    NO_TEAM: NO_TEAM, teamMemberIds: teamMemberIds,
    BUCKETS: BUCKETS, PLANNED_REASONS: PLANNED_REASONS, EDITABLE_LISTS: EDITABLE_LISTS, UNITS: UNITS,
    isPlanned: isPlanned, isHalf: isHalf, bucketOf: bucketOf, expand: expand, computeDays: computeDays,
    all: all, visible: visible, teamPeople: teamPeople, applyPeople: applyPeople, both: both, byDate: byDate, pending: pending, decided: decided, actionable: actionable,
    clashes: clashes, selfConflict: selfConflict, usageOf: usageOf, optionUsage: optionUsage,
    cutoffFor: cutoffFor, previewPurge: previewPurge,
    seedIsNewer: function () { return !!state.seedIsNewer; },
    seedInfo: function () { return { generatedAt: state.seedGeneratedAt || '' }; },
    submit: submit, decide: decide, withdraw: withdraw,
    addOption: addOption, removeOption: removeOption, setPlannedReason: setPlannedReason,
    purge: purge, syncFromSeed: syncFromSeed,
    createUser: createUser, updateUser: updateUser, resetPassword: resetPassword, deleteUser: deleteUser,
    createTeam: createTeam, removeTeam: removeTeam,
    changePassword: changePassword, logout: logout, exportJSON: exportJSON
  };
})(window.WFS);
