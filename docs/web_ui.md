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

Buttons per step: QA has **QA pass / QA fail** (fail requires a note →
`qa_hold`); email steps have **Email sent** (drafts are still written to
`output/email_drafts/`, sending stays manual; the page offers the draft
as a download); hand-recorded Zenodo steps ask for the record id or
DOI + URL; everything else is a single confirm button. "Other actions"
on an open paper: change the data contact, close as exception (note
required), close as publication-only, close as archived elsewhere.

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
python -m oa_web migrate                # creates oa_web.sqlite (once; `oa web` also does it)
python -m oa_web createsuperuser        # first login (once)
oa web                                  # http://127.0.0.1:8000/
```

Run from the project root (it reads `./config.toml`, like the CLI), or
point elsewhere with `oa web --config PATH` / `OA_CONFIG=PATH`.

**More people:** sign in at `/admin/` as the superuser → Users → Add.
Ordinary users need no staff/superuser flag. `python -m oa_web
changepassword <user>` resets a password.

**Other machines:** `oa web --host 0.0.0.0` and list the names/addresses
people will type in an optional config section (Django refuses unknown
Host headers):

```toml
[web]
allowed_hosts = ["localhost", "127.0.0.1", "my-pc.cicbiomagune.es", "10.0.0.12"]
# pub_db_url_template = "https://…/{pub_id}"   # adds an "Open the publication database" link on the db_updated step
# database = "./oa_web.sqlite"
```

It is plain HTTP — fine on the internal network, not for the internet.
The serving machine needs what the CLI needs: the OneDrive sync,
`~/.zenodorc`, and DB/VPN access for Zenodo draft creation.

**Trying it safely:** copy `oa_tracker.sqlite`, `output/` and
`config.toml` to a scratch folder, set `enabled = false` under
`[zenodo]`, `[sharepoint]` and `[automation]` in the copy, and run
`oa web --config /path/to/copy/config.toml`.

## Code

`src/oa_web/`: `guide.py` (per-task wording + buttons — edit this when
the SOP changes), `tracker.py` (service layer over `oa_tracker`),
`views.py`, `templates/`, `static/oa_web/style.css`. Tests:
`tests/test_web.py` (skipped when the extras aren't installed).
