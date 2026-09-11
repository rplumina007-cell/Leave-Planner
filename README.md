# Leave Planner

A web app replacing the team's leave/WFH tracker Google Sheet: a team calendar, a
manager approval queue, and combined leave + WFH dashboards.

The repository holds code only. Team data is imported from the tracker Google
Sheet on whichever machine runs the server, and its totals reconcile exactly with
the sheet's own pivot dashboards.

## Running it

The app is served by a small Python server that handles sign-in and permissions.
The server is standard-library Python — nothing to install.

**First time on a machine:** import the team data from the Google Sheet (needs
`pip install openpyxl`). The sheet link is remembered locally afterwards.

```bash
python tools/import-sheet.py "<Google Sheet link>"
```

Then start the server:

```bash
python server/app.py
```

Then open <http://localhost:8777>. Opening `index.html` directly no longer works:
the data only comes from the server, and only to signed-in users.

If this browser opened the old, pre-login version at that address, it may show a
blank page from its cache — press **Ctrl+Shift+R** once. The server marks pages
as non-cacheable, so it won't happen again.

## Sign-in & roles

**First admin** (once): creates an admin login with a one-time password.

```bash
python server/manage.py create-admin --emp-id <employee ID> --name "<Full Name>"
```

Everyone else is created by an admin in **⚙ Settings → Users**: employee ID, full
name (the user name), password, role, and manager. People sign in with their
**employee ID**, and must choose their own password at first sign-in. Anyone can
change their password later with the 🔑 button.

| | Employee | Manager | Admin |
| --- | --- | --- | --- |
| Calendar | their teams — teammates' reasons/comments hidden | their teams, in full | everyone |
| Apply leave / WFH | for themselves | for themselves, and people they manage in their teams | for anyone, including people without a login |
| Approvals | — | people they manage in their teams (never their own) | every request |
| Dashboards, Register | — | their teams | everyone |
| Delete records | withdraw own pending | people they manage in their teams; own pending | ✓ |
| Settings (users, roles, teams, managers, dropdowns, cleanup, sheet sync) | — | — | ✓ |

**Teams** decide who can see whom. An admin creates teams in **⚙ Settings →
Teams** and gives each login one or more of them in **Users**. People see only
those who share one of their teams — nothing at all about anyone else — and
**All Teams** counts as every team. Someone with no team sees only themselves.

**Team filter.** Admins, and managers in more than one team or in All Teams, get
a **Team** filter on the Calendar, Approvals, Dashboards and Register; the choice
carries from page to page. It counts people explicitly in a team (people in All
Teams show only under "All teams"), and **No team** — for admins and All Teams
managers — covers everyone without one, including people who only exist in the
records.

**Managers** act — apply on someone's behalf, approve or decline, delete — only
for people below them in the reporting chain (people assigned to them, to the
managers assigned to them, and so on) who are also in one of their teams. Every
rule is enforced by the server (`server/api.py`), not just hidden in the page.

**Security**
- Passwords are stored as scrypt hashes (N=2^16, r=8, p=2); older, weaker hashes
  are upgraded automatically at the next sign-in. Sessions are random tokens
  stored hashed, in an HttpOnly, SameSite=Strict cookie that expires after 12 hours.
- Passwords need at least 10 characters, can't contain the person's name or
  employee ID, and common ones ("Password2026!", "Welcome@123") are refused.
- Failed sign-ins are counted over 15 minutes: 5 for one ID from one address,
  20 from one address across any IDs, 50 for one ID from any addresses. Someone
  who knows a colleague's ID can't lock them out from their own device. A wrong
  ID and a wrong password give the same message.
- Register CSV exports neutralise cells a spreadsheet would run as formulas.
- Changing or resetting a password, deactivating someone, or changing their role
  signs them out everywhere. There is always at least one active admin.
- Only the sign-in page and the app's scripts are served — never `data/`,
  `server/`, `tools/` or other project files.
- Locked out? `python server/manage.py reset-password --emp-id <ID>`.

**Sharing with your team**

```bash
python server/app.py --host 0.0.0.0 --cert cert.pem --key key.pem
```

`--host 0.0.0.0` makes it reachable on your network. Use `--cert/--key` (HTTPS)
before anyone else signs in — over plain HTTP, passwords cross the network
unencrypted, and the server warns you. For wider use, have IT host it or put it
behind the company's existing sign-in.

**Database**: `%LOCALAPPDATA%\WFSTracker\wfs.sqlite3` — deliberately outside this
OneDrive folder, since a live database being synced mid-write can corrupt. Back
it up from ⚙ → Data → Export.

**Tests**: `python server/test_server.py` runs 85 end-to-end checks of the gateway,
every role rule, and hosted behaviour (lockout shared across instances, Secure
cookies behind HTTPS) against a throwaway database and generated sample data — no
imported sheet data needed. Set `TEST_DATABASE_URL` to a throwaway Postgres
database to run the same checks there.

## Hosting on Vercel (free)

The live site runs on Vercel's free plan with a free **Neon** Postgres database.
Vercel runs `server/wsgi.py` (entrypoint set in `pyproject.toml`) — the same
application as the local server — and keeps logins and records in Neon, since a
Vercel function can't keep a database file between requests.

**One-time setup**

1. Vercel → *Add New → Project* → import the `Leave-Planner` GitHub repository.
   No build settings are needed.
2. In that project, *Storage* → create a **Neon** database and connect it to the
   project. That adds `DATABASE_URL` to its environment variables. Redeploy once.
3. Copy your current data across. Put the Neon connection string in a git-ignored
   `.env.local` file as `DATABASE_URL=...` (or run `vercel env pull`), then:

   ```bash
   pip install "psycopg[binary]"
   python tools/migrate-to-postgres.py          # dry run: shows what would be copied
   python tools/migrate-to-postgres.py --yes    # copies every login and record
   ```

   Passwords are copied as hashes, so everyone keeps their current password.
4. Open the project's `.vercel.app` address and sign in.

Every push to `main` redeploys. On Vercel the server reads the visitor's address
and HTTPS status from Vercel's own forwarding headers, marks the session cookie
`Secure`, and stores login-failure counts in the database so the lockout holds
across every running instance. Manage live accounts from your computer with
`python server/manage.py --live list-users` (or `reset-password`), which reads
`.env.local`.

On the free plan the first request after a quiet spell takes a few seconds while
the function and the database wake up.

## Going live (other hosts)

The repository deliberately contains no team data, so the live server needs it
brought across. Pick one:

- **Everything as it is now** — logins, roles, manager assignments, approvals and
  every record. Stop the server, copy its database file to the live machine, and
  start the server there pointing at it:

  ```bash
  python server/app.py --db /path/to/wfs.sqlite3
  ```

  On Windows the file lives at `%LOCALAPPDATA%\WFSTracker\wfs.sqlite3`. Treat the
  copy as sensitive: it holds personal data and password hashes. Never commit it.
- **Sheet data only** — run `python tools/import-sheet.py "<Google Sheet link>"` on
  the live machine before the server's first start; it seeds itself from that
  import. Logins then need creating again there, starting with
  `server/manage.py create-admin`.

Either way, serve it over HTTPS (`--cert/--key`) before anyone else signs in.

## Pages

| Page | What it does |
| --- | --- |
| **Calendar** | Month of raised day tiles marking every day someone is on leave or WFH. Chips carry three colours — **planned leave** ochre 🌴, **unplanned leave** wine 🌴, **WFH** teal 🏠 — so planned-ness is readable without opening a day, and the leaf/house icon keeps Leave vs WFH distinct regardless. The stripe across the top of each tile splits the same three ways. Never merged into one "out" state. A dashed left border means still awaiting approval; `½AM` / `½PM` marks half days. Filter by member, type and status; click a day for the full roster. |
| **Approvals** | The manager queue. Split first into **Pending approval** vs **Approved & declined**, and within each into separate **Leave** and **WFH** cards. Shows days of notice and flags teammates already off on the same dates. Approve/decline singly or in bulk; declining prompts for a note. |
| **Dashboards** | Three tabs on one page — **Leave**, **WFH** and **Combined**. All share a single filter window (date range, team, person, whether pending counts), so changing the scope carries across all three and they stay comparable. Each has its own KPI row. Combined carries off-site pressure, per-person and monthly leave-plus-WFH totals, the three-way time split, the leave-vs-WFH rate trend, and coverage risk. |
| **Register** | The searchable, sortable equivalent of the sheet's Leave and WFH tabs, with CSV export. |

## The four leave categories

Leave is always broken out as **Planned Leave**, **Unplanned Leave**,
**Planned Half Day** and **Unplanned Half Day** — the cross of planned-ness
(reason) with duration:

| | Full Day | Half Day |
| --- | --- | --- |
| `Planned Leave` / `Compoff` / `My Day` | Planned Leave | Planned Half Day |
| `Unplanned` | Unplanned Leave | Unplanned Half Day |

Which reasons count as planned is held in `state.plannedReasons` and applied by
`bucketOf()` in `store.js`. It is **editable at runtime** — ⚙ → Dropdowns → the
"counts as planned" toggle — so adding or reclassifying a reason needs no code
change. `PLANNED_REASONS` in `store.js` is only the initial default.

## Insights beyond the sheet

The sheet's dashboards were three-row pivots (Unplanned / Planned / Total).

**Leave tab** — headline KPIs (total, planned, unplanned, half days, pending,
clean records), the four category cards, per-person and month-by-month breakdowns,
reason mix, weekday pattern, planning-discipline ranking, and coverage risk.

**WFH tab** — headline KPIs (total, adoption, remote rate, average per person, top
reason, busiest month), per-person and monthly breakdowns, reason mix, weekday
pattern, then two charts setting WFH against leave.

On top of the sheet's totals, that adds:

- **Unplanned share** and per-person **planning discipline** ranking — who books
  ahead and who calls out on the day.
- **Weekday pattern** for both leave and WFH — Monday/Friday clustering is
  visible at a glance.
- **Coverage risk** — the days with the most people off at once, and how many
  were left in office.
- **Where the team's time went** — person-days per month split in-office / WFH /
  leave, against real Mon–Fri capacity.
- **Leave vs WFH rate over time** — whether remote days are substituting for leave.
- Rates are computed against **actual working days** (Mon–Fri), not raw calendar
  days, so a Nov 24 → Dec 4 request counts as 9 days exactly as the sheet does.

## Data

`data/seed.json` is generated from the sheet and is **not committed** — it holds
personal data, as does `data/sheet-id.txt`, which remembers the sheet link. To
refresh it:

```bash
pip install openpyxl
python tools/import-sheet.py "<Google Sheet link>"   # later runs need no link
```

It reads the **Leave**, **WFH** and **Validation** tabs. Every dropdown in the app
is populated from `state.validation`, which mirrors the Validation tab — add an
option there, re-import, and it appears in the forms with no code change.

### Refreshing from the sheet

After a re-import, admins see a banner — **"Updated data from the Google Sheet is
ready"** → **⟳ Load sheet data** — and the same action is in ⚙ → Data. The server
fingerprints `data/seed.json` and compares it with the import it last loaded.

Loading is a **merge**, not a reset. The sheet is the source of truth for its own
records (new rows appear, edited rows update, rows deleted from the sheet go), but
what exists only in the app is kept:

- requests **submitted in the app** (re-numbered if their ID now collides)
- an **approve/decline made in the app** on a request the sheet still shows as
  Pending — if the sheet has since decided it, the sheet wins
- **members and dropdown values** added in Settings, and the planned/unplanned
  classification of reasons

One consequence: records removed with Cleanup return if the sheet still has them.
**↺ Reset to sheet data** remains the clean-slate option.

Two normalisations happen on import, both deliberate:

- `Half Day – 1nd Half` (a typo in the source) and the mojibake en-dash are
  canonicalised to `Half Day - 1st Half` / `- 2nd Half`.
- The sheet overloads **Status** with `Unplanned` for records logged after the
  fact. The app keeps planned-ness on the *reason* and reserves Status for
  workflow (`Pending` / `Approved` / `Declined`), so those rows import as
  `Approved` with an `autoLogged: true` marker — surfaced in the UI as
  "logged after the fact". Nothing is lost, and the manager queue shows a real
  pending list instead of 53 permanently-unactionable rows.

Changes are saved in the server's database, not back to the sheet.

## Settings (⚙, admins only)

Four tabs:

- **Users** — create logins (employee ID, full name, password, role, manager),
  change roles and manager assignments, deactivate, reset passwords, delete.
  People who appear in the records but have no login are listed as one-click
  prefills. Deleting a login keeps that person's leave and WFH history.
- **Data** — load the latest sheet import, export a JSON backup, and the audit log
  (who did what).
- **Dropdowns** — add or remove values for **leave reasons**, **WFH reasons** and
  **durations**, each showing how many records use it. Two guards:
  - A new leave reason must be classified **planned or unplanned** at the point
    you add it (checkbox), rather than silently defaulting. Existing reasons can
    be reclassified with the "counts as planned" toggle, which immediately moves
    their records between the four dashboard categories.
  - A duration must be `Full Day` or start with `Half Day` — the day-counting and
    half-day maths key off that prefix, so arbitrary values are rejected with an
    explanation.

  Removing a value in use never edits existing records; they keep the value, it
  just disappears from the pickers. **Statuses are fixed** — the approval
  workflow depends on exactly `Pending` / `Approved` / `Declined`.
- **Cleanup** — bulk-delete finished records older than N **days / weeks /
  months / years**. Shows a live preview (how many records, how many days, and
  the exact cutoff date) before you commit, lets you target leave and WFH
  independently, and protects pending requests by default. Irreversible, so a
  "Back up first" button sits next to it.

## Design system — "Warm Press"

All of it lives in CSS custom properties at the top of `src/css/app.css`; nothing
is hard-coded in the JS.

- **Palette** — warm paper neutrals (ivory `#f7f4ef`, ink-brown text), with
  **terracotta `#a63d1f` for Leave** and **teal `#0f6b6b` for WFH**.
- **Surfaces** — every card, KPI tile, calendar day, modal and the page ground is
  an opaque warm colour carrying `--carbon`: a tiled **fractal-turbulence fibre**
  (SVG `feTurbulence` + `feColorMatrix`, inlined as a data URI). It is randomised
  by construction — no angle, no period, nothing that can resolve into a pattern.
  Grain size is `baseFrequency` (0.34 — lower is coarser, higher is finer/sandier).

  Getting here took three passes, and the dead ends are worth recording:
  a hard-stop twill on a 12px tile read as an imposing grid; softening the ramps
  but crossing two diagonals still intersected into a visible **dot lattice**;
  a single-angle version removed the lattice but slanted lines are regular no
  matter how faint. Noise was the answer.

  The tint is always the surface's own warm hue (`feColorMatrix` bakes the colour
  and a noise-driven alpha), never grey or black — grey noise under a translucent
  veil is what dulled the ivory in an earlier pass. Mean darkening is **~4 levels**
  on ivory, and warmth is preserved (red-minus-blue 6 → 7). Felt as material,
  not seen as decoration.

  Three shadow levels (`--lift-1/2/3`) over a fixed ambient gradient; panels lift
  on hover. Backdrop-blur is reserved for the sticky top bar, where content
  genuinely passes underneath.
- **Leave categories** — planned reads calm **ochre**, unplanned reads **wine**,
  with the lighter tint of each as its half day, so the family grouping survives
  at a glance. Verified in CIE Lab: every pair among the four, plus the Leave and
  WFH identity colours, is ≥22 ΔE apart. Deliberately never teal (that means WFH)
  and never terracotta (that is the Leave identity) — an earlier brick red sat
  only 17 ΔE from terracotta and had to go. The reason-mix charts use
  palette-matched ramps per dashboard for the same reason: a shared generic ramp
  had put "Planned Leave" in teal.
- **Calendar** — a recessed tray (inset shadow) holding 42 raised, woven day
  tiles with an 8px gutter, each lifting on hover. Weekends step back with a
  flatter, warmer stock and a dashed border rather than desaturation, which would
  grey them out of the palette; out-of-month days drop to 42% and lose their shadow.
- **Type** — **SF Pro (San Francisco)**, using SF's own size split: *SF Pro
  Display* for headings and headline figures, *SF Pro Text* for interface copy.
  Weighted up throughout — 800 on uppercase labels and table headers, 700 on
  headings, nav, tabs and buttons.

  SF itself is never downloaded: it ships with Apple platforms and cannot be
  redistributed, so the stack resolves it natively via `-apple-system` /
  `BlinkMacSystemFont`, with the named `SF Pro *` families catching a local
  install on Windows.

  **On Windows you will not see SF** unless you install it. Off Apple platforms the
  stack lands on **Inter**, which was drawn in the SF idiom and is the closest
  freely-licensable match. Behind Inter sit **Segoe UI Variable** then `system-ui`
  as belt-and-braces; the variable face is listed *ahead of* `system-ui` on purpose,
  since on Windows `system-ui` resolves to plain Segoe UI and would shadow it.

  Verified by measuring rendered text width rather than trusting
  `document.fonts.check`, which reports unreliably here: the app renders at Inter
  metrics (331px for a test string) — distinct from Segoe UI Variable (306px) and
  plain Segoe UI (312px).

### Fonts are self-hosted

Inter is **SIL Open Font License 1.1**, so it ships with the app rather than being
fetched from Google Fonts. `src/css/fonts.css` holds the variable font (weights
100–900, latin + latin-ext) base64-inlined as `@font-face` data URIs; the original
`.woff2` files and the full licence are in `assets/fonts/`.

That keeps **zero outbound requests** — the single-file build works with no network
at all. Cost is ~175KB, taking the bundle from 160KB to 335KB.
- **Leave categories** — deliberately *not* on the general categorical ramp.
  Planned is the warm family, Unplanned the plum family, and the lighter tint of
  each is the half day:

  | | Full day | Half day |
  | --- | --- | --- |
  | Planned | `--bk-p` ochre | `--bk-ph` light ochre |
  | Unplanned | `--bk-u` wine | `--bk-uh` dusty rose |

  Teal never appears on the leave dashboard, so a category can't be misread as WFH.
- **Dark mode** — a warm espresso base rather than neutral black, keeping the
  same hue relationships. Follows the OS by default; the ◐ button overrides.
- Respects `prefers-reduced-motion`.

`design-options.html` is the picker these choices came from — kept for reference.

## Adding features

The app is a page registry over hash routing. To add a page:

1. Create `src/js/pages/mypage.js`:

   ```js
   (function (W) {
     var el = W.util.el;
     W.pages = W.pages || {};
     W.pages.mypage = {
       id: 'mypage',
       label: 'My page',
       render: function () { return el('div', { text: 'hello' }); },
       badge: function () { return 0; }   // optional nav count
     };
   })(window.WFS);
   ```

2. Add a `<script>` tag for it in `index.html`.
3. Optionally add its id to `ORDER` in `src/js/app.js` to place it in the nav —
   unlisted pages get appended.

`render()` returns a DOM node and may call `WFS.app.rerender()` after any state
change. Anything that mutates data should go through `WFS.store`, which persists
and notifies every subscriber.

### Layout of the code

| File | Role |
| --- | --- |
| `src/js/util.js` | Dates (working-day maths), formatting, DOM helper |
| `src/js/store.js` | State, persistence, domain rules, mutations |
| `src/js/analytics.js` | All dashboard maths — pure functions over store state |
| `src/js/charts.js` | Dependency-free SVG/DOM charts |
| `src/js/form.js` | The apply-for-leave / apply-for-WFH modal |
| `src/js/app.js` | Shell, routing, page registry, data menu |

### Swapping in a real backend

`store.adapter` is the only thing that touches storage:

```js
WFS.store.adapter = {
  load()      { /* return state or null */ },
  save(state) { /* POST to your API */ },
  reset()     { }
};
```

Replace those three methods — with a REST API, or the Google Sheets API to write
back to the original sheet — and no page code changes.

## Known limits

- Single-browser storage; there is no server, so two people don't see each
  other's edits. That's the `store.adapter` swap above.
- The "acting as" selector in the header is a role switcher for demoing, not
  authentication. Real sign-in belongs in front of the approvals page.
- Public holidays aren't modelled — only weekends are excluded from day counts,
  which is what the sheet did.
