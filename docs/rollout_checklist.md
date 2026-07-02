# Rollout checklist — automation-max branch (written 2026-07-02)

Working checklist for taking the `automation-max` branch live, plus every
flag raised during the build that needs an operator decision. Delete this
file (or fold leftovers into the roadmap) once everything is ticked.

## 1. Sandbox validation (the gate to production)

- [ ] Create `~/.zenodorc` (mode 600: `chmod 600 ~/.zenodorc`):

  ```ini
  [zenodo]
  token = <production token>

  [zenodo-sandbox]
  token = <sandbox token — create at sandbox.zenodo.org, separate account/token>
  ```

- [ ] Run `oa auto` once in a terminal. Expected: it drafts **3272 and
      3293** (currently READY) on sandbox.zenodo.org with reserved DOIs
      and uploads their packages; digest at `output/auto_digest.md`.
      **Note:** 3293 is the parked one — if even a sandbox draft is
      unwanted, advance/close it first.
- [ ] Inspect both drafts in the sandbox UI: resource type Dataset,
      CC0 license, related work "Is published in" → paper DOI, public
      visibility, creators list sensible, description framing right.
- [ ] Optionally test the full publish path on ONE sandbox draft
      (`zenodo_validated` then `zenodo_publish` rows) — sandbox DOIs are
      fake (10.5072), safe to mint.
- [ ] Flip `config.toml` → `[zenodo] environment = "production"`.
- [ ] Merge `automation-max` into `main`.
- [ ] Schedule the cron: `crontab -e` →
      `0 7 * * 1-5 /home/rtasseff/projects/OAData/scripts/run_auto.sh`
      (WSL cron only runs while WSL is up — otherwise Windows Task
      Scheduler → `wsl.exe -d <distro> -- .../scripts/run_auto.sh`).

## 2. Decisions to confirm

- [ ] **README location (recommended + implemented: beside the ZIP).**
      One canonical place: `README.txt` as its own file next to the zip
      (uploads to Zenodo as a browsable file; QC-able in SharePoint
      without extracting). Detection stays lenient (inside-zip still
      counted) so nobody is bounced. If you agree, update the protocol
      docx: §2.4 "Include a README file" → add "upload the README.txt to
      the SharePoint folder **as its own file, next to the ZIP** (you may
      also keep a copy inside the ZIP)"; §4 upload instruction → "upload
      the ZIP file **and the README.txt**, along with the postprint".
- [ ] **Protocol docx additions** (both already reinforced by the
      automated reminder texts): ticking *"I think this is done"* on the
      Tracker is what triggers processing.

## 3. IT asks (in priority order; each is fully documented)

- [ ] **Send from the office address** — one Exchange "Send As" grant;
      the exact EAC clicks + one-line PowerShell + verification, doc-cited,
      are in `docs/email_from_office_address.md`. After IT grants it, set
      `[email] sender_email = "PublicationsData@biomagune.onmicrosoft.com"`
      in config.toml and test-send one draft to yourself.
- [ ] **Rotate the IT Zenodo token.** `tmp_zenodo_make.txt` line 211
      carries a live production Zenodo token ("biblio" account) in
      plaintext. It's gitignored here, but suggest David rotates it —
      anyone holding it can publish under the library account. (Friendly
      extra: their script interpolates `$_GET['id']` into SQL —
      injection risk in their intranet page.)
- [ ] **Trim the over-privileged Entra app** (standing item from the
      roadmap, non-urgent): remove `Sites.FullControl.All` +
      `AppRoleAssignment.ReadWrite.All`, keep `Sites.Selected` + per-site
      write; re-run `spike_sharepoint_write.py` to confirm.
- [ ] **Rotate the MariaDB password** (standing item, transcript
      exposure noted 2026-05-05 in the roadmap).

## 4. Repo cleanup (say the word / do when convenient)

- [ ] Delete `tmp_zenodo_make.txt` after the token conversation.
- [ ] Delete `tmp_webpage_*.html` (4 files, ~1.2 MB of stale scratch).
- [ ] Delete the five `spike_*.py` files — fully graduated into
      `sharepoint.py` per the roadmap.
- [ ] Decide whether `publication_archive_protocol.docx` should be
      committed (currently untracked; the canonical copy lives on
      SharePoint — consider keeping only the SharePoint link, which is
      already in config.toml as `sop_url`).
- [ ] Stale SharePoint folder `3280` still needs deleting (carried over
      from the last round — operator does this in SharePoint directly).

## 5. Later / follow-ups

- [ ] **Auto-send reminder emails** via Graph delegated `Mail.Send` on the
      existing app registration — only after the current automation has
      earned trust, and only with a tested spike first (notes + doc links
      in `docs/email_from_office_address.md` §Later). The Send As grant
      from item 3 is a prerequisite either way.
- [ ] Promote `propose_done`-driven closures further only if ever
      comfortable; current stop-point (operator validates + publishes) is
      deliberate.
- [ ] Stage 4 (write-back to the central DB) still needs an IT write
      grant; until then `db_updated` stays a manual step.
