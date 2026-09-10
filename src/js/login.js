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
