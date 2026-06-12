# SharePoint List — demo & test runbook

Purpose: drive the full data-contact ↔ operator round trip on the **live** List
using throwaway fake publications, first to **find/fix bugs**, then to **record a
walkthrough video**. The video supplements the SOP for end users.

Tooling: `scripts/demo_sharepoint.py` (`seed` / `clear`). Demo rows live at
**PubId ≥ 990000** so they never collide with real publications.

## Safety / ground rules

- Nothing here writes under `/mnt/c/`. We only **read** the folder tree.
- **Do not run `oa scan` during the demo window.** Scan isn't needed (seed + push
  creates the List rows). If it runs against the fake folders it is *not*
  destructive, but it will (a) blank the seeded title / journal / "Required"
  labels — only data-contact, corresponding-author, and Zenodo code are
  override-protected — and (b) clutter your real worklist with mandate-missing
  rows. If a scan does blank a row, restore it with `seed --force`.
- If `oa scan` / `oa sharepoint sync` run on a schedule, **pause the scan** for the
  session (sync is fine and expected).
- The DEMO rows are visible to anyone with List access until `clear`. They're
  clearly titled `DEMO:` and obviously fake.

## The staged fake publications

Six rows spanning the pipeline so the List shows several status labels and every
user feature is exercised. **Create a SharePoint folder named exactly by each
PubId** in *Shared Documents* (so the Folder link resolves):

| PubId  | Status → List label                | Folder        | What it demonstrates / tests |
|--------|------------------------------------|---------------|------------------------------|
| 990001 | OPEN_ACTIVE → "Data uploaded — under review" | with a file | Folder link · **Suggest a new data contact** · **I think this is done** |
| 990002 | OPEN_ACTIVE → "Data uploaded — under review" (embargo 6) | with a file | **Exemption: All data deposited in another archive** (needs External PID + URL) |
| 990003 | OPEN_ACTIVE → "Data uploaded — under review" | with a file | **Exemption: Collaborative AND no biomaGUNE data or lead** · **Note** |
| 990004 | OPEN_INACTIVE → "Waiting for data" | **EMPTY**     | "Waiting for data" label |
| 990005 | OPEN_READY_FOR_ZENODO_DRAFT → "Ready to archive" | with a file | Later-stage label · DOI link (fake, 404s) |
| 990006 | OPEN_ZENODO_PUBLISHED → "Published to Zenodo" | with a file | "Published to Zenodo" label · Zenodo link (fake, 404s) |

You are seeded as **both** the data contact and the corresponding author on all
six, so both `[Me]` views light up.

---

## Phase 0 — prep (once)

- [ ] Pause any scheduled `oa scan`.
- [ ] In *Shared Documents*, create folders `990001`–`990006`. Put a dummy file in
      all of them **except `990004`** (leave that one empty).
- [ ] Seed the DB:
      ```bash
      python scripts/demo_sharepoint.py seed --email rtasseff@cicbiomagune.es --name "Ryan Tasseff"
      ```
- [ ] Push to the List:
      ```bash
      oa sharepoint sync
      ```

## Phase 1 — outbound checks (what the List shows)

- [ ] 6 DEMO rows present.
- [ ] "Me as data contact" and "Me as corresponding author" views each show all 6.
- [ ] Status labels match the table above (990004 "Waiting for data", 990005
      "Ready to archive", 990006 "Published to Zenodo", rest "Data uploaded…").
- [ ] Data archiving = "Required" on all; Embargo = 6 only on 990002.
- [ ] **Folder link** opens the matching SharePoint folder (the star of the video).
- [ ] DOI link present on 990005/990006 (fake → 404, expected); Zenodo link on 990006.

## Phase 2 — inbound feature walkthrough (find bugs)

For each, **edit the row in SharePoint → Save**, then run `oa sharepoint sync` and
inspect `output/sharepoint_proposals.tsv`. To apply a proposal: set `done=1` on its
row, then `oa apply output/sharepoint_proposals.tsv` (applied rows move to
`action_history.tsv`). The dedup signature means an unchanged edit won't re-emit;
changing the edit re-emits.

**990001 — suggest a new data contact**
- [ ] Set *Suggest a new data contact* (the Person picker) to a colleague who has
      signed into the site. Save → sync.
- [ ] Expect a `propose_data_contact` row; note says to open the List row to see who
      and to run `oa action 990001 set_data_contact --email <email> --name <name>`.
      Row's *Request status* → "Received — pending review".
- [ ] Apply the real change: `oa action 990001 set_data_contact --email <…> --name <…>`,
      then `oa sharepoint sync` — the Data contact chip updates on the List.
- The proposal now **names the suggested person and pre-fills the command**
      (`oa action 990001 set_data_contact --email <their email> --name "<their name>"`),
      resolved from the site User Information List. If the picked person has never
      signed into the site they stay unmapped and it falls back to "open the row".

**990001 — "I think this is done"**
- [ ] Tick *I think this is done*. Save → sync.
- [ ] Expect a `propose_done` row (acknowledge-only — verify the data, then close via
      the normal flow, e.g. `oa action 990001 …`).

**990002 — exemption needing evidence (the guard)**
- [ ] Set *Propose exemption* = "All data deposited in another archive", leave
      *External archive PID/URL* **blank**. Save → sync.
- [ ] Expect a `propose_exemption` row whose note says the PID/URL is missing — **no
      closure** (this is the guard working).
- [ ] Now fill *External archive PID* and *External archive URL*. Save → sync.
- [ ] Expect a `close_archived_external` row with pid + url filled. Set `done=1`,
      `oa apply …` → 990002 closes **CLOSED_DATA_ARCHIVED** with the external PID/URL.

**990003 — collaborative exemption + note**
- [ ] Set *Propose exemption* = "Collaborative AND no biomaGUNE data or lead" and type
      something in *Notes*. Save → sync.
- [ ] Expect a `close_exception` row (collaborative → CLOSED_EXCEPTION, no evidence
      required) **and** a separate `user_note` row carrying the Notes text
      (awareness-only; durable in the file, recorded to the archive's notes on apply).
- [ ] Set `done=1`, `oa apply …` → 990003 closes **CLOSED_EXCEPTION**.

**990004 / 990005 / 990006 — display only**
- [ ] No user action. Confirm labels render and that later-stage rows correctly have
      no obvious "action" for a data contact (reinforces "not every row is yours").

> After a closure the row is reconciled over two syncs (`sync_closed=false`): the
> next `oa sharepoint sync` **relabels** it to the closed status ("Done — data
> archived" / "Closed — exception") so the contact sees the outcome, and the sync
> after that **removes** it from the List. The clean recording run starts fresh.

## Phase 3 — fix bugs

- [ ] Log anything found below; I fix it; re-run the affected step until clean.

### Bug log
| # | Row/feature | Symptom | Status |
|---|-------------|---------|--------|
| 1 | 990001 suggest contact | Proposal couldn't say *who* was picked (raw LookupId) | Fixed — `resolve_user_details` names the person + pre-fills the command |
| 2 | proposal rows | `current_status` blank | Fixed — mirrors the action sheet (raw status code) |
| 3 | proposal rows | reminder fields blank / hardcoded `0` | Fixed — carried from the archive |
| 4 | suggest contact | pre-filled `--name "..."` over-quoted in the TSV | Fixed — `shlex.quote` (single quotes) |
| 5 | 990002 close | closed row went **stale** on the List (never relabeled/removed) | Fixed — `reconcile_closed_rows`: show "Done" once, then remove |
| 6 | 990003 Notes | note was stdout-only → lost on an unattended sync | Fixed — durable `user_note` row, recorded to archive notes on apply |

## Phase 4 — clean run for recording

- [ ] `python scripts/demo_sharepoint.py clear` (removes DB rows + List items).
- [ ] Re-seed + push for a pristine List (no leftover "pending review" stamps or
      half-applied closures):
      ```bash
      python scripts/demo_sharepoint.py seed --email rtasseff@cicbiomagune.es --name "Ryan Tasseff"
      oa sharepoint sync
      ```
- [ ] Record the screen walkthrough (folder link, the two `[Me]` views, suggest a
      contact, an exemption, a "done" tick, a note).

### Suggested recording script (~2 min, data-contact's view)

Before recording: sign in as the user, open the List, switch to the **"Me as data
contact"** view, bump browser zoom for legibility, close stray tabs/panels.
Don't click the DOI/Zenodo links on 990005/990006 on camera — they're fake demo
links and will 404. The **folder link** is real (use 990001).

1. **Orient (0:00–0:20).** "This is the OA Archive Tracker. This view shows only
   the publications where *you're* the data contact — one row each." Point at the
   plain-language **Status** and **Data archiving** columns.
2. **Open the data folder (0:20–0:40).** Click the **Folder** link (its text is the
   publication ID). "Click here to jump straight to the folder where the data
   lives — this is where you upload your dataset." Show it open, come back.
3. **Respond to a row (0:40–1:15).** Open a row (990003) → the short form. Walk the
   options slowly: **I think this is done**; **Propose exemption** (open the
   dropdown, pick "Collaborative AND no biomaGUNE data or lead"); **Exemption /
   done detail** (type a one-line reason); **Suggest a new data contact** (if it's
   not you); **Notes** (anything you want to tell us). Save.
4. **What happens next (1:15–1:35).** "That's it — we review what you send and
   handle the archiving and records. The status updates as it moves, and when it's
   done the row shows 'Done — data archived' and then drops off your list."
5. **Corresponding-author view (1:35–1:55, optional).** Switch to **"Me as
   corresponding author."** "If you're a corresponding author, this view shows the
   publications you're responsible for — and you can suggest who the data contact
   should be."
6. **Close (1:55–2:00).** "Questions? Contact the data team. Full steps are in the
   SOP."

## Phase 5 — teardown

- [ ] `python scripts/demo_sharepoint.py clear`
- [ ] Delete the `990001`–`990006` folders from *Shared Documents*.
- [ ] Re-enable the scheduled `oa scan`.
- [ ] (Optional) `oa scan` once and confirm no stray 99000x rows remain.
