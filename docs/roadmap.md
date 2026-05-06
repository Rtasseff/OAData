# OA Archive Tracker — Roadmap

This document is the **single source of truth** for the staged automation plan.
Other docs (`summary.md`, `techSpec.md`, `sop.md`) link here rather than
restating the plan, so there is one place to update when stages, scope, or
priorities change.

---

## Staged automation plan

### Stage 1 — Folder-based tracking + reminders + operator workflow (MVP)

**Status:** shipped, in active use.

**Goal:** get reliability and visibility fast, without depending on the internal
publication DB or the Zenodo API.

- **Input:** locally synced SharePoint "Publications Data" tree (one folder per
  publication; folder name = publication ID).
- **Core logic:**
  - empty folder → `OPEN_INACTIVE` (red flag: data contact hasn't acted)
  - non-empty folder → `OPEN_ACTIVE` (activity started; triggers manual QA)
- **System of record:** SQLite registry keeps all archives (open + closed),
  including "became active" date, last change, reminders, PID/URL, notes.
- **Operator layer:** generated Action Sheet TSV listing tasks; operator marks
  tasks done; script ingests it and updates SQLite (so SQLite is never edited
  by hand).
- **Outputs:** weekly report + reminder email drafts + completion email drafts.
- **Closure:** once Zenodo is published and the internal DB updated (manually
  for now), the folder can be removed and status becomes `CLOSED_*` (records
  remain in SQLite).

### Stage 2 — Read internal publication database

**Status:** in progress — connectivity testing; **blocked on IT host-grant** (as of 2026-05-05).

**Goal:** enrich and de-risk operations, without waiting on write access.
Sequenced ahead of Zenodo automation because article-level metadata (PI,
publication DOI, etc.) is needed to meaningfully fill out new Zenodo records
and to check whether a record already exists for a publication.

- Pull PI / data-contact details, publication DOI, metadata.
- Use DB info to improve reminder targeting and email-template filling.
- Cross-check expected folders vs observed folders.

#### Progress log

**2026-05-05 — Initial connectivity test**

Confirmed:
- IT granted read access via phpMyAdmin at
  `https://intranet.cicbiomagune.es/phpmyadmin/` (user-issued credentials).
- DB host/port reachable from the WSL workstation:
  `nc -vz intranet.cicbiomagune.es 3306` succeeded
  (resolves to `10.10.3.230`, port 3306 / native MySQL protocol).
- Local tooling installed: `mariadb-client` (system), `pymysql 1.1.3` in
  `.venv/`. PyMySQL chosen for its pure-Python, zero-system-deps fit with the
  project's lightweight ethos. Neither is committed to `pyproject.toml` yet —
  they're throwaway test deps until Stage 2 design begins.
- `~/.my.cnf` (mode 600) created on the workstation with the IT-issued user
  and password. Not in the repo.

Blocked:
- `mysql -e "SELECT VERSION();"` returns
  `ERROR 1045 (28000): Access denied for user 'rtasseff'@'bmg-rtasseff.cicbiomagune.int' (using password: YES)`.
- Credentials confirmed correct (same pair works via phpMyAdmin).
- Diagnosis: MySQL **host-grant restriction**. The user account is granted
  only from the phpMyAdmin host, not from the WSL workstation. Same root
  cause as the classic "phpMyAdmin works but direct mysql doesn't" gotcha.

Next action:
- Email IT requesting a grant for the same user from the workstation host
  (`'rtasseff'@'bmg-rtasseff.cicbiomagune.int'`) or, if hostname-based grants
  are awkward, the internal subnet (`'rtasseff'@'10.10.%'`). Same SELECT-only
  privilege scope as today.
- Once the grant is in place: re-run the `mysql` smoke test, then run the
  PyMySQL test against the same credentials. After both pass, capture
  schema (database name, relevant tables, `DESCRIBE` of each) into a scratch
  note and start the Stage 2 design plan (config schema, `pub_db.py` module,
  credential handling, dep promotion to `pyproject.toml`).

Fallback if IT cannot widen the grant:
- SSH tunnel from the workstation to a whitelisted host, connect to MySQL
  via the tunnel so the connection appears to originate from the allowed
  source. Adds a separate access ask (SSH on the intermediate host) and
  is less convenient for daily use; raise direct-grant first.

**2026-05-05 — Follow-up: IT replied with DB-selection advice; tests confirm host-grant restriction**

IT response (translated): the user `rtassef` only has access to the
`publications` database; specify it explicitly when connecting (database +
username + password are the 3 params their PHP code uses).

That advice doesn't actually address the failure: in MySQL, **authentication
runs before database selection**, so a missing `database=` parameter cannot
itself produce error 1045. We tested IT's advice anyway and ran broader
diagnostics — none changed the result:

- Added `database=publications` to `~/.my.cnf` → same 1045.
- Tried both username spellings (`rtassef` per IT's email, `rtasseff` per
  the Linux account) → same 1045, same source host in the error message.
- `mysql -h 10.10.3.230 --protocol=TCP -u rtassef -p publications -e "SELECT VERSION();"`
  (IP-direct, explicit TCP, password prompted interactively to bypass any
  `.my.cnf` quoting issue) → same 1045.
- `mysql --default-auth=mysql_native_password ...` → same 1045 (rules out
  auth-plugin negotiation between MariaDB client and the server).
- Client confirmed: `mysql Ver 15.1 Distrib 10.11.14-MariaDB` — modern
  enough to support both `mysql_native_password` and `caching_sha2_password`.
- Password independently verified working via phpMyAdmin with the same
  username.

With password and username both verified, and TCP / DNS / auth-plugin /
DB-selection all ruled out, the only remaining cause is a **host-grant
restriction**: MySQL grants are per `(user, source-host)` pair, and the
account on the server is granted only from a different source (almost
certainly the phpMyAdmin server's host) — no grant matches connections
originating from `bmg-rtasseff.cicbiomagune.int`.

Next action — email IT with technical wording so the ask is unambiguous.
Suggested Spanish text:

> *Confirmé que la contraseña y el usuario son correctos (las mismas
> credenciales funcionan vía phpMyAdmin). El parámetro `database` no
> resuelve el problema porque la autenticación de MySQL ocurre antes de
> la selección de base de datos — por eso el error `1045` aparece
> independientemente del valor de `database`. El error indica la fuente
> como `'rtassef'@'bmg-rtasseff.cicbiomagune.int'`, lo que sugiere que el
> grant actual del usuario no incluye mi estación de trabajo como host
> de origen. ¿Podrían añadir un grant para `'rtassef'@'10.10.%'` (sólo
> con los mismos permisos SELECT que ya tengo en la base `publications`)?
> Eso me permitiría conectarme con el cliente MySQL nativo desde mi PC,
> en paralelo al acceso por phpMyAdmin que ya funciona.*

Asking for `@'10.10.%'` (internal subnet) rather than `@'%'` (anywhere) is
more likely to be approved on security grounds.

Stage 2 work pauses here until the grant is updated. When it is, the
remaining checklist is:

1. Re-run `mysql -e "SELECT VERSION(); SHOW TABLES;"` — confirm grant works.
2. Run the PyMySQL throwaway script — confirm the lib choice.
3. Capture schema (database name confirmed: `publications`; tables and
   `DESCRIBE` of each) into a scratch note.
4. Open the Stage 2 design plan: config schema for credentials, a
   `pub_db.py` module, dep promotion of PyMySQL into `pyproject.toml`.

### Stage 3 — Zenodo automation (start with uploads)

**Status:** not started.

**Goal:** remove the most painful manual step (large-file drag/drop and Zenodo
UI errors). Builds on Stage 2 — uses publication metadata to populate Zenodo
record fields and detect pre-existing records.

- Automate uploads via the Zenodo API first (highest ROI).
- Later expand to create/edit draft records via API.
- **Enforce the DOI/PID rule:** the dataset must get a Zenodo-minted DOI; the
  paper DOI may only be linked as a related identifier in Zenodo metadata.

### Stage 4 — Write back to internal publication database

**Status:** not started; depends on IT access.

**Goal:** close the loop automatically (best case).

- Push Zenodo DOI/URL into the internal publication DB automatically.
- Potentially auto-transition `OPEN_ZENODO_PUBLISHED` → `OPEN_DB_UPDATED` →
  `CLOSED_DATA_ARCHIVED`.

---

## Cross-cutting / open ideas

Items that don't fit cleanly into a single stage. To be expanded over time —
this is the parking lot for "build out from there".

- _(none yet — add here as ideas come up)_

---

## Progress since MVP

Things shipped beyond the original Stage 1 scope, kept here so the roadmap
reflects reality:

- `oa action <pub_id> <task_code>` — single-archive mid-week updates without
  regenerating the action sheet.
- `done=1` with `pid`/`url` — fast-track to `OPEN_ZENODO_PUBLISHED` in one row.
- `done=2` — full-closure shortcut (`CLOSED_DATA_ARCHIVED` with PID,
  `CLOSED_EXCEPTION` without).
- `oa reopen <pub_id> --reason "..."` — bring a `CLOSED_*` archive back to an
  OPEN state when a PI finally responds.
- Manual-contact final-reminder flow — replaces the last templated reminder
  with a `contact_pi_manual` row (no draft generated).
