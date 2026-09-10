/* All dashboard maths in one place. Pure functions over store state, so adding a
   new insight means adding a field here and a card in pages/insights.js. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store;

  function inScope(rec, o) {
    if (o.person && o.person !== 'all' && rec.name !== o.person) return false;
    if (rec.status === 'Declined') return false;
    if (!o.includePending && rec.status === 'Pending') return false;
    var k = U.monthKey(rec.from);
    if (o.from && k < o.from) return false;
    if (o.to && k > o.to) return false;
    return true;
  }

  /* Month keys spanning the selected window, in order. */
  function monthSpan(from, to) {
    var out = [], p = from.split('-'), y = +p[0], m = +p[1];
    var guard = 0;
    while (guard++ < 60) {
      var k = y + '-' + U.pad(m);
      out.push(k);
      if (k >= to) break;
      m++; if (m > 12) { m = 1; y++; }
    }
    return out;
  }

  function emptyBuckets() {
    return { planned: 0, unplanned: 0, plannedHalf: 0, unplannedHalf: 0 };
  }

  function compute(opts) {
    var o = opts || {};
    /* Only what this user has full access to (everyone for an admin, a manager's
       teams), narrowed to one team when an admin picks it. */
    var ids = o.team && o.team !== 'all' ? S.teamMemberIds(o.team) : null;
    var inTeam = function (x) { return !ids || !!ids[x.empId]; };
    var st = { leave: S.visible('leave').filter(inTeam), wfh: S.visible('wfh').filter(inTeam), people: S.teamPeople().filter(inTeam) };
    var months = monthSpan(o.from, o.to);
    var leave = st.leave.filter(function (r) { return inScope(r, o); });
    var wfh = st.wfh.filter(function (r) { return inScope(r, o); });

    /* Roster plus anyone appearing in the records. A member removed from the
       team while their history was kept still has to show up here, or their
       days would count in the totals but vanish from every per-person view. */
    var names;
    if (o.person && o.person !== 'all') {
      names = [o.person];
    } else {
      names = uniq(st.people.map(function (p) { return p.name; })
        .concat(leave.concat(wfh).map(function (r) { return r.name; })));
    }

    /* capacity: person-days of Mon-Fri time available in the window */
    var workingDays = U.sum(months, U.workingDaysInMonth);
    var capacity = workingDays * (names.length || 1);

    /* ---- leave: four buckets ---- */
    var buckets = emptyBuckets(), bucketCounts = emptyBuckets();
    leave.forEach(function (r) {
      var b = S.bucketOf(r);
      buckets[b] += r.days;
      bucketCounts[b] += 1;
    });
    var leaveDays = U.sum(leave, function (r) { return r.days; });
    var plannedDays = buckets.planned + buckets.plannedHalf;
    var unplannedDays = buckets.unplanned + buckets.unplannedHalf;

    /* ---- per person ---- */
    var byPerson = names.map(function (name) {
      var mine = leave.filter(function (r) { return r.name === name; });
      var w = wfh.filter(function (r) { return r.name === name; });
      var b = emptyBuckets();
      mine.forEach(function (r) { b[S.bucketOf(r)] += r.days; });
      var total = b.planned + b.unplanned + b.plannedHalf + b.unplannedHalf;
      var unp = b.unplanned + b.unplannedHalf;
      var wDays = U.sum(w, function (r) { return r.days; });
      return {
        name: name, buckets: b, total: total,
        unplannedDays: unp, plannedDays: total - unp,
        unplannedPct: U.pct(unp, total),
        episodes: mine.length, unplannedEpisodes: mine.filter(function (r) { return !S.isPlanned(r); }).length,
        wfhDays: wDays, wfhEpisodes: w.length,
        wfhRate: U.pct(wDays, workingDays),
        outRate: U.pct(total + wDays, workingDays)
      };
    });

    /* ---- per month ---- */
    var byMonth = months.map(function (k) {
      var l = leave.filter(function (r) { return U.monthKey(r.from) === k; });
      var w = wfh.filter(function (r) { return U.monthKey(r.from) === k; });
      var b = emptyBuckets();
      l.forEach(function (r) { b[S.bucketOf(r)] += r.days; });
      var wd = U.workingDaysInMonth(k) * (names.length || 1);
      var lD = b.planned + b.unplanned + b.plannedHalf + b.unplannedHalf;
      var wD = U.sum(w, function (r) { return r.days; });
      return {
        key: k, label: U.monthLabel(k), buckets: b,
        leaveDays: lD, wfhDays: wD, capacity: wd,
        onsite: Math.max(0, wd - lD - wD),
        absenceRate: U.pct(lD, wd), wfhRate: U.pct(wD, wd)
      };
    });

    /* ---- reason mixes ---- */
    function mix(records) {
      var g = U.groupBy(records, function (r) { return r.reason || 'Unspecified'; });
      return Object.keys(g).map(function (k) {
        return { label: k, days: U.sum(g[k], function (r) { return r.days; }), count: g[k].length };
      }).sort(function (a, b) { return b.days - a.days; });
    }

    /* ---- weekday pattern (Mon-Fri) ---- */
    function weekday(records, splitFn) {
      var rows = [0, 1, 2, 3, 4].map(function (i) { return { dow: i + 1, label: U.DOW[i + 1], a: 0, b: 0, total: 0 }; });
      records.forEach(function (r) {
        S.expand(r).forEach(function (d) {
          var g = U.parse(d.date).getDay();
          if (g < 1 || g > 5) return;
          var row = rows[g - 1];
          row.total += d.fraction;
          if (splitFn && splitFn(r)) row.a += d.fraction; else row.b += d.fraction;
        });
      });
      return rows;
    }

    /* ---- coverage risk: working days with the most people away ---- */
    var dayIndex = {};
    [['leave', leave], ['wfh', wfh]].forEach(function (pair) {
      var kind = pair[0];
      pair[1].forEach(function (r) {
        S.expand(r).forEach(function (d) {
          if (months.indexOf(U.monthKey(d.date)) === -1) return;
          var slot = dayIndex[d.date] || (dayIndex[d.date] = { date: d.date, leave: [], wfh: [], absent: 0 });
          slot[kind].push(r.name);
          if (kind === 'leave') slot.absent += d.fraction;
        });
      });
    });
    var coverage = Object.keys(dayIndex).map(function (k) {
      var s = dayIndex[k];
      var offsite = uniq(s.leave).length + uniq(s.wfh).length;
      return {
        date: k, leave: uniq(s.leave), wfh: uniq(s.wfh),
        absent: s.absent, offsite: offsite,
        onsite: Math.max(0, names.length - offsite),
        coverPct: U.pct(Math.max(0, names.length - offsite), names.length || 1)
      };
    }).sort(function (a, b) {
      return (b.leave.length - a.leave.length) || (b.offsite - a.offsite) || (a.date < b.date ? -1 : 1);
    });

    /* ---- streak / recency insights ---- */
    var today = U.todayISO();
    var upcoming = st.leave.concat(st.wfh).filter(function (r) {
      return r.status !== 'Declined' && (r.to || r.from) >= today;
    }).sort(function (a, b) { return a.from < b.from ? -1 : 1; });

    var wfhDays = U.sum(wfh, function (r) { return r.days; });
    var wfhUsers = uniq(wfh.map(function (r) { return r.name; }));

    return {
      months: months, names: names, workingDays: workingDays, capacity: capacity,
      leave: leave, wfh: wfh,
      buckets: buckets, bucketCounts: bucketCounts,
      leaveDays: leaveDays, plannedDays: plannedDays, unplannedDays: unplannedDays,
      unplannedShare: U.pct(unplannedDays, leaveDays),
      halfDays: bucketCounts.plannedHalf + bucketCounts.unplannedHalf,
      absenceRate: U.pct(leaveDays, capacity),
      byPerson: byPerson, byMonth: byMonth,
      leaveMix: mix(leave), wfhMix: mix(wfh),
      leaveWeekday: weekday(leave, function (r) { return !S.isPlanned(r); }),
      wfhWeekday: weekday(wfh, function () { return true; }),
      coverage: coverage,
      wfhDays: wfhDays, wfhRate: U.pct(wfhDays, capacity),
      wfhUsers: wfhUsers, wfhAdoption: U.pct(wfhUsers.length, names.length || 1),
      wfhEpisodes: wfh.length,
      pendingLeave: st.leave.filter(function (r) { return r.status === 'Pending'; }).length,
      pendingWfh: st.wfh.filter(function (r) { return r.status === 'Pending'; }).length,
      upcoming: upcoming,
      zeroUnplanned: byPerson.filter(function (p) { return p.unplannedDays === 0; }).length
    };
  }

  function uniq(a) {
    var seen = {}, out = [];
    a.forEach(function (x) { if (!seen[x]) { seen[x] = 1; out.push(x); } });
    return out;
  }

  W.analytics = { compute: compute, monthSpan: monthSpan, uniq: uniq };
})(window.WFS);
