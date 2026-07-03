# Rollout checklist — automation-max branch

**Last updated 2026-07-03.** This is the pick-up-here document: current
state first, then the exact next actions, then the longer-tail items.
Delete this file (or fold leftovers into the roadmap) once everything is
ticked.

## Where things stand

- ✅ Automation shipped on branch `automation-max` (294 tests green):
  Zenodo API drafts/uploads, auto-QC, `oa auto`, digest, docs.
- ✅ Sandbox validation ran; drafts looked right **except the Publisher
  field**, which the UI auto-fills but the API does not — **fixed
  2026-07-03** (`metadata.publisher = "Zenodo"` on every draft).
- ✅ README-location decision confirmed (README.txt beside the ZIP);
  protocol docx updated by the operator and removed from the repo (the
  live copy is on SharePoint; link = `[sharepoint] sop_url` in config).
- ✅ Zenodo tokens: operator's own tokens are in `~/.zenodorc`; IT told
  to rotate their exposed "biblio" token. IT asks (token rotation, Entra
  app trim, MariaDB password) have been handed off — **our part is done**;
  they're off this list.
- ⏳ Office-address sending: operator pursues the Send As grant next
  week — the ready-made ask is `docs/email_from_office_address.md`.
  After the grant: set `[email] sender_email` in config.toml and
  test-send one draft to yourself.

## Next actions (in order)

1. **(Optional but recommended)** one more sandbox draft to see
   Publisher filled in. Cheapest path: discard one of the sandbox test
   drafts in the sandbox UI, clear its code
   (`oa action <pub_id> reset_zenodo_code`), knock the archive back if
   needed, then `oa action <pub_id> zenodo_create_draft`. Or simply skip
   — Publisher is visible at the validation step either way, and you
   review every draft before publish.
2. **Flip to production:** `config.toml` → `[zenodo] environment = "production"`.
3. **Commit the flip, merge, push:**

   ```bash
   git add config.toml && git commit -m "Switch Zenodo to production"
   git checkout main && git merge automation-max && git push
   ```

4. **Install the cron:** `crontab -e` →
   `0 7 * * 1-5 /home/rtasseff/projects/OAData/scripts/run_auto.sh`
   (WSL cron only runs while WSL is up — otherwise Windows Task
   Scheduler → `wsl.exe -d <distro> -- .../scripts/run_auto.sh`).
5. **New weekly rhythm** (details: sop.md §8.1b): open
   `output/auto_digest.md` → validate drafts in the browser → `done=1`
   on `zenodo_validated` / `zenodo_publish` rows → `db_updated` →
   remove finished folders → send `.eml` drafts → `oa apply`.

Note for the first production runs: the sandbox drafts made for
3272/3293 stay on the sandbox (harmless). Their archives already carry
`zenodo_env = "sandbox"` codes, so under production config the engine
will NOT touch those drafts; to give them real production records, clear
the sandbox code first (`oa action <pub> reset_zenodo_code`, then let
`oa auto` re-draft) — this is exactly the env-pinning safety doing its job.

## Reading list before the first production run

1. This file (you're here).
2. `docs/sop.md` §8.1b — the `oa auto` cadence and what stays manual.
3. `output/auto_digest.md` after each run — the one operational file.
4. Reference when needed: README "Documentation map" table says which
   doc owns what.

## Repo cleanup (whenever convenient)

- [ ] Delete `tmp_zenodo_make.txt` (token conversation with IT has
      happened; the file has served its purpose).
- [ ] Delete `tmp_webpage_*.html` (4 files, ~1.2 MB of stale scratch).
- [ ] Delete the five `spike_*.py` files — fully graduated into
      `sharepoint.py` per the roadmap.
- [ ] Stale SharePoint folder `3280` still needs deleting (operator,
      in SharePoint directly — carried over from the last round).

## Later / follow-ups

- [ ] **Office-address Send As grant** (next week) →
      `docs/email_from_office_address.md`, then `[email] sender_email`.
- [ ] **Auto-send reminder emails** via Graph delegated `Mail.Send` —
      only after the automation earns trust, spike first; notes in
      `docs/email_from_office_address.md` §Later. (The old
      `Mail.Send.Shared` scope no longer exists in the permissions
      reference — re-verify at implementation time.)
- [ ] Stage 4 (central-DB write-back) still needs an IT write grant;
      until then `db_updated` stays a manual step.
