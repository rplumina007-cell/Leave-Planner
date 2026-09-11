/* Shell: signed-in identity, role-based navigation and hash routing.

   What each role can open is decided by ACCESS below for the UI, and enforced
   again by the server for the data - hiding a page here is convenience, not
   security. To add a page, register it in src/js/pages/ and add its id to
   the roles that should see it. A page with nav:false is reachable but not
   listed in the top bar (Settings lives in the profile menu). */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, el = U.el, I = W.icon;

  /* Stamped by tools/build-single-file.py; 'dev' when served from source. */
  var BUILD = 'dev';

  var ORDER = ['calendar', 'approvals', 'insights', 'records', 'settings'];
  var ACCESS = {
    employee: ['calendar'],
    manager: ['calendar', 'approvals', 'insights', 'records'],
    admin: ['calendar', 'approvals', 'insights', 'records', 'settings']
  };
  var ROLE_LABEL = { employee: 'Employee', manager: 'Manager', admin: 'Admin' };

  function pages() {
    var reg = W.pages || {};
    var allowed = ACCESS[S.role()] || [];
    return ORDER.concat(Object.keys(reg).filter(function (k) { return ORDER.indexOf(k) === -1; }))
      .filter(function (k) { return reg[k] && allowed.indexOf(k) !== -1; })
      .map(function (k) { return reg[k]; });
  }

  function route() {
    var id = (location.hash || '').replace(/^#\/?/, '');
    var list = pages();
    return list.filter(function (p) { return p.id === id; })[0] || list[0];
  }

  function rerender() { mount(); }

  function mount() {
    var root = document.getElementById('root');
    U.clear(root);
    var page = route();
    if (location.hash !== '#/' + page.id) history.replaceState(null, '', '#/' + page.id);
    root.appendChild(topbar(page));
    if (S.is('admin') && S.seedIsNewer()) root.appendChild(syncBanner());
    var news = changesNotice();
    if (news) root.appendChild(news);
    var body = el('main', {});
    try {
      body.appendChild(page.render(body));
    } catch (err) {
      body.appendChild(el('div', { class: 'card' }, [
        el('div', { class: 'card-b' }, [
          el('h3', { text: 'Something went wrong rendering this page' }),
          el('pre', { class: 'mono', style: 'white-space:pre-wrap;color:var(--bad)', text: String(err && err.stack || err) })
        ])
      ]));
      if (window.console) console.error(err);
    }
    root.appendChild(body);
  }

  /* ---- theme: an explicit choice of System / Light / Dark ---- */
  function themeMode() { return document.documentElement.getAttribute('data-theme') || ''; }
  function setTheme(mode) {
    if (mode) document.documentElement.setAttribute('data-theme', mode);
    else document.documentElement.removeAttribute('data-theme');
    try { localStorage.setItem('wfs-theme', mode); } catch (e) { }
  }

  function roleLine(user) {
    return (ROLE_LABEL[user.role] || user.role) + ' · ' + user.empId +
      (user.teams && user.teams.length ? ' · ' + user.teams.join(', ') : '');
  }

  /* ---- profile menu: one place for password, settings, theme and sign-out ---- */
  function profileMenu(user, minimal) {
    var wrap = el('div', { class: 'menu-wrap' });
    var panel = null;
    var btn = el('button', {
      class: 'who-btn', type: 'button', 'aria-haspopup': 'menu', 'aria-expanded': 'false', title: 'Account and settings'
    }, [
      U.avatar(user.name),
      el('div', { class: 'who-text', style: 'line-height:1.25' }, [
        el('div', { style: 'font-weight:700;font-size:13px', text: user.name }),
        el('div', { class: 'sub', style: 'font-size:11px', text: roleLine(user) })
      ]),
      I('chevD')
    ]);

    function outside(e) { if (!wrap.contains(e.target)) close(); }
    function onKey(e) { if (e.key === 'Escape') { close(); btn.focus(); } }
    function close() {
      if (!panel) return;
      panel.remove();
      panel = null;
      btn.setAttribute('aria-expanded', 'false');
      document.removeEventListener('mousedown', outside);
      document.removeEventListener('keydown', onKey);
    }
    function item(icon, label, fn) {
      return el('button', { class: 'mi', type: 'button', role: 'menuitem', onclick: function () { close(); fn(); } }, [I(icon), label]);
    }
    function themeChoice() {
      var cur = themeMode();
      return el('div', { class: 'seg', role: 'group', 'aria-label': 'Theme' }, [['', 'System'], ['light', 'Light'], ['dark', 'Dark']].map(function (t) {
        return el('button', {
          type: 'button', class: cur === t[0] ? 'on' : '', 'aria-pressed': cur === t[0] ? 'true' : 'false',
          onclick: function () { setTheme(t[0]); close(); }
        }, [t[1]]);
      }));
    }

    btn.addEventListener('click', function () {
      if (panel) { close(); return; }
      panel = el('div', { class: 'menu', role: 'menu' }, [
        el('div', { style: 'padding:8px 10px 6px' }, [
          el('div', { style: 'font-weight:700;font-size:13px', text: user.name }),
          el('div', { class: 'sub', style: 'font-size:11.5px', text: roleLine(user) })
        ]),
        el('div', { class: 'sep' }),
        minimal ? null : item('key', 'Change password', passwordDialog),
        !minimal && S.is('admin') ? item('settings', 'Settings', function () { location.hash = '#/settings'; }) : null,
        minimal ? null : el('div', { class: 'sep' }),
        el('div', { class: 'mlabel', text: 'Theme' }),
        themeChoice(),
        el('div', { class: 'sep' }),
        item('logout', 'Sign out', function () { S.logout(); })
      ]);
      wrap.appendChild(panel);
      btn.setAttribute('aria-expanded', 'true');
      setTimeout(function () {
        document.addEventListener('mousedown', outside);
        document.addEventListener('keydown', onKey);
      }, 0);
      var first = panel.querySelector('.mi');
      if (first) first.focus();
    });
    wrap.appendChild(btn);
    return wrap;
  }

  function brand() {
    return el('div', { class: 'brand' }, [el('span', { class: 'dot', text: 'L' }), el('span', { text: 'Leave Planner' })]);
  }

  function topbar(page) {
    var nav = el('nav', { class: 'nav' }, pages().filter(function (p) { return p.nav !== false; }).map(function (p) {
      var n = p.badge ? p.badge() : 0;
      return el('a', {
        href: '#/' + p.id, class: p.id === page.id ? 'on' : '', 'aria-current': p.id === page.id ? 'page' : null
      }, [p.label, n ? el('span', { class: 'count', style: 'margin-left:6px', text: String(n) }) : null]);
    }));

    return el('header', { class: 'topbar' }, [
      brand(), nav, el('span', { class: 'spacer' }), profileMenu(S.me())
    ]);
  }

  /* Shown to admins when data/seed.json holds a newer sheet import than the server has loaded. */
  function syncBanner() {
    return el('div', { class: 'card', style: 'margin-bottom:18px;border-left:4px solid var(--accent)' }, [
      el('div', { class: 'card-h', style: 'border-bottom:0' }, [
        el('div', { style: 'flex:1;min-width:220px' }, [
          el('h3', { text: 'Updated data from the Google Sheet is ready' }),
          el('div', {
            class: 'sub',
            text: 'A newer sheet import is on the server. Loading it keeps requests submitted here, ' +
              'approvals made here, and Settings changes.'
          })
        ]),
        el('button', { class: 'btn primary', type: 'button', onclick: runSync }, [I('refresh'), 'Load sheet data'])
      ])
    ]);
  }

  function runSync() {
    return S.syncFromSeed().then(function (res) {
      if (!res) return;
      var r = res.summary;
      U.toast('Sheet data loaded — ' + r.added + ' new, ' + r.updated + ' updated, ' + r.removed + ' removed' +
        (r.keptLocal ? ', ' + r.keptLocal + ' app-only kept' : ''));
    });
  }

  /* ---- "What's changed": decisions on your own requests since you last looked.
     Remembered per browser; the very first visit just starts the clock. ---- */
  function changesNotice() {
    var me = S.me(), key = 'lp-seen-' + me.empId, seen;
    try { seen = localStorage.getItem(key); } catch (e) { return null; }
    if (!seen) {
      try { localStorage.setItem(key, new Date().toISOString()); } catch (e) { }
      return null;
    }
    var since = Date.parse(seen);
    var news = S.myRequests().filter(function (r) {
      return r.status !== 'Pending' && r.decidedAt && Date.parse(r.decidedAt) > since;
    }).sort(function (a, b) { return Date.parse(b.decidedAt) - Date.parse(a.decidedAt); });
    if (!news.length) return null;

    function dismiss() {
      try { localStorage.setItem(key, new Date().toISOString()); } catch (e) { }
      rerender();
    }
    return el('div', { class: 'card notice', role: 'status' }, [
      el('div', { class: 'card-h', style: 'border-bottom:0;align-items:flex-start' }, [
        el('div', { style: 'flex:1;min-width:220px;display:grid;gap:4px' }, [
          el('h3', { text: 'What’s changed since your last visit' })
        ].concat(news.slice(0, 4).map(function (r) {
          var ok = r.status === 'Approved';
          return el('div', { class: 'nl' }, [
            el('span', { style: 'color:' + (ok ? 'var(--ok)' : 'var(--bad)') }, [I(ok ? 'check' : 'x')]),
            el('span', {}, [
              'Your ' + (r.kind === 'wfh' ? 'WFH' : 'leave') + ' for ',
              el('b', { text: U.fmtRange(r.from, r.to) }),
              ' was ' + r.status.toLowerCase() + (r.decidedBy ? ' by ' + r.decidedBy : '') + '.',
              !ok && r.comment ? el('span', { class: 'sub', text: ' “' + r.comment + '”' }) : null
            ])
          ]);
        })).concat(news.length > 4 ? [el('div', { class: 'sub', text: '+' + (news.length - 4) + ' more — see My requests on the Calendar' })] : [])),
        el('button', { class: 'btn sm', type: 'button', onclick: dismiss }, ['Got it'])
      ])
    ]);
  }

  /* ---- password: optional from the profile menu, mandatory at first sign-in ---- */
  function passwordForm(forced, onDone) {
    var cur = el('input', { type: 'password', autocomplete: 'current-password' });
    var next = el('input', { type: 'password', autocomplete: 'new-password' });
    var again = el('input', { type: 'password', autocomplete: 'new-password' });
    var err = el('div', {});
    var go = el('button', { class: 'btn primary', type: 'submit', text: forced ? 'Set password and continue' : 'Change password' });

    var form = el('form', { style: 'display:grid;gap:13px' }, [
      el('label', { class: 'f' }, [forced ? 'One-time password' : 'Current password', cur]),
      el('label', { class: 'f' }, ['New password', next]),
      el('label', { class: 'f' }, ['Confirm new password', again]),
      el('div', { class: 'sub', style: 'font-size:12px', text: 'At least 10 characters, not a common password, and not containing your name or employee ID.' }),
      err,
      el('div', { class: 'row', style: 'justify-content:flex-end' }, [go])
    ]);
    form.addEventListener('submit', function (e) {
      e.preventDefault();
      U.clear(err);
      function fail(msg) { err.appendChild(el('div', { class: 'err', text: msg })); }
      if (!cur.value || !next.value) return fail('Fill in all three fields.');
      if (next.value !== again.value) return fail('The new passwords don\'t match.');
      if (next.value.length < 10) return fail('New password must be at least 10 characters.');
      go.disabled = true;
      S.changePassword(cur.value, next.value).then(function () {
        U.toast('Password changed');
        onDone();
      }, function (e2) {
        go.disabled = false;
        fail(e2.message);
      });
    });
    setTimeout(function () { cur.focus(); }, 0);
    return form;
  }

  function passwordDialog() {
    var overlay = el('div', { class: 'modal' });
    function close() { overlay.remove(); }
    overlay.appendChild(el('div', { class: 'box' }, [
      el('div', { class: 'card-h' }, [
        el('h3', { text: 'Change password' }), el('span', { style: 'flex:1' }),
        el('button', { class: 'iconbtn', type: 'button', title: 'Close', 'aria-label': 'Close', onclick: close }, [I('x')])
      ]),
      el('div', { class: 'card-b' }, [passwordForm(false, close)])
    ]));
    overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
    document.body.appendChild(overlay);
  }

  function forcedPasswordScreen(user) {
    var root = document.getElementById('root');
    U.clear(root);
    root.appendChild(el('header', { class: 'topbar' }, [brand(), el('span', { class: 'spacer' }), profileMenu(user, true)]));
    root.appendChild(el('main', { style: 'display:grid;place-items:center;padding:30px 0' }, [
      el('div', { class: 'card', style: 'width:min(460px,100%)' }, [
        el('div', { class: 'card-h' }, [el('div', {}, [
          el('h3', { text: 'Choose your own password' }),
          el('div', { class: 'sub', text: 'You signed in with a one-time password. Set a private one to continue.' })
        ])]),
        el('div', { class: 'card-b' }, [passwordForm(true, start)])
      ])
    ]));
  }

  function currentUser() { return S.me().name; }

  function start() {
    return S.init().then(function () { mount(); }, function (e) {
      if (e.payload && e.payload.mustChange) {
        return W.api.get('/api/me').then(function (res) { forcedPasswordScreen(res.user); });
      }
      var root = document.getElementById('root');
      U.clear(root);
      root.appendChild(el('div', { class: 'card', style: 'margin-top:40px' }, [
        el('div', { class: 'card-b' }, [el('h3', { text: 'Could not load the tracker' }), el('div', { class: 'sub', text: e.message })])
      ]));
    });
  }

  function boot() {
    try {
      var t = localStorage.getItem('wfs-theme');
      if (t) document.documentElement.setAttribute('data-theme', t);
    } catch (e) { }
    S.subscribe(function () { if (document.getElementById('root').firstChild) mount(); });
    window.addEventListener('hashchange', function () { if (S.state) mount(); });
    start();
  }

  W.app = { boot: boot, rerender: rerender, currentUser: currentUser, pages: pages, build: BUILD };
})(window.WFS);

document.addEventListener('DOMContentLoaded', function () { window.WFS.app.boot(); });
