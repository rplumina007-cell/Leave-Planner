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
    okBox.hidden = true;
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

  /* ---- Forgot password: a 6-digit code by email, then a new password ---- */
  var okBox = document.getElementById('ok');
  var resetForm = document.getElementById('resetForm');
  var stepId = document.getElementById('stepId'), stepCode = document.getElementById('stepCode');
  var rEmp = document.getElementById('rEmp'), rSend = document.getElementById('rSend');
  var rSent = document.getElementById('rSent'), rCode = document.getElementById('rCode');
  var rPw = document.getElementById('rPw'), rPw2 = document.getElementById('rPw2');
  var rGo = document.getElementById('rGo'), rResend = document.getElementById('rResend');
  var rErr = document.getElementById('rErr');
  var resendTimer = null;

  function rShow(msg) { rErr.textContent = msg; rErr.hidden = !msg; }
  function post(path, body) {
    return fetch(path, {
      method: 'POST', credentials: 'same-origin',
      headers: { 'Content-Type': 'application/json', 'X-WFS-Request': '1' },
      body: JSON.stringify(body)
    }).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (b) {
        if (!r.ok) throw new Error(b.error || 'Something went wrong. Try again.');
        return b;
      });
    }, function () { throw new Error('Could not reach the server. Check your connection.'); });
  }
  function openReset() {
    show(''); okBox.hidden = true;
    form.hidden = true; resetForm.hidden = false;
    stepId.hidden = false; stepCode.hidden = true; rShow('');
    rEmp.value = emp.value.trim();
    rEmp.focus();
  }
  function backToSignIn(message) {
    clearInterval(resendTimer);
    resetForm.hidden = true; form.hidden = false;
    rCode.value = rPw.value = rPw2.value = '';
    if (rEmp.value.trim()) emp.value = rEmp.value.trim();
    okBox.textContent = message || ''; okBox.hidden = !message;
    pw.value = '';
    (emp.value ? pw : emp).focus();
  }
  function holdResend(seconds) {
    clearInterval(resendTimer);
    var left = seconds;
    rResend.disabled = true;
    rResend.textContent = 'send a new code (' + left + 's)';
    resendTimer = setInterval(function () {
      left -= 1;
      if (left > 0) { rResend.textContent = 'send a new code (' + left + 's)'; return; }
      clearInterval(resendTimer);
      rResend.disabled = false;
      rResend.textContent = 'send a new code';
    }, 1000);
  }
  function sendCode() {
    rShow('');
    var id = rEmp.value.trim();
    if (!id) { rShow('Enter your employee ID.'); rEmp.focus(); return; }
    rSend.disabled = true; rSend.textContent = 'Sending…';
    post('/api/password/forgot', { empId: id }).then(function (b) {
      rSent.textContent = b.message;
      stepId.hidden = true; stepCode.hidden = false;
      holdResend(60);
      rCode.focus();
    }, function (e) { rShow(e.message); }).then(function () {
      rSend.disabled = false; rSend.textContent = 'Email me a code';
    });
  }
  function setPassword() {
    rShow('');
    var code = rCode.value.replace(/\s/g, '');
    if (!/^\d{6}$/.test(code)) { rShow('Enter the 6-digit code from the email.'); rCode.focus(); return; }
    if (!rPw.value) { rShow('Choose a new password.'); rPw.focus(); return; }
    if (rPw.value !== rPw2.value) { rShow('The two passwords don’t match.'); rPw2.focus(); return; }
    rGo.disabled = true; rGo.textContent = 'Saving…';
    post('/api/password/reset', { empId: rEmp.value.trim(), code: code, password: rPw.value }).then(function () {
      backToSignIn('Password updated. Sign in with your new password.');
    }, function (e) {
      rShow(e.message);
      if (/code/i.test(e.message)) { rCode.focus(); rCode.select(); } else { rPw.focus(); }
    }).then(function () {
      rGo.disabled = false; rGo.textContent = 'Set new password';
    });
  }

  document.getElementById('forgot').addEventListener('click', openReset);
  document.getElementById('rBack').addEventListener('click', function () { backToSignIn(''); });
  rResend.addEventListener('click', function () {
    rCode.value = '';
    stepCode.hidden = true; stepId.hidden = false;
    sendCode();
  });
  rCode.addEventListener('input', function () { rCode.value = rCode.value.replace(/\D/g, '').slice(0, 6); });
  resetForm.addEventListener('submit', function (e) {
    e.preventDefault();
    if (stepCode.hidden) sendCode(); else setPassword();
  });
})();
