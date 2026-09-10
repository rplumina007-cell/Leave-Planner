/* Team filter shared by Calendar, Approvals, Dashboards and Register.

   Offered to admins, and to managers who see more than one team (several teams,
   or All Teams). The choice carries from page to page. The server has already
   limited what reaches this user - the filter only narrows it further. It counts
   people explicitly in a team, so All Teams members appear only under "all". */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, el = U.el;
  var chosen = 'all';

  /* [{v, t}] choices, or null when this user doesn't get the filter. */
  function options() {
    var mine = S.me().teams || [];
    var everyTeam = S.is('admin') || mine.indexOf(S.ALL_TEAMS) !== -1;
    if (!S.is('admin') && !(S.is('manager') && (everyTeam || mine.length > 1))) return null;
    var names = everyTeam ? (S.state.teams || []) : mine;
    return [{ v: 'all', t: everyTeam ? 'All teams' : 'All my teams' }]
      .concat(names.map(function (t) { return { v: t, t: t }; }))
      .concat(everyTeam ? [{ v: S.NO_TEAM, t: 'No team' }] : []);
  }

  /* The chosen team, falling back to 'all' if it's no longer on offer. */
  function value() {
    var opts = options();
    if (!opts || !opts.some(function (o) { return o.v === chosen; })) chosen = 'all';
    return chosen;
  }

  /* Predicate for anything carrying an empId - a record or a person. */
  function matches() {
    var t = value();
    if (t === 'all') return function () { return true; };
    var ids = S.teamMemberIds(t);
    return function (x) { return !!ids[x.empId]; };
  }

  function label() {
    var t = value();
    return t === 'all' ? '' : t === S.NO_TEAM ? 'No team' : t;
  }

  /* People this user has full access to, inside the chosen team. */
  function people() { return S.teamPeople().filter(matches()); }

  /* The <select>, or null when not offered. onChange runs before the re-render. */
  function control(onChange) {
    var opts = options();
    if (!opts) return null;
    var current = value();
    return el('label', { class: 'f', style: 'font-size:11px' }, ['Team', el('select', {
      onchange: function (e) {
        chosen = e.target.value;
        if (onChange) onChange();
        W.app.rerender();
      }
    }, opts.map(function (o) {
      return el('option', { value: o.v, selected: current === o.v || null, text: o.t });
    }))]);
  }

  W.teamFilter = {
    options: options, value: value, matches: matches, label: label, people: people, control: control,
    reset: function () { chosen = 'all'; }
  };
})(window.WFS);
