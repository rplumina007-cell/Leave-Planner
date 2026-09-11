/* Sign-in page. Kept separate from the app bundle: it is the only script the
   server hands out before a session exists. */
(function () {
  'use strict';
  try {
    var t = localStorage.getItem('wfs-theme');
    if (t) document.documentElement.setAttribute('data-theme', t);
  } catch (e) { }

  var form = document.getElementById('form');
  var emp = document.getElementById('emp');
  var pw = document.getElementById('pw');
  var err = document.getElementById('err');
  var go = document.getElementById('go');

  function show(msg) { err.textContent = msg; err.hidden = !msg; }

  /* Show / hide the password, and warn while Caps Lock is on. */
  var toggle = document.getElementById('pwToggle');
  var slash = document.getElementById('pwSlash');
  var caps = document.getElementById('caps');
  toggle.addEventListener('click', function () {
    var reveal = pw.type === 'password';
    pw.type = reveal ? 'text' : 'password';
    var label = reveal ? 'Hide password' : 'Show password';
    toggle.setAttribute('aria-pressed', reveal ? 'true' : 'false');
    toggle.setAttribute('aria-label', label);
    toggle.title = label;
    slash.setAttribute('display', reveal ? 'inline' : 'none');
    pw.focus();
  });
  function capsCheck(e) {
    if (e && typeof e.getModifierState === 'function') caps.hidden = !e.getModifierState('CapsLock');
  }
  pw.addEventListener('keydown', capsCheck);
  pw.addEventListener('keyup', capsCheck);
  pw.addEventListener('blur', function () { caps.hidden = true; });

  form.addEventListener('submit', function (e) {
    e.preventDefault();
    show('');
    if (!emp.value.trim() || !pw.value) { show('Enter your employee ID and password.'); return; }
    go.disabled = true;
    go.textContent = 'Signing in…';
    fetch('/api/login', {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-WFS-Request': '1' },
      body: JSON.stringify({ empId: emp.value.trim(), password: pw.value })
    }).then(function (r) {
      return r.json().then(function (body) { return { ok: r.ok, body: body }; });
    }).then(function (res) {
      if (res.ok) { location.replace('/'); return; }
      pw.value = '';
      show(res.body.error || 'Sign-in failed.');
      go.disabled = false; go.textContent = 'Sign in';
      pw.focus();
    }).catch(function () {
      show('Could not reach the server. Is it running?');
      go.disabled = false; go.textContent = 'Sign in';
    });
  });
})();
