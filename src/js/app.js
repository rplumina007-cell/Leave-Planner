/* Shell: signed-in identity, role-based navigation and hash routing.

   What each role can open is decided by ACCESS below for the UI, and enforced
   again by the server for the data - hiding a page here is convenience, not
   security. To add a page, register it in src/js/pages/ and add its id to
   the roles that should see it. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, el = U.el;

  /* Stamped by tools/build-single-file.py; 'dev' when served from source. */
  var BUILD = 'dev';

  var ORDER = ['calendar', 'approvals', 'insights', 'records'];
  var ACCESS = {
    employee: ['calendar'],
    manager: ['calendar', 'approvals', 'insights', 'records'],
    admin: ['calendar', 'approvals', 'insights', 'records']
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

  function themeButton() {
    return el('button', {
      class: 'iconbtn', title: 'Toggle light / dark', text: '◐', onclick: function () {
        var cur = document.documentElement.getAttribute('data-theme');
        var next = cur === 'dark' ? 'light' : cur === 'light' ? '' : 'dark';
        if (next) document.documentElement.setAttribute('data-theme', next);
        else document.documentElement.removeAttribute('data-theme');
        try { localStorage.setItem('wfs-theme', next); } catch (e) { }
      }
    });
  }

  function identity(user) {
    return el('div', { class: 'who' }, [
      U.avatar(user.name),
      el('div', { style: 'line-height:1.25' }, [
        el('div', { style: 'font-weight:700;font-size:13px', text: user.name }),
        el('div', { class: 'sub', style: 'font-size:11px', text: (ROLE_LABEL[user.role] || user.role) + ' · ' + user.empId +
          (user.teams && user.teams.length ? ' · ' + user.teams.join(', ') : '') })
      ])
    ]);
  }

  function topbar(page) {
    var me = S.me();
    var nav = el('nav', { class: 'nav' }, pages().map(function (p) {
      var n = p.badge ? p.badge() : 0;
      return el('a', {
        href: '#/' + p.id, class: p.id === page.id ? 'on' : ''
      }, [p.label, n ? el('span', { class: 'count', style: 'margin-left:6px', text: String(n) }) : null]);
    }));

    return el('header', { class: 'topbar' }, [
      el('div', { class: 'brand' }, [el('span', { class: 'dot', text: 'L' }), el('span', { text: 'Leave Planner' })]),
      nav,
      el('span', { class: 'spacer' }),
      identity(me),
      el('button', { class: 'iconbtn', title: 'Change password', text: '🔑', onclick: function () { passwordDialog(); } }),
      S.is('admin') ? el('button', { class: 'iconbtn', title: 'Settings', text: '⚙', onclick: function () { W.settings.open(); } }) : null,
      themeButton(),
      el('button', { class: 'btn sm', title: 'Sign out', text: 'Sign out', onclick: function () { S.logout(); } })
    ]);
  }

  /* Shown to admins when data/seed.json holds a newer sheet import than the server has loaded. */
  function syncBanner() {
    var when = S.seedInfo().generatedAt;
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
        el('button', { class: 'btn primary', text: '⟳ Load sheet data', onclick: runSync })
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

  /* ---- password: optional from the key button, mandatory at first sign-in ---- */
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
        el('button', { class: 'iconbtn', text: '✕', title: 'Close', onclick: close })
      ]),
      el('div', { class: 'card-b' }, [passwordForm(false, close)])
    ]));
    overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
    document.body.appendChild(overlay);
  }

  function forcedPasswordScreen(user) {
    var root = document.getElementById('root');
    U.clear(root);
    root.appendChild(el('header', { class: 'topbar' }, [
      el('div', { class: 'brand' }, [el('span', { class: 'dot', text: 'L' }), el('span', { text: 'Leave Planner' })]),
      el('span', { class: 'spacer' }),
      identity(user),
      themeButton(),
      el('button', { class: 'btn sm', text: 'Sign out', onclick: function () { S.logout(); } })
    ]));
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
