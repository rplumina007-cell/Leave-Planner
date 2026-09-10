/* Apply-for-leave / apply-for-WFH modal.
   Every dropdown is populated from state.validation, which mirrors the sheet's
   Validation tab - add an option there and it shows up here automatically. */
(function (W) {
  'use strict';
  var U = W.util, S = W.store, el = U.el;

  function select(name, options, value) {
    return el('select', { name: name, required: true }, options.map(function (o) {
      return el('option', { value: o, selected: o === value || null, text: o });
    }));
  }

  function open(kind, prefill) {
    prefill = prefill || {};
    var v = S.state.validation;
    var reasons = kind === 'leave' ? v.leaveReasons : v.wfhReasons;
    var today = U.todayISO();

    /* Only an admin files on someone else's behalf; everyone else is fixed to
       themselves (the server enforces the same rule). */
    var me = S.me();
    var canPickAnyone = me.role === 'admin';
    var fName = canPickAnyone
      ? el('select', { name: 'empId', required: true }, S.state.people.map(function (p) {
          return el('option', { value: p.empId, selected: p.empId === (prefill.empId || me.empId) || null, text: p.name + ' · ' + p.empId });
        }))
      : el('input', { name: 'empId', value: me.name + ' · ' + me.empId, disabled: true });
    var fReason = select('reason', reasons, prefill.reason);
    var fDur = select('duration', v.durations, prefill.duration);
    var fFrom = el('input', { type: 'date', name: 'from', required: true, value: prefill.from || today });
    var fTo = el('input', { type: 'date', name: 'to', required: true, value: prefill.to || prefill.from || today });
    var fComment = el('textarea', { name: 'comment', rows: 2, placeholder: 'Optional context for your manager' });
    var summary = el('div', { class: 'sub' });
    var warn = el('div', {});

    function draft() {
      var half = fDur.value.indexOf('Half') === 0;
      return {
        empId: canPickAnyone ? fName.value : me.empId,
        name: canPickAnyone ? (S.state.people.filter(function (p) { return p.empId === fName.value; })[0] || {}).name : me.name,
        from: fFrom.value, to: half ? fFrom.value : fTo.value,
        duration: fDur.value, reason: fReason.value, comment: fComment.value.trim(),
        id: '__draft__'
      };
    }

    function refresh() {
      var half = fDur.value.indexOf('Half') === 0;
      fTo.disabled = half;
      if (half) fTo.value = fFrom.value;
      if (fTo.value < fFrom.value) fTo.value = fFrom.value;

      var d = draft();
      var days = S.computeDays(d.from, d.to, d.duration);
      U.clear(warn);
      U.clear(summary);

      if (!days) {
        summary.appendChild(el('span', { class: 'err', text: 'That range has no working days (Mon–Fri).' }));
      } else {
        summary.appendChild(el('span', {}, [
          el('b', { text: U.n1(days) + (days === 1 ? ' working day' : ' working days') }),
          '  ·  ' + U.fmtRange(d.from, d.to)
        ]));
      }

      var mine = S.selfConflict(d);
      if (mine.length) {
        warn.appendChild(el('div', {
          class: 'err',
          text: '⚠ ' + d.name + ' already has ' + mine.length + ' overlapping request (' +
            mine.map(function (m) { return (m.kind === 'leave' ? 'Leave' : 'WFH') + ' ' + U.fmtRange(m.from, m.to); }).join(', ') + ').'
        }));
      }
      var others = S.clashes(d);
      if (others.length) {
        var names = W.analytics.uniq(others.map(function (o) { return o.rec.name; }));
        warn.appendChild(el('div', {
          style: 'margin-top:8px;font-size:12px;color:var(--warn);font-weight:600',
          text: '👥 Overlaps with ' + names.length + ' teammate' + (names.length > 1 ? 's' : '') + ': ' + names.join(', ')
        }));
      }
      return days;
    }

    [fName, fReason, fDur, fFrom, fTo].forEach(function (f) { f.addEventListener('change', refresh); });

    var overlay = el('div', { class: 'modal' }, [
      el('form', { class: 'box' }, [
        el('div', { class: 'card-h' }, [
          el('span', { class: 'pill ' + kind, text: kind === 'leave' ? '🌴 Leave' : '🏠 Work from Home' }),
          el('h3', { text: 'New request' }),
          el('span', { class: 'spacer', style: 'flex:1' }),
          el('button', { type: 'button', class: 'iconbtn', text: '✕', onclick: close })
        ]),
        el('div', { class: 'card-b', style: 'display:grid;gap:13px' }, [
          el('div', { class: 'f2' }, [
            el('label', { class: 'f' }, ['Team member', fName]),
            el('label', { class: 'f' }, [kind === 'leave' ? 'Leave reason' : 'WFH reason', fReason])
          ]),
          el('div', { class: 'f2' }, [
            el('label', { class: 'f' }, ['Duration', fDur]),
            el('label', { class: 'f' }, ['From date', fFrom])
          ]),
          el('div', { class: 'f2' }, [
            el('label', { class: 'f' }, ['To date', fTo]),
            el('div', { style: 'display:flex;align-items:flex-end;padding-bottom:9px' }, [summary])
          ]),
          el('label', { class: 'f' }, ['Comment', fComment]),
          warn
        ]),
        el('div', { class: 'card-h', style: 'border-top:1px solid var(--line);border-bottom:0;justify-content:flex-end' }, [
          el('button', { type: 'button', class: 'btn', text: 'Cancel', onclick: close }),
          el('button', { type: 'submit', class: 'btn primary', text: 'Submit for approval' })
        ])
      ])
    ]);

    overlay.querySelector('form').addEventListener('submit', function (e) {
      e.preventDefault();
      var days = refresh();
      if (!days) return;
      var d = draft();
      delete d.id;
      var btn = overlay.querySelector('button[type=submit]');
      btn.disabled = true;
      S.submit(kind, d).then(function () {
        close();
        U.toast((kind === 'leave' ? 'Leave' : 'WFH') + ' request submitted — pending approval');
      }, function (err) {
        btn.disabled = false;
        U.clear(warn);
        warn.appendChild(el('div', { class: 'err', text: err.message }));
      });
    });
    overlay.addEventListener('mousedown', function (e) { if (e.target === overlay) close(); });
    function onKey(e) { if (e.key === 'Escape') close(); }
    document.addEventListener('keydown', onKey);
    function close() { overlay.remove(); document.removeEventListener('keydown', onKey); }

    document.body.appendChild(overlay);
    refresh();
    (canPickAnyone ? fName : fReason).focus();
  }

  W.form = { open: open };
})(window.WFS);
