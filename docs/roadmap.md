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

**Status:** **shipped 2026-05-11**, in active use. See `docs/sop.md` for
operator procedure; the full implementation log (connectivity, schema
discovery, design refinements, and the five rollout phases A–E) is
preserved below for audit.

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

**2026-05-05 — Reproduction in PHP/mysqli (IT's own toolchain)**

IT's response framed PHP + 3 params as the canonical way to connect, so a
diagnostic in *that* toolchain carries weight that a native-client failure
might not. Installed `php-cli` + `php-mysql` on the workstation and ran:

```php
$db = new mysqli("intranet.cicbiomagune.es", "rtassef", $pass, "publications");
```

(Password supplied via env var, both `rtassef` and `rtasseff` username
spellings tested.)

Result — identical to the native `mysql` client failure:

```
mysqli_sql_exception: Access denied for user 'rtassef'@'bmg-rtasseff.cicbiomagune.int'
                       (using password: YES)
```

This is significant because it eliminates "you're using a tool we don't
support" as a possible IT response: PHP + `mysqli` is the same driver
phpMyAdmin sits on top of, called with the exact 3 params IT recommended.
The failure is reproducible in IT's preferred environment, with the source
host identified explicitly in the error message — strong evidence for the
host-grant diagnosis when re-engaging IT.

Stage 2 work pauses here until the grant is updated. When it is, the
remaining checklist is:

1. Re-run `mysql -e "SELECT VERSION(); SHOW TABLES;"` — confirm grant works.
2. Run the PyMySQL throwaway script — confirm the lib choice.
3. Capture schema (database name confirmed: `publications`; tables and
   `DESCRIBE` of each) into a scratch note.
4. Open the Stage 2 design plan: config schema for credentials, a
   `pub_db.py` module, dep promotion of PyMySQL into `pyproject.toml`.

**2026-05-05 — Unblocked: IT confirmed and added the host grant**

IT reproduced the failure on their side, confirmed the host-grant theory,
and added a grant for the user from `bmg-rtasseff.cicbiomagune.int`. The
PHP-toolchain reproduction was decisive — once IT could see the same
failure with `mysqli`, they took the diagnosis seriously.

Confirmed working:

```
mysql -h intranet.cicbiomagune.es -u rtasseff -p publications -e "SELECT VERSION(); SHOW TABLES;"
```

Returns rows. Server version: `5.5.68-MariaDB`. Database name confirmed:
`publications`. Many tables visible (`OldNews_*`, `RIAP_*`, …) — full list
to be captured in the schema-discovery step.

Username clarification: the real account is `rtasseff` (matching the Linux
account name). IT's original email had a typo (`rtassef`, one `f`); their
host-grant fix was applied to the correct `rtasseff` account.

Server version note for design: MariaDB 5.5 is end-of-life; auth plugin is
`mysql_native_password` (the only option in 5.5, so no negotiation
concerns); default charset is utf8 (3-byte), not utf8mb4. PyMySQL handles
all of this without special configuration. SQL feature limits (no CTEs,
no window functions, no JSON functions) don't affect the simple SELECTs
Stage 2 will need.

**Action item — rotate the DB password.** During the troubleshooting
sequence the password was pasted into the assistant transcript once.
Coordinate with IT to issue a new one before the project module starts
relying on it. Until then, treat the current password as compromised
relative to chat-transcript history (it is not in the repo or in any
committed file).

**2026-05-05 — PyMySQL connectivity + initial schema discovery**

PyMySQL connects against the live grant using
`pymysql.connect(read_default_file=os.path.expanduser("~/.my.cnf"), user="rtasseff")`.
Reading the option file means the password never lands in repo files or
in shell history; `user=` is overridden explicitly because IT's email
typoed the username.

Initial schema landscape:

- 550 tables in `publications` (IT was right — *muchas tablas*).
- ~50 match publication / user / contact keywords.
- Core tables identified for Stage 2 (full schemas in scratch file):
  - `publication` (2418 rows) — main publication record. Includes
    `id`, `accession_number`, `publi_datacode`, `doi`, `title`,
    `author`, `id_journal`, `journal`, `year`, `oa_id_project`,
    `goldOAfee`, `publi_datacode`, audit columns.
  - `publicationRequest` (10 rows), `publicationRequestProject`,
    `publicationRequestCostCenter` — looks like a request workflow,
    very few rows; may be peripheral.
  - `publication_task` (897 rows), `publication_task_status` (3),
    `publication_task_type` (4) — *another workflow tracker inside
    the publication DB itself*. Worth investigating: does this
    overlap with our own OA workflow, or is it for a different purpose?
  - `journal` (604), `journal2` (272), `scopus_journal_def` (31137) —
    journal metadata incl. `open_access`, `embargo`, `repository`,
    `embargoTime`. Useful for OA compliance checks.
  - `mdm_personal` (1477 rows) — personnel master data: `id`, `name`,
    `empleado`, `start_date`, `end_date`, `id_department`, etc. Likely
    the canonical researcher/PI directory. `users` (only 2 rows) is
    *not* it — that's an app-admin table.
  - `copi_projects` (374), `project_pi` (0 rows — empty), `org_contact`
    (1130) — relationship/contact tables.
- Sensitive-data note: schema (column names + types) is captured to
  `~/oa-stage2-notes.md` (outside the repo). No row data has been
  pulled into chat or the scratch file beyond `COUNT(*)`. Sample-row
  pulls are deferred until we agree on what's safe to surface (titles
  and DOIs are public; some columns like `users.password` clearly are
  not).

Open questions before Stage 2 design begins (answered/refined below):

1. ~~**Join key**~~ — confirmed: `publication.id` matches the SharePoint
   folder name (verified with publication 3097).
2. **PI / data-contact path** — refined: `project_publis` (4136 rows)
   is the real M:N publication↔project link, not `oa_id_project` (NULL
   for ~52% of rows). PI on the project is `project.id_pi` (probably
   `mdm_personal.id`).
3. **Parallel task systems**: there are *two* publication task tables
   (`publi_task` 635 rows, `publication_task` 897 rows) and a central
   `publi_email` log (232 rows) and a central `repo_publis` (1988 rows
   with repository deposit + open-access + embargo info). The
   project-office system has substantial functional overlap with our
   tracker but isn't 1:1. Decision deferred until design phase: do we
   read these tables as ground-truth signals, ignore them, or
   eventually consolidate?

**2026-05-05 — IT lifecycle explanation + verified join path + data-quality reality check**

IT provided a written explanation of the project lifecycle (full Spanish
original captured in `~/oa-stage2-notes.md`). Key conceptual model:

> `funding_agency` → `cff_funding` (call for funding) → `cff_oaMandate`
> (the OA requirement attached to the call) → projects request funding;
> the project inherits the call's OA mandate; publications are linked to
> projects, and so the OA requirement for a publication is *derived* from
> its project's funding source.

The 5 OA mandates are (confirmed from `cff_oaMandate`):

| id | type | implication for our work |
|---|---|---|
| 1 | `Yes OA: 0 months, DATA` | data archiving required at publication |
| 2 | `Yes OA: 6 months, DATA` | data archiving required, ≤6mo embargo |
| 3 | `Yes OA: 6 months` | only the article, ≤6mo embargo — *no data deposit required* |
| 4 | `No OA` | nothing required — *should this even be in our tracker?* |
| 5 | `Yes OA: 0 months, DATA, OA Journal Cost` | strictest: + only OA journals |

That's a substantial design input — Stage 2 can derive **per-publication
whether data archiving is even required, and the maximum embargo
period**, instead of treating every publication as needing data deposit.

Verified join path (using publication 3097 as a real test case):

```
publication.id → project_publis.id_publi → project_publis.id_project →
  project.id → project.id_funding → cff_funding.id →
    cff_funding.id_oa_mandate → cff_oaMandate.type
```

**Real-world data quality is messy** — Stage 2 must handle gracefully:

- Some `cff_funding` rows have `id_oa_mandate = NULL` (publication 3097's
  Merck call has no mandate set).
- Some `project.id_funding` values point to non-existent `cff_funding`
  rows (orphaned FK; pub 3097's project 1424 has `id_funding=98` which
  doesn't exist).
- Free-text `project.funding_agency` can disagree with the joined
  `cff_funding.funding_agency` (pub 3097's project 351 says "AXA
  Foundation" but joined cff_funding 103 says "Merck"). Trust the FK,
  not the free text.
- One publication can map to multiple projects (pub 3097 has 2). Each
  project may have a different mandate, or a missing mandate. Need a
  policy: probably "use the strictest mandate found across all linked
  projects; fall back to OPEN_INACTIVE behavior if none can be derived".

Tables IT named that don't exist with those names: `call_type`,
`proposal`, `reports`. May be in another DB or have different names.
Need to confirm with IT before relying on any of them.

Other parallel-tracker tables found (functional overlap with our SQLite
tracker — to be evaluated in Stage 2 design, not now):

- `publi_email` (232 rows) — central email log with `sentDate, sentBy`.
- `repo_publis` (1988 rows) — central repository-deposit tracking with
  `repository_code, open_access, embargo_time`.
- `publication_task` (897 rows) and `publi_task` (635 rows) — *two*
  central task systems for the same domain.
- `publi_corr_auth` / `publi_first_auth` — authoritative author links.

Remaining checklist:

- [x] Confirm `mysql` CLI works after grant.
- [x] Run the PyMySQL throwaway script — confirmed.
- [x] Capture full table list and candidate-table schemas to scratch
      file outside the repo (`~/oa-stage2-notes.md`).
- [x] Confirm join key — `publication.id`.
- [x] Trace the PI/data-contact path with one sample lookup —
      `project_publis` is the M:N link; `project.id_pi` and
      `project.id_user` reach personnel via (presumably) `mdm_personal`.
- [x] Capture IT's lifecycle explanation and the 5 OA mandate values.
- [ ] Verify `mdm_personal.id` is what `project.id_pi` references (one
      sample lookup) — needed before we read names/emails from the DB.
- [ ] Confirm with IT what `proposal`/`reports`/`call_type` are (or
      ignore them as Stage-2-out-of-scope).
- [ ] Rotate the DB password with IT (transcript exposure earlier).
- [ ] **Open the Stage 2 design plan** with these inputs:
  - config schema for credentials (read from `~/.my.cnf` style file
    outside the repo, *not* in `config.toml`);
  - `src/oa_tracker/pub_db.py` with helpers like
    `lookup_publication(pub_id)`, `derive_oa_mandate(pub_id)` that
    handle NULL mandates and orphaned FKs;
  - dep promotion of PyMySQL into `pyproject.toml`;
  - mocked-connection test fixtures (CI cannot reach this DB);
  - explicit policy for: multi-project publications, missing mandates,
    `No OA` mandate publications, and how to surface the
    central-tracker overlap (`publi_email`, `repo_publis`, both task
    tables) without duplicating their work.

### Stage 2.5 — Automate Zenodo repo creation (interim, before uploads)

**Status:** design captured 2026-05-30 in `docs/zenodo_design.md`,
all design decisions locked the same day (license CC0-1.0, no
community, default keywords `["CIC biomaGUNE"]`, abstract-based
description with attribution framing, sandbox-first rollout, all
authors via `publication.author_with_affiliation` parse with a
fallback path). Only remaining gate is the Zenodo personal access
token, expected from operator on return to keyboard.

**Goal:** automate creation of empty Zenodo draft records (metadata only,
no large-file upload yet). Stage 2 currently produces a "Zenodo cheat
sheet" (`output/zenodo_cheat/<pub_id>.txt`) the operator copies into
the Zenodo UI by hand; Stage 2.5 calls the Zenodo API with the same
consolidated metadata and records the returned record id into
`zenodo_code` automatically.

This is split out from Stage 3 so the higher-risk file-upload work
(slow, large, error-prone) can ship later. Creating an empty record
is cheap and reversible.

See `docs/zenodo_design.md` for the full design — API surface, metadata
mapping, module structure, configuration, error-handling policy, and
the operator decisions that gate implementation.

### Stage 3 — Zenodo automation: uploads

**Status:** design captured 2026-05-30 in `docs/zenodo_design.md`
alongside Stage 2.5 (same module, same configuration, shared
client). Implementation follows once Stage 2.5 is validated against
the sandbox.

**Goal:** remove the most painful manual step (large-file drag/drop and Zenodo
UI errors). Builds on Stages 2 and 2.5 — uses publication metadata to
populate Zenodo record fields and pushes the actual data files.

- Automate uploads via the Zenodo API (new Files API, 50 GB/file).
- File discovery + naming policy (default: flatten with collision
  detection; per-archive override to pre-zip if structure matters).
- Local upload manifest under `output/zenodo_uploads/{pub_id}/` for
  idempotent resume on partial uploads.
- **Enforce the DOI/PID rule:** the dataset must get a Zenodo-minted DOI; the
  paper DOI may only be linked as a related identifier in Zenodo metadata.
- Publish stays operator-confirmed indefinitely — minting a DOI is
  permanent and has the highest safety value for verification. See
  `docs/zenodo_design.md` § *Promotion path*.

### Stage 4 — Write back to internal publication database

**Status:** not started; depends on IT access.

**Goal:** close the loop automatically (best case).

- Push Zenodo DOI/URL into the internal publication DB automatically.
- Potentially auto-transition `OPEN_ZENODO_PUBLISHED` → `OPEN_DB_UPDATED` →
  `CLOSED_DATA_ARCHIVED`.

---

## Parallel track — User-facing interaction layer (SharePoint List)

**Status (as of 2026-06-02):** design fully captured (2026-05-20 through
2026-05-31) — architecture, interaction design, action-routing policy,
IT-ask shape, full spike log. IT approved the dedicated-app-registration
ask on 2026-05-22 and scheduled implementation for the week of
2026-05-25. App registration delivered and verified on 2026-06-01
(client ID received, device-code auth + delegated `Sites.Selected`
consent both confirmed by the spike). **2026-06-02 — WRITE CONFIRMED; parallel track is technically unblocked.**
After several failed manual attempts (deprecations, wrong role), IT got the
per-site grant working by adding `Sites.FullControl.All` +
`AppRoleAssignment.ReadWrite.All` to the app to enable the grant;
`spike_sharepoint_write.py` then created a list + item on the live site
(HTTP 201 each). **Follow-up (non-urgent hygiene):** the app is now
over-privileged — its delegated tokens carry tenant-wide
`Sites.FullControl.All` and `AppRoleAssignment.ReadWrite.All`, not the
one-site `Sites.Selected` of the least-privilege design. When the IT
relationship recovers, trim the app back to `Sites.Selected` + the per-site
write grant and re-test (this also confirms whether write rides on the clean
per-site grant vs. the broad FullControl). Work now moves to step 3 (list
schema design) → build of `src/oa_tracker/sharepoint.py`. Runs in parallel
to the staged automation plan, not blocking any stage.

**Goal:** give data contacts, PIs, and group members a single place to see
the publications biomaGUNE is processing, the publications for their
group, and the publications where they are listed as data contact — with
enough interactivity to register a few high-value signals back to the
tracker (propose exemption, mark "I think this is done", request
data-contact reassignment).

**What is actually built (no auth detail):**

- A regular SharePoint List on the existing PublicationsData site —
  the same kind of list anyone makes from "New → List" in SharePoint.
  Nothing custom-hosted. One row per publication.
- Columns split into two groups:
  - *System-owned*, kept in sync by the OA tool: publication id, title,
    current pipeline status, assigned data contact (Person), PI / group
    (Person), folder link, SOP link, mandate flags.
  - *User-editable*, filled in by users in the SharePoint UI:
    `proposed_done` (Yes/No), `exemption_reason` (text),
    `proposed_data_contact` (Person), notes.
- Per-user views via SharePoint's built-in view filters
  (`AssignedDataContact = [Me]`, `Group = [Me]`). No per-user code on
  our side.

**Who touches what:**

- *End users (data contacts, PIs)* open the list in their browser, same
  as any other SharePoint content. They install nothing. They edit
  user-editable columns directly in SharePoint.
- *The OA tool* (Python CLI on the operator workstation) is a sync
  engine. On each run it pushes system-owned columns out to the list
  (create/update rows) and pulls user-edited columns back. User edits
  surface as action-sheet rows for operator review — never auto-applied.

**Where Microsoft Graph fits:** Graph is the API the OA tool uses to
talk to SharePoint (create/update/query list items). Users never touch
Graph; they only see the SharePoint list.

**Motivation:** every signal in or out is currently email-mediated, and
operator overhead grows with archive count. People act surprised when
reminded of outstanding tasks because the only visibility they ever get is
the reminder itself. A simple list view they can check on their own would:

- replace "did you know you have an outstanding archive?" with self-service
  status lookup;
- replace "email me if you think this should be exempt" with a structured
  exemption proposal;
- replace recurring "are you done?" check-ins with a user-driven done flag;
- give a place to assign / reassign data contacts without an operator
  round-trip.

### Why a SharePoint List (and not the alternatives)

- **Intranet web page** — requires IT to allocate hosting; the political
  cost is high and resolutions are slow and restrictive. Avoid unless
  cheaper paths fail.
- **SharePoint Page with rendered markdown** — static, not interactive;
  cannot capture user input.
- **Power Apps / Power Automate** — tenant restrictions likely block
  additional service consents; not a path we control.
- **External web app (GitHub Pages, self-hosted Flask, etc.)** — auth and
  data-sensitivity concerns; cannot show user-scoped views without us
  building a login system.
- **SharePoint List** — already inside the tenant, no new hosting,
  interactive column types out of the box, per-user filtered views without
  code, scriptable from the terminal via Microsoft Graph. Best match for
  our constraints.

### Approach

- One SharePoint List in an existing site, columns include: publication
  id, title, mandate flags, current pipeline status, assigned data
  contact (Person), PI / group (Person), SharePoint folder hyperlink,
  protocol hyperlink, plus user-editable fields (`proposed_done`,
  `exemption_reason`, `proposed_data_contact`).
- A new CLI command (`oa sharepoint sync` or similar) pushes current
  archive state out (full overwrite of system-owned columns) and pulls
  user-edited fields back into SQLite.
- User-edited fields route through the action sheet at the start (no
  auto-apply on any signal class until it has been operator-validated).
  See *Action routing* below for the promotion policy.
- Auth: Microsoft Graph via device-code flow, refresh token stored in
  `~/` outside the repo. Fallback if user-consented app registration is
  blocked: PnP PowerShell on Windows invoked from WSL, reusing the
  existing user login.

### Interaction design — zero-click access for users

End users will not learn filter UI and will not read SOPs. The adoption
budget is one click past a bookmark. SharePoint can hit that with its
built-in `[Me]` filter token:

- Single list view filtered `AssignedDataContact = [Me] OR Group = [Me]`.
  The token resolves at render time, so one view serves everyone and
  each person sees only their own rows.
- Make that the **default view** of the list. The bookmark is just the
  list URL with no query parameters; opening it lands the user on
  their personalized slice. Zero clicks.
- One-line description at the top of the list (replaces the SOP):
  *"These are publications where you are the data contact or where
  your group is involved. Click a row to see details or send a signal."*
- Secondary view for group-wide visibility (PI use case). Not
  advertised in the UI; only used by people who go looking.
- Reminder and completion email templates should include deep links to
  the user's specific row in the list
  (`?FilterField1=PubId&FilterValue1=<id>`), so the inbox path to the
  right row is also one click.

### Action routing — action sheet first, automate as comfort grows

All user-edited fields surface as action-sheet rows for operator review
at the start. Nothing auto-applies on the first round of any new signal
class. This is the same validation posture used during Stage 2 (mandate
derivation): a new input source can carry bugs, edge cases, or
misunderstandings that have not yet been exposed by real data, so the
operator manually verifies each class of change before it is automated.

**The action-sheet route is itself the automation deliverable for this
phase.** Before this work, every signal from a data contact arrived via
email and required the operator to translate it into a sheet row by
hand. After this work, the same signal lands as a structured row with
no operator transcription. That is real automation — the final state
change still going through `oa apply` does not make the work less
automated, it makes it more conservative.

Promotion to auto-apply is the expected endpoint for each signal class,
not a maybe. Once the operator has seen a class of signals behave
correctly across realistic edge cases (a few weekly cycles, no
surprises), that class gets promoted to auto-apply on the next sync.
Candidates in likely promotion order:

1. *Reassign data contact* (Person column) — unambiguous instruction,
   easily reversed if wrong. Probably first to promote.
2. *Mark exemption* with a closed-list Choice (categories TBD) —
   closed list forces the reason into a recognized bucket. "Other /
   needs explanation" stays operator-routed permanently because free
   text cannot be auto-applied safely.
3. *I think this is done* (Yes/No) — last to promote, if ever. Closing
   an archive is the most irreversible step in the pipeline;
   operator-verification has the highest safety value here.

Free-text "Notes" never auto-applies — surfaced in sync output for
operator awareness, no state change.

### Why polling, not a push trigger

The "user clicks a field, action happens within seconds" experience
would need Power Automate flows or webhook subscriptions on the list.
The operator has a Power Automate license and has built flows there
before, but only via the Power Automate GUI — CLI access has been
denied. Maintaining flows through the GUI is slow and brittle compared
to keeping everything under OA-CLI control. Polling via scheduled
`oa sharepoint sync` runs (~15-minute cadence) is the chosen model:
near-real-time from the user's perspective, fully under CLI control,
no additional IT involvement. Power Automate stays available as a
specific-purpose GUI tool if a single high-value flow ever earns its
keep.

### Sequenced steps

1. **Auth spike (gates everything).** From the workstation, attempt
   device-code auth against Microsoft Graph with the smallest possible
   scope (e.g. `Sites.Read.All` against one test site). One short script.
   If the tenant blocks user-consented app registration, fall back to a
   PnP PowerShell spike. If both fail, raise the IT ask explicitly and do
   not proceed with design.
2. **Choose the host site.** Reuse an existing site if possible (no new
   IT involvement); otherwise request one. Capture which site and how
   access is scoped.
3. **List schema design.** Lock the columns, types, and which are
   system-owned vs user-editable. Decide the per-user view filters
   (`AssignedDataContact = [Me]`, `Group = [Me]`, etc.).
4. **Minimal sync module (`src/oa_tracker/sharepoint.py`).** Read-only
   first: pull list state into a scratch JSON so we can diff against
   SQLite without writing anything. Manual review before any push.
5. **Write path.** Push system-owned columns from SQLite into the list.
   Idempotent — re-runnable, no duplicates.
6. **Pull path.** Read user-edited fields back, emit action-sheet rows
   (`propose_exemption`, `propose_done`, `propose_reassign_data_contact`,
   etc.). Operator applies via the existing `oa apply` flow.
7. **Rollout.** Pilot with one group (PI + a couple of data contacts),
   iterate on column choices and views, then announce more broadly. SOP
   update once stable.

### Risks / open questions

- **Tenant auth restrictions.** Single biggest unknown. The spike result
  decides whether the whole track is feasible without an IT fight.
- **List size limits.** SharePoint lists handle thousands of items but
  views have a 5000-item threshold by default. Closed archives may need
  to be hidden from the active view (kept in SQLite as today) to stay
  under it.
- **Two-way edit conflicts.** Operator changes via `oa action` happen
  between syncs. Treat SharePoint edits as proposals, never as
  authoritative; operator wins on conflict.
- **Person columns vs free-text names.** Need to resolve internal user
  identities. The `mdm_personal` → Graph user mapping may be imperfect;
  design must tolerate unmapped names.
- **Sensitive fields.** Decide explicitly what does *not* go into the
  list (internal notes, reminder history, mandate-derivation
  diagnostics). Default: only fields a non-operator user would want or
  could act on.

### Progress log

**2026-05-20 — Auth spike: device-code feasibility against Microsoft Graph**

Goal of the spike: confirm whether we can authenticate against Microsoft
Graph from the workstation using a first-party Microsoft public client,
**without** asking IT to register a custom app — and whether the tenant
will grant the scopes we need.

Target site for the read test: `https://biomagune.sharepoint.com/sites/PublicationsData`.

Throwaway script: `spike_sharepoint_auth.py` at the project root. Uses
`msal` (installed into `.venv/` but not yet promoted into
`pyproject.toml` — same throwaway pattern as `pymysql` during the Stage 2
connectivity work). Device-code flow, read-only, never writes anything.

Three first-party Microsoft public clients were tested:

| Client | Client ID | Scope | Result |
|---|---|---|---|
| Microsoft Graph Command Line Tools | `14d82eec-204b-4c2f-b7e8-296a70dab67e` | `User.Read` | works |
| Microsoft Graph Command Line Tools | `14d82eec-204b-4c2f-b7e8-296a70dab67e` | `Sites.Read.All` | admin-consent required (`AADSTS65001`) |
| Azure CLI | `04b07795-8ddb-461a-bbee-02f9e1bf7b46` | `Sites.Read.All` | Microsoft-side preauthorization block (`AADSTS65002`) — Azure CLI is not authorized by Microsoft to request Graph SharePoint scopes, independent of tenant policy. Dead end regardless of IT. |
| Microsoft Graph Explorer | `de8bc8b5-d9f9-48b1-a8ad-b748da725064` | `Sites.Read.All` | not provisioned in tenant (`AADSTS1001010` — missing service principal). Nobody in the org has ever signed into Graph Explorer, so the app does not exist in our directory. |

**Diagnosis:**

- Device-code auth itself works in this tenant — the `User.Read` flow
  completes cleanly with the Graph PowerShell client.
- The block is **per-scope**, not per-client: the tenant requires admin
  consent for `Sites.Read.All`, and the only Microsoft-first-party
  client that can request Graph SharePoint scopes *and* is enrolled in
  our tenant is Graph PowerShell.
- No zero-IT path remains via public Microsoft clients.

**2026-05-21 — Pivot: dedicated app registration, not a Microsoft public client**

Initial plan was to ask IT to admin-consent `Sites.Selected` on the
existing `Microsoft Graph Command Line Tools` public client
(`14d82eec-204b-4c2f-b7e8-296a70dab67e`). Two problems came up before
sending the ask:

1. **Deprecation optics.** The Graph CLI binary that shares that
   client's display name is being retired by Microsoft: deprecation
   began 2025-09-01, full retirement on 2026-08-28
   (`https://devblogs.microsoft.com/microsoft365dev/microsoft-graph-cli-retirement/`).
   The underlying Entra app is shared with the Graph PowerShell SDK and
   continues to exist, but the *display name* an IT reviewer sees
   ("Microsoft Graph Command Line Tools") matches the soon-retired
   product. A cautious admin will Google it and see "retirement."
2. **Shared identity is not auditable.** Consent on a Microsoft-owned
   public client applies to everyone in the tenant who uses that
   client — there's no per-tool audit boundary.

Both problems vanish if we register our own app. The IT ask becomes
strictly narrower and cleaner to defend, and we control the lifecycle.

**The IT ask (English; translate for sending):**

Create a single-purpose Entra ID app registration named e.g.
`OA Archive Tracker`. Single-tenant, no redirect URI (it is a CLI),
public-client flows enabled (for device-code authentication). Grant
admin consent for the **delegated** Graph permission
**`Sites.Selected`**, then grant the app the **`write`** role on
**only** `https://biomagune.sharepoint.com/sites/PublicationsData`.

Write covers create/update/delete on list items and the list itself
on that one site, and includes read — it is one grant covering both
the tool's outbound sync (push state into the list) and inbound sync
(read user-edited columns back). No follow-up ask is needed for the
standard operation; only an elevation to `manage` / `fullcontrol`
would be a future ask, and we are not planning that.

Security framing for the cautious admin:

- Delegated, not application-only: the tool runs under the operator's
  own corporate identity via device-code flow. No client secret, no
  certificate, no service account, no stored password. Refresh token
  lives in the operator's home directory and dies with the operator
  account.
- `Sites.Selected` by itself grants the app access to **no sites at
  all**. The app can only touch the sites IT explicitly attaches it
  to in the per-site grant. Revocation is instant: deleting that one
  per-site permission cuts off all access, no app removal needed.
- `write` is the second-lowest per-site role (above `read`, below
  `manage` and `fullcontrol`). It does not grant administrative
  rights on the site, permission management, or settings changes.

**Do not mention `Sites.Read.All` / `Sites.ReadWrite.All` to IT in
either the email or the conversation.** Naming the broader scopes
gives the admin an alternative anchor and risks them defaulting to
"easier to consent tenant-wide" thinking. Keep the entire
conversation on `Sites.Selected` + one site.

The audience for this ask is the MS-tenant IT contact (different
person from the MariaDB/database IT contact from Stage 2). This
contact has historically been categorically restrictive on MS tenant
permissions, so the ask must lead with the architecture, not with the
spike journey, and must not reference the wider `Sites.Read.All` scope.

Fallbacks if the dedicated app registration is denied:

1. Tenant-wide admin consent for `Sites.Read.All` on the Graph
   PowerShell client (broader, deprecated display-name issue, worse
   security posture — only as a last resort).
2. PnP PowerShell on Windows invoked from WSL. Recent PnP versions
   also require admin consent on their own client, so this is not
   guaranteed to bypass anything — but it uses the SharePoint REST
   surface directly and has a separate consent profile worth testing
   if Graph is fully denied.
3. Drop the SharePoint-List approach entirely and revisit the
   alternatives in the section above.

Stage decision until IT responds: parallel-track work pauses at step 1.
Stages 2.5 / 3 / 4 can proceed independently.

**2026-05-22 — IT approved the ask**

The MS-tenant IT contact agreed to the 2026-05-21 ask (dedicated Entra
app registration, delegated `Sites.Selected`, per-site `write` grant on
PublicationsData). Implementation scheduled for the week of 2026-05-25.
Parallel-track work resumes on sequenced step 3 (list schema design)
while waiting for the app's client ID and the per-site grant to be in
place; steps 1 and 2 become trivial once we receive those.

**2026-06-01 — Client ID received; auth + consent verified; per-site grant still pending**

IT delivered the app's client (application) ID:
`fbd87ebd-fd7e-4e4c-967a-9932bde32da1`. Ran the spike against it with the
delegated `Sites.Selected` scope:

```
.venv/bin/python spike_sharepoint_auth.py \
  --client-id fbd87ebd-fd7e-4e4c-967a-9932bde32da1 \
  --tenant biomagune.onmicrosoft.com --scope Sites.Selected
```

Confirmed:

- App registration exists and device-code auth completed → public-client
  flows are enabled (satisfies the 2026-05-21 ask's "public client flows
  enabled" requirement). No client secret was involved — settles the
  confidential-vs-public-app question for good; none is needed.
- Delegated `Sites.Selected` is admin-consented — token returned
  `Scopes granted: openid profile Sites.Selected email`.

Still blocked:

- `GET /sites/biomagune.sharepoint.com:/sites/PublicationsData` → HTTP 403
  `accessDenied`. Textbook signature of "`Sites.Selected` is consented but
  the app has no role on this specific site yet": the per-site grant from
  the 2026-05-21 ask has not been applied. `Sites.Selected` alone grants
  access to no sites — the app must be explicitly attached to PublicationsData.

Next action — one remaining IT micro-task: attach the app to the
PublicationsData site with the `write` role. PnP one-liner:

```
Connect-PnPOnline -Url https://biomagune.sharepoint.com/sites/PublicationsData -Interactive
Grant-PnPAzureADAppSitePermission `
  -AppId fbd87ebd-fd7e-4e4c-967a-9932bde32da1 `
  -DisplayName "OA Archive Tracker" `
  -Site https://biomagune.sharepoint.com/sites/PublicationsData `
  -Permissions Write
```

Graph equivalent: `POST /sites/{site-id}/permissions` (site-id from
`GET /sites/biomagune.sharepoint.com:/sites/PublicationsData`) with body
`{"roles":["write"],"grantedToIdentities":[{"application":{"id":"fbd87ebd-fd7e-4e4c-967a-9932bde32da1","displayName":"OA Archive Tracker"}}]}`.

On a green run (HTTP 200 + "Lists readable on site"), wire the client ID
into `config.toml` and resume at step 3 (list schema design). Holding off
on committing the client ID to `config.toml` until that green run, so an
unverified value never gets baked in.

**2026-06-02 — Per-site grant diagnosed (PnP is a dead end); access model settled by direct test**

IT tried the 2026-06-01 PnP grant command and hit repeated errors. Root
cause: Microsoft retired the **PnP Management Shell** sign-in app on
2024-09-09, so current PnP.PowerShell `Connect-PnPOnline -Interactive`
now requires its own registered client ID — an unrelated rabbit hole. The
per-site grant itself is fine; the *tool* was broken.

Corrected a wrong assumption from the prior round: the granting admin does
**not** need to be a site owner. Per the official API reference
([Create permission](https://learn.microsoft.com/en-us/graph/api/site-post-permissions)),
the delegated requirement is a token carrying **`Sites.FullControl.All`**
plus an admin role ("SharePoint Administrator or higher" — Global Admin
qualifies). A Microsoft Q&A matching our exact symptom ("Access Denied
Despite Global Admin Rights") confirms a `Sites.Selected`-only token cannot
make the grant; the granting token must carry `Sites.FullControl.All`.

Corrected method handed to IT (scope unchanged — still `write` on only
PublicationsData), every line traceable to a Microsoft Learn page:

```
Install-Module Microsoft.Graph -Scope CurrentUser
Connect-MgGraph -Scopes "Sites.FullControl.All"
$siteId = (Invoke-MgGraphRequest -Method GET `
  -Uri "https://graph.microsoft.com/v1.0/sites/biomagune.sharepoint.com:/sites/PublicationsData").id
$params = @{ roles = @("write"); grantedToIdentities = @(@{ application = @{
  id = "fbd87ebd-fd7e-4e4c-967a-9932bde32da1"; displayName = "OA Archive Tracker" } }) }
New-MgSitePermission -SiteId $siteId -BodyParameter $params
```

`Connect-MgGraph` (Microsoft.Graph.Authentication) is the documented
sign-in cmdlet; `-Scopes` = "an array of delegated permissions to consent
to". `New-MgSitePermission` (Microsoft.Graph.Sites) is the API reference's
own PowerShell example. Refs:
- https://learn.microsoft.com/en-us/powershell/module/microsoft.graph.authentication/connect-mggraph
- https://learn.microsoft.com/en-us/graph/api/site-post-permissions

**Access model settled by direct empirical test (not theory):**

- `spike_onedrive_write.py` — wrote a file to the operator's own OneDrive
  via Graph with delegated `Files.ReadWrite` (no admin consent needed).
  HTTP 201; file visible at the `biomagune-my.sharepoint.com` URL. Proves
  terminal→Microsoft 365 writes work end to end. (OneDrive for Business is
  a SharePoint site collection; the drive `root` == the "Documents"
  library, which explains why the returned web path showed `Documents`.)
- `spike_sharepoint_auth.py --scope Sites.ReadWrite.All` (graph-cli client,
  delegated) → "Need admin approval." Consistent with the 2026-05-20
  `Sites.Read.All` result. Confirms (twice now) that **every `Sites.*`
  scope requires one admin consent in this tenant** — there is no
  terminal-only route to the shared List. Documented asymmetry:
  `Files.ReadWrite.All` (delegated) needs no admin consent and reaches
  SharePoint document libraries the user can access, but NOT list items;
  list items require a `Sites.*` scope.

**Decision:** stay on the narrow scope IT already approved
(`Sites.Selected`, `write` on one site) and fix only the *tool*
(PnP → Graph PowerShell). The broad one-click alternative (delegated
`Sites.ReadWrite.All` + a single "Grant admin consent") was considered and
set aside: it deletes the per-site-grant step but is a tenant-wide write
grant — a poor ask to a restrictive admin right after selling
least-privilege. App-only application permission stays available as the
eventual option for unattended cron if the delegated re-auth cadence
becomes a nuisance.

**Next action:** send IT the corrected Graph PowerShell method (with both
doc links). On success, verify with
`spike_sharepoint_auth.py --client-id fbd87ebd-fd7e-4e4c-967a-9932bde32da1 --scope Sites.Selected`
→ expect HTTP 200 + "Lists readable on site", then wire the client ID into
`config.toml` and start step 3 (list schema design).

**2026-06-02 (later) — UNBLOCKED: per-site grant confirmed live**

IT reported hitting "a deprecated function (April 2026)" with the suggested
approach and instead applied the per-site grant via a **manual
configuration change** on his side (exact method not shared). The spike
confirms access:

- `spike_sharepoint_auth.py --client-id fbd87ebd-… --tenant biomagune.onmicrosoft.com --scope Sites.Selected`
- Token acquired with `Sites.Selected`; `GET /sites/biomagune.sharepoint.com:/sites/PublicationsData` → **HTTP 200** (was 403 for days). Site id:
  `biomagune.sharepoint.com,e3683750-8c69-4d61-b138-122d58f5bc8b,736e32a7-2293-4105-b8f5-738a4ab6ba0c`.

Footgun recorded: the app is **single-tenant**, so the spike must be run
with `--tenant biomagune.onmicrosoft.com`. Without it the script falls back
to the `organizations` (multi-tenant) endpoint and fails at device-flow
init with `AADSTS50059: No tenant-identifying information` — before any
sign-in. (Plan: default the spike's tenant so this can't recur.)

Open item (not a blocker): `GET /sites/{id}/lists` returned `[]`. Most
likely just that our List does not exist yet (creating it is the next build
step) and the default query may not surface stock libraries; to be
confirmed when we create and query our own list. The verified client ID is
**not yet committed to `config.toml`** — that happens when
`src/oa_tracker/sharepoint.py` is built, so the SharePoint config schema is
designed in one coherent pass.

**Next:** step 3 (list schema design) → read-only sync prototype (step 4,
`src/oa_tracker/sharepoint.py`).

**2026-06-02 (later still) — READ confirmed, WRITE denied (403)**

`spike_sharepoint_write.py` (delegated `Sites.Selected`, single-tenant
authority): `GET site` → 200, but `POST /sites/{id}/lists` (create list)
→ 403 and `PUT` a file to the library root → 403 `accessDenied`. The app
effectively has read-only on the site right now.

Two candidate causes, both a single step to fix:

1. The manual grant set the per-site role to `read` instead of `write`.
   Fix: IT sets the role to `write` (Graph `roles:["write"]`, or
   `Grant-PnPAzureADAppSitePermission -Permissions Write`).
2. The operator's own account is read-only on PublicationsData — delegated
   `Sites.Selected` is capped by the signed-in user's own rights ("the
   application can never exceed the current user's permissions"), so writes
   fail even if the app role is `write`. Fix: a site owner gives the
   operator Edit/Contribute.

Useful clue: even the plain file write failed (not just list creation), so
this is a true read/write denial, not merely a missing "manage lists"
right. Reading the grant's role via API (`GET /sites/{id}/permissions`)
needs `Sites.FullControl.All`, which our `Sites.Selected` token lacks — so
the cheap discriminator is whether the operator can create a list / upload
a file on PublicationsData in the browser: yes → cause 1 (fix the app
role); no → cause 2 (fix the operator's site permission).

**Next:** run the browser discriminator → make the precise one-role ask to
IT. Step 3 (list schema design) can proceed meanwhile.

**2026-06-02 (resolved diagnosis) — app role is `read`; needs `write`**

Operator confirmed he owns the PublicationsData site and edits it via the
GUI routinely → cause #2 (operator read-only) is ruled out. With the user
side uncapped and read working but write denied, the app's per-site role is
necessarily `read`. IT's manual change attached the app correctly but with
the wrong role. Note: site ownership alone does NOT let the operator fix
this — changing an app's Sites.Selected role needs a tenant admin role
(`Sites.FullControl.All`), so it goes back to IT.

Precise ask sent to IT: bump the app's role on PublicationsData from `read`
to `write` (GUI permission level → Write/Edit, or the same
`New-MgSitePermission … roles=@("write")` Graph command already documented
on 2026-06-02). Verify with `spike_sharepoint_write.py` → expect
`*** WRITE CONFIRMED ***`.

**2026-06-02 (DONE) — WRITE CONFIRMED on the live site**

IT's fix (his words: "something missing when granting write access… need to
add approlegrant to allow Graph to grant new permission") — i.e. he added
`AppRoleAssignment.ReadWrite.All` (and `Sites.FullControl.All`) to the app
so Graph would let him assign the per-site role. `spike_sharepoint_write.py`
result: `GET site` 200, `POST /sites/{id}/lists` **201**,
`POST …/items` **201** — a list + item created on the live PublicationsData
site. End-to-end terminal→SharePoint read AND write is proven on the real
shared site. (Nine months / three projects of MS-tenant access work closed.)

**Over-permissioning to clean up later** (non-urgent; do NOT reopen the IT
loop now — operator's bandwidth with the MS-tenant contact is strained): the
delegated token returns `AppRoleAssignment.ReadWrite.All … Sites.FullControl.All
Sites.Selected` — far broader than the intended one-site `Sites.Selected`.
Under delegated sign-in the blast radius is bounded by the operator's own
identity (no app-only secret), so it is hygiene, not an incident — but the
refresh token in `~/` now effectively carries FullControl over all
SharePoint. When IT has bandwidth: remove `Sites.FullControl.All` and
`AppRoleAssignment.ReadWrite.All`, leave `Sites.Selected` + the per-site
`write` grant, and re-run this test. Pass → least-privilege achieved and the
per-site grant confirmed correct. Fail → the per-site role still needs to be
`write` (but the tooling now works).

Housekeeping: delete the throwaway "OA Write Test (safe to delete)" list from
the site. The verified client ID goes into `config.toml` when
`src/oa_tracker/sharepoint.py` is built.

**2026-06-02 (verified) — write independently confirmed by API read-back**

Operator initially could not see the test list in the SharePoint web UI
(the home page / left nav showed no lists). Diagnosed as expected behavior:
a list created via the Graph API is not pinned to the site navigation —
it only appears under *Site contents*. Settled it the way the project
requires (evidence, not assertion): re-ran
`spike_sharepoint_auth.py --client-id fbd87ebd-… --tenant biomagune.onmicrosoft.com --scope Sites.Selected`,
which enumerated the site's lists and returned "OA Write Test (safe to
delete)". The write is real and persisted; the earlier `[]` list result
was simply pre-creation. Housekeeping unchanged: delete that test list
from *Site contents* in the browser.

**2026-06-02 — step 3 captured: list schema design**

`docs/sharepoint_list_design.md` written and decisions locked in the same
operator review. Highlights:

- **Model the corresponding author, not the PI.** The responsible party
  for a paper is, by tradition, the corresponding author (often the PI,
  not always). Decisive practical point: the CA is **already cached** from
  the central DB (`publi_corr_auth → center_user`), so it needs *no new
  enrichment* — unlike a PI column, which would need `project.id_pi` work.
  PI/group is not modeled. The CA gets its own `[Me]` view and is the
  expected (not enforced) party to reassign the data contact.
- **Effective-CA override.** Many papers have an external (non-biomaGUNE)
  corresponding author → the DB field is blank → the row is orphaned (no
  CA view; surfaces in a data-contact view only once a contact is set).
  New `set_corresponding_author` / `reset_corresponding_author` CLI
  overrides + a `corresponding_author_overridden` column let the operator
  pin a biomaGUNE person on those rows. Auto-deriving a biomaGUNE PI from
  the WoS author list is out of scope for v1 (too fuzzy).
- **Exemption categories locked** (four structured buckets + "Other"):
  *all data archived elsewhere* (PID+URL → closes as **archived** via the
  new `close_archived_external`, counting in "data archived" totals), *no
  data shareable (sensitivity)* → exception, *no data generated* →
  publication-only, *collaborative, no biomaGUNE data/lead/archive* →
  exception. "Other" stays operator-routed forever.
- Also locks: column set (system-owned / user-editable / sync-internal),
  friendly status-label mapping, identity resolution keyed on the
  `center_user` institutional email with an always-populated text
  fallback, two single-column `[Me]` views (data-contact default +
  corresponding-author), `propose_*` action routing + promotion order,
  SQLite v3 additions (`sharepoint_item_id`, `sharepoint_synced_at`,
  `corresponding_author_overridden`), the `[sharepoint]` config block, and
  the `sharepoint.py` module layout.

Empirical unknowns flagged for the step-4/5 prototype (one-row checks, not
blockers): **writing a Person column value via Graph** (generally needs the
user's site LookupId, not just an email), the `@cicbiomagune.es` ↔ Entra UPN
correspondence, and Hyperlink/Choice write payload shapes.

**Next:** step 4 (read-only sync prototype in `src/oa_tracker/sharepoint.py`)
— pull the list to scratch JSON and diff against SQLite, writing nothing,
while validating the Person-write mechanism on one row.

**2026-06-02 — local scaffolding landed + first visible push spike**

Pure-local scaffolding (no IT/sign-in), all tests green (**184 passed**):

- `db.py` → schema **v3**: `sharepoint_item_id`, `sharepoint_synced_at`,
  `corresponding_author_overridden` (+ v2→v3 migration; migration test).
- `status.py` → new task codes: `propose_data_contact` / `propose_exemption`
  / `propose_done` (acknowledgment-only for now — the sync module gives them
  real routing), `close_archived_external` (wildcard any-OPEN →
  `CLOSED_DATA_ARCHIVED`, requires PID+URL), and CLI overrides
  `set_corresponding_author` / `reset_corresponding_author`.
- `actions.py` → `close_archived_external` apply logic (intercepts *before*
  the PID fast-track so an external PID isn't mistaken for a Zenodo publish;
  hard-requires PID+URL and an OPEN status), `propose_*` ack-only handling,
  and the CA-override functions mirroring `set/reset_data_contact`.
- `scanner.py` → honors `corresponding_author_overridden` on rescan (mirrors
  `data_contact_overridden`); `cli.py` wires the two new override codes into
  `oa action`.

First visible test: **`spike_sharepoint_push.py`** (throwaway, project root).
Reads the local tracker (read-only) and projects every OPEN archive (17 today)
onto a SharePoint List named "OA Archive Tracker (preview — safe to delete)"
on PublicationsData. Idempotent (matches on PubId). **Deliberately uses
text/choice/number/boolean columns only — NO Person columns and NO hyperlink
columns** — so the first visible run can't trip on the two unproven payload
shapes (Person-field LookupId write, hyperlink write). Corresponding author /
data contact appear as the design's always-populated text fallback; the
orphaned-external-CA case (e.g. pub 3204) correctly shows a blank CA with a
populated data contact. Run: `.venv/bin/python spike_sharepoint_push.py`.
Validating the Person-write mechanism + `[Me]` views is the next increment,
folded into the step-4 module build.

**2026-06-02 (later) — Person-column write CONFIRMED; last unknown retired**

`spike_sharepoint_person.py` populated two `personOrGroup` columns on the
preview list. Mechanism (grounded in MS docs, then verified live): PATCH
`…/items/{id}/fields` with `{"<Column>LookupId": "<siteUserId>"}`, where the
id is the user's row in the site's hidden **User Information List**
(readable via `GET /sites/{id}/lists?$filter=displayName eq 'User Information
List'` → `/items?$expand=fields`, email in `fields.EMail`, row `id` = the
LookupId). Result: resolved contacts render as real **person chips** with
working hover-cards; the User Information List was readable with the delegated
`Sites.Selected` token (no SharePoint REST / `ensureUser` fallback needed).
Tolerate-unmapped confirmed in practice: `TBD` data contacts, external
corresponding authors, and anyone who hasn't signed into the site stay
text-only (the name column still shows them) — exactly the design's fallback.

**All SharePoint technical unknowns are now retired**: list create, item
create/patch, read-back, and Person-field write all proven on the live site.
The only remaining piece for a working self-service list is the `[Me]` view,
which is configuration, not an unknown — Graph v1.0 has no clean view-create
endpoint, so it's a browser step now (List settings → Views → filter
`Data contact (person) = [Me]`); the production module can script it via the
SharePoint REST `views` endpoint (needs a SharePoint-audience token, distinct
from our Graph token — to validate when the module is built, not a blocker).

**Next:** create the `[Me]` view (browser), then graduate the spikes into
`src/oa_tracker/sharepoint.py` (steps 4–6: provisioning + idempotent push +
pull → `propose_*` action rows).

**2026-06-03 — outbound module shipped (`src/oa_tracker/sharepoint.py`)**

The spikes are now a real, tested module (steps 4 + 5). **199 tests pass**
(15 new, all network-free via an in-memory `FakeGraph`). Landed:

- `GraphClient` — delegated device-code auth with a **persisted MSAL token
  cache** (`~/.oa_sharepoint_token.json`), so scheduled runs reuse the
  refresh token instead of re-prompting; `request()` retries 429/5xx;
  `resolve_users()` reads the User Information List → email→LookupId.
- Column registry (single source for provisioning + field mapping), pure
  mappers (`status_label`, `data_archiving_label`, `build_system_fields`,
  `diff_against_list`), and orchestration (`ensure_list`, `fetch_items`,
  `push_archives`) — all client-injected and unit-tested.
- Idempotent: matches rows on PubId (create vs patch); provisioning skips
  existing columns. Push is **resilient** — a per-row failure becomes a
  warning, not an aborted sync (covers the one field type not yet
  live-verified, Hyperlink: DOI/Zenodo/SOP/Folder use the documented
  `{Url,Description}` shape, to confirm on the first real run).
- `config.py` → `[sharepoint]` settings (`SharePointSettings`); the verified
  **client ID is now wired into `config.toml`** (the step the roadmap
  reserved for module-build time). No secret in config — only the token
  cache in `~/`.
- CLI: `oa sharepoint provision` (idempotent list+columns) and
  `oa sharepoint sync [--read-only]` (read-only diffs to
  `output/sharepoint_state.json`, writing nothing).

The module provisions the **real** list `OA Archive Tracker` (distinct from
the throwaway `…(preview — safe to delete)` spike list — that can be
deleted). Person columns + `[Me]` views carry over from the proven spike
mechanism. Folder column stays blank until `folder_url_template` is set in
config (local `/mnt/c` paths aren't web links).

Operator review (2026-06-03) tightened the UX before any rollout, on the
principle that users won't read and a long form reads as noise:

- **Person columns are display-hidden plumbing.** They exist only to power
  the `[Me]` filter (a view can filter on a hidden column); displayed views
  show only the plain-text `…(name)` columns, so each identity appears once.
- **The edit form shows only user-editable fields** (browser config) — the
  short set a user can act on, nothing else.
- The reassign field is renamed **"Suggest a new data contact"** (named as
  an action, the single unambiguous place to change the contact).
- The collaborative exemption is reworded to a conjunction —
  **"Collaborative project AND no biomaGUNE data or lead"** — so it can't be
  a catch-all every collaborator self-selects.
- Bug fix: `oa sharepoint sync --read-only` no longer provisions; it diffs an
  existing list and writes nothing (tells you to run `provision` if absent).

These apply to the real `OA Archive Tracker` list created by `provision`; the
throwaway preview list still carries the old labels and can be deleted.

**Next:** the inbound **pull path** (step 6) — read user-edited columns,
diff against `IngestedSig`, emit `propose_data_contact` / `propose_exemption`
/ `propose_done` action-sheet rows, and write `RequestStatus` back. That's
the half that closes the loop into `oa apply`; it's the next focused chunk.

**2026-06-03 (later) — pull path shipped; loop closed end to end**

Step 6 done. `oa sharepoint sync` is now bidirectional: push system columns,
then pull user edits into reviewable action rows. **213 tests pass** (+14).
Landed in `sharepoint.py`:

- `pull_proposals(items, name_for)` (pure) — reads user-editable columns,
  dedupes against the per-row `IngestedSig` hash (so a 15-min poll doesn't
  re-surface the same edit), and emits `Proposal`s. **Exemptions route by
  category to a concrete, already-proven closure** so applying them really
  closes: *no data generated* → `close_publication_only`; *sensitivity* /
  *collaborative* → `close_exception`; *archived elsewhere* (with PID+URL)
  → `close_archived_external`. Missing evidence falls back to a
  `propose_exemption` row that asks the user for the PID/URL instead of
  closing. `propose_done` and `propose_data_contact` are informational rows
  (the latter carries the exact `oa action … set_data_contact` command in
  its note) — operator-confirmed, not auto-applied, per the promotion policy.
- `write_proposal_feedback` stamps `IngestedSig` (+ user-visible
  `RequestStatus = "Received — pending review"` when actionable) back on the
  row, closing the user's feedback loop.
- CLI: `oa sharepoint sync` appends proposals to
  `output/sharepoint_proposals.tsv` (standard action-sheet format) →
  operator reviews, sets `done=1`, runs `oa apply output/sharepoint_proposals.tsv`.
  `--read-only` reports the count of waiting proposals and writes nothing.

Verified by integration tests through the real seam: a user's exemption
choice on the list → proposal row → `apply_actions` → the archive actually
transitions (`CLOSED_PUBLICATION_ONLY`, and `CLOSED_DATA_ARCHIVED` with the
external PID recorded). **The SharePoint track is now functionally complete
for a pilot**: people interact with the list, their signals arrive as
structured operator-reviewed rows instead of email, and confirmed signals
drive real state changes.

**Next (rollout, not new mechanics):** live `provision` → `sync` on the real
list, configure the hidden-Person view + trimmed form once, pilot with one
group, then schedule `oa sharepoint sync` (~15 min). Promotion of individual
`propose_*` classes to auto-apply happens per the policy once each behaves.

**2026-06-03 — first live `provision` hit a 400; fixed (column types)**

Auth + token cache + list create + the first 8 columns all worked live; the
column loop then 400'd. Confirmed via the Graph docs that `indexed`/`hidden`
are valid on create (not the cause) — the failure was the first column of a
type the spikes never exercised: `hyperlinkOrPicture` (the link columns), with
`dateTime` (Last updated) also unverified. Fix: those columns are now plain
**text** (URLs as strings) — the registry uses only spike-proven facets
(text/choice/number/boolean/personOrGroup). Provisioning is now resilient and
diagnostic — per-column failures are caught and re-raised with Graph's own
error body and the offending column name, so any future facet issue is
precisely fixable instead of a bare 400. `build_system_fields` also tolerates
a partially-provisioned list (skips fields whose column is absent). Real
clickable hyperlink columns are deferred as a later polish (verify the facet
in isolation first). Re-run `oa sharepoint provision` — it skips the 8 columns
already created and adds the rest as text. **213 tests pass.**

**2026-06-03 — folder link wired; hyperlink-column limit confirmed**

Confirmed via MS Q&A/docs that **Graph cannot create or write SharePoint
Hyperlink columns** (long-standing limitation — 400/invalidRequest), which is
also what the provision 400 was. Accepted workaround, which fits our text
columns: keep the URL in a text column and apply one-time **JSON column
formatting** in the browser to render it as a clickable link (persists across
syncs). The `folder_url_template` is now set in `config.toml` to the real
Shared Documents folder URL (`…/Shared%20Documents/Forms/Standard%20View.aspx?id=…/{pub_id}`,
verified against pub 3117), so `oa sharepoint sync` populates the Folder
column per row; the same column-formatting trick works for DOI/Zenodo.
Showing the list in the **left nav (Quick Launch)** is a one-time browser
step (List settings → navigation = Yes) — Graph v1.0 has no site-navigation
API. Both are config/browser, not provisioning, so re-running never undoes
them.

**2026-06-04 — provision 409 on re-run; hidden-column trap fixed**

Re-running `provision` 409'd on `IngestedSig` (`nameAlreadyExists`). Root
cause: it had been created **hidden**, and Graph's columns API does not
return hidden columns — so the idempotency check couldn't see it and tried to
re-create it. The same invisibility would have broken the pull-path dedup
(the sync couldn't read `IngestedSig` back either). Fix (214 tests):
`IngestedSig` is no longer created hidden (hide it via view config like the
Person columns); `ensure_list` treats a 409 as a benign already-exists skip;
and the column-name map is now built from the registry and overlaid with the
live list (`resolve_names`), so it's complete even for columns the API omits
and still corrects any Graph name munging. Because the *existing* list still
carries the hidden `IngestedSig`, the clean fix is to delete that list (no
real content yet) and re-provision fresh — yielding a non-hidden, reliably
readable `IngestedSig`.

**2026-06-08 — demo/test scaffolding for an end-to-end List walkthrough + video**

The list is live with the right views/form (operator set those up in the UI),
but the operator isn't a data contact anywhere, so there's nothing to click.
Goal: drive the full round trip on the live List with throwaway fakes — first to
find/fix bugs, then to record a walkthrough video that supplements the SOP.

Added `scripts/demo_sharepoint.py` (`seed` / `clear`) plus
`docs/sharepoint_demo_runbook.md` (the staged walkthrough + bug log). Design:

- **Six staged rows** spanning the pipeline (OPEN_INACTIVE → OPEN_ACTIVE ×3 →
  OPEN_READY_FOR_ZENODO_DRAFT → OPEN_ZENODO_PUBLISHED) so the List shows several
  status labels and every user feature is exercised. Operator is seeded as both
  data contact and corresponding author, so both `[Me]` views populate.
- Demo rows live at **PubId ≥ 990000** — never collide with a real publication,
  and `clear` finds them unambiguously (DB rows + events **and** live List items).
- They flow through the *real* push (`get_open_archives` → `push_archives`), so
  the exercise validates production code, not a mock.
- **Real folders now in play.** The operator controls SharePoint and will create
  folders `990001`–`990006` so the **Folder link is clickable in the video**.
  Confirmed via `pub_db.enrich_archive` (pub_db.py:300): an unknown PubId returns
  blank `CachedPubFields` (no raise) → `oa scan` is **non-destructive** on fakes,
  but it *does* overwrite the seeded title/journal/data-required labels (only
  data-contact / corresponding-author / Zenodo code are override-protected) and
  adds mandate-missing clutter to the real worklist. Mitigation: don't scan
  mid-demo; `seed --force` restores blanked rows.
- No `/mnt/c/` writes anywhere — we only read the tree.
- Person columns resolve only if the seeded email is the operator's institutional
  `…@cicbiomagune.es` (passed via `--email`) and that address has signed into the
  site (User Information List).
- Pure scaffolding: standalone script (not production CLI surface), reusing
  `db`/`config`/`sharepoint`. 214 tests still pass (no module changes).

**2026-06-10 — walkthrough fixes to the pull path (proposal rows)**

First live round of the walkthrough (990001 "suggest a new data contact")
surfaced three things, now fixed (216 tests):

- **Names the suggested person.** A picked Person column comes back as a numeric
  LookupId; the proposal couldn't say *who*. Added `GraphClient.resolve_user_details`
  (LookupId → name/email, from the same User Information List read, refactored into
  `_read_user_info`) and threaded it into `pull_proposals`. The
  `propose_data_contact` note now names the person and pre-fills
  `oa action <pub> set_data_contact --email … --name "…"`. Tolerate-unmapped:
  a person who's never signed into the site falls back to the old "open the row".
- **`current_status` populated.** The pulled proposal rows left `current_status`
  blank. `cli.py` sync now mirrors `sheet.py:_row` — `current_status` = the raw
  pipeline code (e.g. `OPEN_ACTIVE`; the List shows the friendly label for it),
  pulled from the DB archive.
- **Reminder fields carried from the archive.** `first_seen_at`,
  `next_reminder_at`, `reminder_count` are now filled from the archive (were
  blank / hardcoded `0`). They exist only so the file is a drop-in for
  `oa apply` — kept for action-sheet consistency, as the operator confirmed.
- **Pre-filled command is copy-paste clean.** The `--name "..."` had
  double-quotes, which the TSV writer escaped into a `""..."""` thicket. Switched
  to `shlex.quote` (single quotes) so the raw proposals file carries a directly
  pasteable command and still handles names with spaces/apostrophes.

Verified the full data-contact round trip live on 990001: suggestion pulled with
the named person, `set_data_contact` applied, List chip flipped to the new
contact (and 990001 dropped out of the operator's "Me as data contact" view, as
intended), then reset back to the operator.

**2026-06-10 (later) — closed-row reconcile (stale-row bug)**

The exemption walkthrough (990002 → `CLOSED_DATA_ARCHIVED`) exposed that
`push_archives` only touches OPEN archives, so a row that closes just goes
**stale** — it kept showing "Data uploaded — under review" forever, and
`sync_closed=false` ("keep closed off the list") was never actually enforced
(the push has no delete path). Operator chose **"show Done once, then remove."**

Added `sharepoint.reconcile_closed_rows` (+ `ReconcileResult`), wired into
`oa sharepoint sync` after the pull. It uses the row's own Status field as the
state marker — no extra bookkeeping:

- row still shows an open label → relabel to the closed label (both modes);
- already shows the closed label and `sync_closed=false` → delete it;
- `sync_closed=true` → relabel and keep.

Rows with no matching archive are left untouched (never delete a row we don't
recognise); an `oa reopen` makes the archive OPEN again so the push re-adds it.
`cli` looks up only the non-open PubIds actually on the List. `config.toml`
comment updated; `FakeGraph` gained DELETE; 219 tests pass (3 new). Verified
live on 990002: relabel to "Done — data archived" on one sync, removed on the next.

**2026-06-10 (later still) — user notes made durable (`user_note` task code)**

990003's walkthrough looked like "Notes didn't surface." The diagnostic
(`scripts/diag_sharepoint_fields.py`) proved the field was read fine and the
signature already matched — i.e. the note **had** been ingested and the
`User notes (awareness only)` line printed on the emission sync. The real bug
was asymmetry: proposals persist to `sharepoint_proposals.tsv` (a file) while
notes were a one-time **stdout** echo — so a scheduled/unattended sync would
lose user notes entirely. Fix: added a `changes_status: False` `user_note` task
code (status.py + validate_transition + the ack-only branch in actions.py); the
sync now emits a durable `user_note` row for any free-text note, recorded to the
archive's notes on apply. `ProposalDetail` already rides inside its proposal's
note. 220 tests pass (1 new). Added `scripts/diag_sharepoint_fields.py`
(read-only field dumper) for future field-mapping debugging.

**2026-06-11 — email templates point at the Tracker; email overhaul**

Reminder + completion drafts now link the **OA Archive Tracker** (data-contact
view) and a `To:` line, and the reminder lists the self-service actions
(upload / done / propose exemption / suggest a different contact).

Follow-on overhaul the same day (226 tests pass):

- **Reminder suppression.** `oa emails` now holds a reminder when an un-applied
  Tracker response sits in `output/sharepoint_proposals.tsv`
  (`pending_response_pubs`) — we don't nag someone awaiting our reply. Resumes
  once the row is applied/declined (`oa apply` moves it to history).
- **Folder URL unified.** Emails now build the folder link from the same
  `config.sharepoint.folder_url_template` the List uses (was a separate
  hardcoded `AllItems.aspx` base; legacy base kept as fallback).
- **Links + sender moved to config.** `[sharepoint] sop_url` (protocol, also the
  List SOP column) and `tracker_url`; new `[email]` section with
  `sender_name`/`sender_title`/`sender_email`. Email module constants are now
  fallbacks only. Signature is `${sender_name}`/`${sender_title}` in templates.
- **Corresponding author cc'd** on completion notices (`${cc_line}`), only when
  the CA differs from the data contact.
- **`.eml` drafts.** New `[email] draft_format` = `eml` | `txt` | `both`
  (config.toml ships `eml`; dataclass default stays `txt` so direct-`Config`
  callers/tests are unchanged). `.eml` files carry real To/Cc/Subject(/From)
  headers via `email.message.EmailMessage`.

  >>> ROLLBACK (email format): edit `config.toml` → `[email]` →
  >>> `draft_format = "txt"`. That restores the exact prior plain-text drafts.
  >>> ("both" emits .txt and .eml together — useful while testing .eml.)

  CAVEAT to verify in Outlook: double-clicking an `.eml` may open it
  **read-only** rather than as a composable draft (Outlook behaviour varies).
  If so, either use Outlook's *Actions → Resend* on the opened message, or roll
  back to `txt`. Not yet tested live — operator is testing next round.

**2026-06-12 — reminders span the author-owned phases; cheat sheet gains a DOI slot**

First live operational round surfaced a reminder-scoping bug and a couple of
small gaps (231 tests pass):

- **Reminders cover OPEN_INACTIVE *and* OPEN_ACTIVE; stop at QA pass.** Previously
  `oa emails` drafted a reminder for *any* `OPEN_*` archive with a due date, so
  publications already past QA (`OPEN_READY_FOR_ZENODO_DRAFT`+) got "please upload"
  nudges even though the ball was in the operator's court. New `_REMINDER_STATUSES
  = {OPEN_INACTIVE, OPEN_ACTIVE}` gates the reminder loop. Rationale (operator):
  authors who drop an incomplete/unusable file and never return still need
  reminding through OPEN_ACTIVE; only once QA passes is the remaining work
  (Zenodo/DB) operator-owned. NB: this is the *opposite* of the "only
  OPEN_INACTIVE" narrowing briefly floated — OPEN_ACTIVE must keep getting
  reminders.
- **Phase-aware reminder text.** New `${status_note}` placeholder
  (`_reminder_status_note`): OPEN_INACTIVE → "nothing uploaded yet"; OPEN_ACTIVE →
  "we see files but it doesn't look complete/packaged — finish per protocol or
  tell us it's final."
- **Cheat sheet "Our Zenodo DOI".** New `${zenodo_doi}` line, derived as
  `10.5281/zenodo.<code>` when a code is set, blank otherwise (the DOI is the PID
  we collect and never lives in the central DB). Blank line is a labeled scratch
  space during the manual mint.

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

Stage 2 shipped (2026-05-11):

- `pub_db.py` reads the central MariaDB publication DB on every scan,
  derives `oa_paper_required` / `oa_data_required` / `max_embargo_months`
  via `cff_oaMandate` ∪ Spanish AEI 2022+ project_code pattern.
- 19 new columns on `archives` cache the derived flags plus
  operator-managed `data_contact_*` and `zenodo_code` fields.
- Action sheet emits mandate-aware rows: `mandate_missing`,
  `close_publication_only` with auto-note (No-OA), paper-only auto-note
  on standard rows; reminders suppressed when data isn't required.
- Reminder + completion templates updated with `${publication_title}`,
  `${oa_status}`, `${flags}`, `${data_contact_email}`, etc.
- New `oa emails`-generated Zenodo cheat sheet
  (`output/zenodo_cheat/<pub_id>.txt`) for archives at any
  Zenodo-draft status.
- New CLI overrides: `oa action <pub> set_data_contact/reset_data_contact/
  set_zenodo_code/reset_zenodo_code`.
- New weekly-report section: "Mandate Issues — confirm with PO/IT"
  plus inline mandate labels on per-archive entries.
