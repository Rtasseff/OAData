# Web UI (`oa web`)

A small Django site over the same tracker the CLI drives, so the weekly
operator work can be handed to someone who never opens a terminal.
**What each action means stays canonical in [sop.md](sop.md) §7–8**; this
page covers only running the site and how it fits with the CLI.

## What it is

| Page | Shows | CLI equivalent |
|---|---|---|
| **Papers** | One row per publication: id (links to the SharePoint folder), status, pending action(s), title, data contact, corresponding author. Tabs: Open / Needs action / Closed / All, plus search. | `oa status` |
| **Actions** | Every pending action, in working order — the action sheet, live. | `oa sheet` |
| **Report** | The weekly report, live, beside the latest `oa auto` digest. | `oa report` |
| **History** | The audit trail (`events` table), newest first. | — |
| **Paper** (click an action or a title) | What to do for each pending action, the links needed (SharePoint folder, Zenodo draft, email draft download, DOI/URL to copy), and the button that records it. After a button the same page reloads showing the next step. | `oa action` |

Buttons per step: QA has **QA pass** (the system then creates the
Zenodo draft and uploads the package at the next `oa auto` run) and
**QA fail** (requires a note → `qa_hold`); email steps have **Email
sent** (drafts are still written to `output/email_drafts/`, sending
stays manual; the page offers the draft as a download); the Zenodo
review step shows the DOI/URL the system reserved and **confirm**
records them after checking the record is public (the CLI's
`zenodo_validated` auto-record path — nothing is typed). For the rare
record whose DOI or URL differs, a fold-out *Different DOI or URL on
Zenodo?* holds the same two values pre-filled and editable; its button
records them as entered, without the Zenodo check (the CLI's `pid`/`url`
on that row — see [sop.md](sop.md) §7). Everything else is a single
confirm button.

**Deposited elsewhere.** The normal workflow never asks anyone to make a
Zenodo draft by hand. Where the system would create one (QA pass, and
the create-draft step) there is a folded-away alternative, *Deposited
elsewhere — record its DOI and URL*, for a deposit made by hand or data
that lives in another repository (common in collaborations, and not
necessarily Zenodo). It applies `archived_external` (the same code the
Tracker List's "deposited externally" exemption routes to): the archive
jumps to *Published — update database* and the completion email,
institutional-DB entry and folder removal still follow as steps. True
exemptions (no data shareable / generated, consultation only) close at
once and leave only the folder deletion as an action.
The manual *record id* / *DOI + URL* inputs only appear when the system
cannot act itself (Zenodo integration off, non-numeric id); they are
pre-filled whenever the record is known.

"Other actions" on an open paper: change the data contact, close as
exception (note required), close as publication-only, close as archived
elsewhere (DOI + URL; closes at once — for when the DB entry and folder
removal are already done, i.e. the CLI's `done=2` habit).

Not in the UI (CLI only): `oa reopen`, the `reset_*` overrides,
`set_corresponding_author`, the `done=2` full-closure shortcut, applying
`sharepoint_proposals.tsv`, and running `oa auto` (cron does that).

## How it fits with the CLI

* **Same code path.** Every button calls `actions.apply_single` — the
  function behind `oa action` — so transition validation, Zenodo API
  calls and warnings are identical. The pending actions come from
  `sheet.build_rows`, the same builder `oa sheet` writes to the TSV.
* **Audit trail.** Events carry `source = "web:<username>"`, so the
  `events` table records who pressed what. No schema change.
* **Files stay in step.** After a web action the matching row leaves
  `output/action_sheet.tsv` and is appended to `action_history.tsv`
  (exactly what `oa apply` does), and if the status moved, that paper's
  other stale rows are dropped from the sheet too. Email drafts are
  regenerated (`generate_emails`) so e.g. the completion draft exists
  right after a publish is confirmed. The action sheet + `oa apply`
  keep working as before; use either.
* **Guards.** A button is refused if the action is no longer pending or
  the paper's status changed since the page was loaded (two people, or
  `oa auto`, acting at once). DOI/URL inputs are only accepted on the
  steps that ask for them, so the `done=1`+PID fast-track can't be
  tripped by accident.
* **SharePoint List.** After each button press the changed paper's row is
  pushed to the List in the background (create/patch, relabel or remove
  for closures, clear a rejected "done" tick) — the same operations as
  the `oa auto` push, scoped to one row, using the cached headless token.
  The page shows the result on reload ("SharePoint List: row updated" or
  a warning); a failed push never undoes the recorded action and the next
  `oa auto` / `oa sharepoint sync` reconciles anyway. The *pull* side
  (proposals data contacts make on the List) stays with `oa auto` /
  `sync`. Turn the push off with `sharepoint_push = false` under `[web]`.
* **Never writes to the SharePoint sync.** The QA view lists the
  folder's top-level files read-only; "folder deleted" buttons only
  record that the operator deleted it on SharePoint.
* **Separate login database.** Django's tables (users, sessions) live in
  `oa_web.sqlite`; `oa_tracker.sqlite` is only touched through
  `oa_tracker`.

## Running it

```bash
source .venv/bin/activate
pip install -e '.[web]'                 # Django, markdown, whitenoise, waitress (once)
python -m oa_web createsuperuser        # first login (once; creates oa_web.sqlite as needed)
oa web                                  # http://127.0.0.1:8000/
```

Run from the project root (it reads `./config.toml`, like the CLI), or
point elsewhere with `oa web --config PATH` / `OA_CONFIG=PATH`.

**More people:** sign in at `/admin/` as the superuser → Users → Add.
Ordinary users need no staff/superuser flag. `python -m oa_web
changepassword <user>` resets a password.

**Other machines:** `oa web --host 0.0.0.0`. Any host name/address is
accepted by default (internal network); the optional config section can
restrict that and tune the rest:

```toml
[web]
# allowed_hosts = ["localhost", "my-pc.cicbiomagune.es"]   # default: any
# pub_db_url_template = "https://…/{pub_id}"   # adds an "Open the publication database" link on the db_updated step
# database = "./oa_web.sqlite"
# sharepoint_push = true     # push each changed row to the List at once (default)
```

It is plain HTTP — fine on the internal network, not for the internet.
The serving machine needs what the CLI needs: the OneDrive sync,
`~/.zenodorc`, and DB/VPN access for Zenodo draft creation.

**WSL note:** Windows reaches a server inside WSL through `localhost`
port forwarding — but only if nothing on the Windows side already owns
that port. If `localhost:<port>` answers with a dropped connection or
the wrong app, check with `netstat -ano | findstr :<port>` in
PowerShell and pick another port (`oa web --port 8080`), or use the WSL
address (`hostname -I`) directly.

**Trying it safely:** copy `oa_tracker.sqlite`, `output/` and
`config.toml` to a scratch folder, set `enabled = false` under
`[zenodo]`, `[sharepoint]` and `[automation]` in the copy, and run
`oa web --config /path/to/copy/config.toml`.

## Code

`src/oa_web/`: `guide.py` (per-task wording + buttons — edit this when
the SOP changes), `tracker.py` (service layer over `oa_tracker`),
`views.py`, `templates/`, `static/oa_web/style.css`. Tests:
`tests/test_web.py` (skipped when the extras aren't installed).
