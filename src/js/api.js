/* Thin fetch wrapper for the server API.
   Resolves with the JSON payload; rejects with an Error carrying .status and
   .payload. A lost session sends the browser back to the sign-in page. */
window.WFS = window.WFS || {};
(function (W) {
  'use strict';

  function request(method, path, body) {
    var opts = {
      method: method,
      credentials: 'same-origin',
      headers: { 'Accept': 'application/json' }
    };
    if (method !== 'GET') {
      opts.headers['Content-Type'] = 'application/json';
      opts.headers['X-WFS-Request'] = '1';   // the server refuses POSTs without it (CSRF guard)
      opts.body = JSON.stringify(body || {});
    }
    return fetch(path, opts).then(function (r) {
      return r.json().catch(function () { return {}; }).then(function (payload) {
        if (r.ok) return payload;
        if (r.status === 401 && path !== '/api/login' && path !== '/api/me/password') {
          location.replace('/login.html');
        }
        var e = new Error(payload.error || ('Request failed (' + r.status + ')'));
        e.status = r.status;
        e.payload = payload;
        throw e;
      });
    }, function () {
      throw new Error('Could not reach the server. Is it still running?');
    });
  }

  W.api = {
    get: function (path) { return request('GET', path); },
    post: function (path, body) { return request('POST', path, body); }
  };
})(window.WFS);
