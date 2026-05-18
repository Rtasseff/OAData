# Mandate classification — how we decide if a publication needs data

This document is the **single source of truth** for how the OA Tracker
determines whether a publication has an Open-Data mandate that triggers
our workflow. It is the answer to "why is publication X flagged as
`mandate_missing` / `data_required` / etc."

When you find a publication where our classification disagrees with the
intranet edit page (`/research_database/admin/publication/publi_bootgrid/edit.php?id=X`),
this is the doc to update.

## Where the logic lives

`src/oa_tracker/pub_db.py` — specifically:

- `_classify_project_signal(mandate_id, project_code)` — per-project rule.
- `derive_oa_requirement(conn, pub_id)` — aggregates the per-project
  signals into the publication-level flags written to the `archives`
  table.

The scanner (`scanner.py`) calls `enrich_archive` on every active
archive on every `oa scan`. The flags it writes are then consumed by
`sheet.py` (action sheet), `emails.py` (reminder suppression + cheat
sheet), and `report.py` (Mandate Issues section).

## Per-publication query

For each publication ID, we run exactly one query against the central
`publications` database:

```sql
SELECT pp.id_project AS proj_id,
       p.project_code AS project_code,
       cf.id_oa_mandate AS mandate_id
  FROM project_publis pp
  LEFT JOIN project p      ON p.id  = pp.id_project
  LEFT JOIN cff_funding cf ON cf.id = p.id_call
 WHERE pp.id_publi = %s
```

`project_publis` is the many-to-many table that links a publication to
its associated projects. We join `cff_funding` via `project.id_call`
(the FK to the specific call the project applied to), **not** via
`project.id_funding`. The `id_funding` column on the `project` table
is empirically unreliable: it's often orphaned (no matching
`cff_funding` row) or it points to a completely different funder than
the call the project was actually awarded under.

Concrete example: project `SPINETRACER` (id=1656) has
`id_funding=250` (Michael J. Fox Foundation) and `id_call=2091`
(ERC-2024-PoC2 Proof of Concept Grant, mandate=5). The Horizon
Europe / ERC call is the right one, and the central edit page uses
`id_call` for its label. We do the same.

The `LEFT JOIN` means a missing `id_call` reference (e.g.,
`project.id_call = 0` or pointing to a deleted row) doesn't drop the
project — `mandate_id` just comes back NULL and the project
contributes `"unknown"` unless Source B fires.

## Per-project signal (Source A and Source B)

Each row returned is classified by `_classify_project_signal` into one
of four buckets. Source B is checked first (it dominates Source A in
practice; see "Why Source B dominates" below):

### Source B — AEI / PRTR project_code prefix

```python
_AEI_PATTERN = re.compile(r"^(PID|PDC|TED)\d{4}-")
```

If `project_code` matches this pattern, the project contributes
**`"data"`** with 0-month embargo (Spanish law mandates immediate OA
for these grants).

Prefixes recognized:

| Prefix | Spanish AEI program | Years observed in our DB |
|---|---|---|
| `PID` | Plan Estatal — Proyectos de Investigación | 2019, 2020, 2021, 2022, 2023, 2024 |
| `PDC` | Plan Estatal — Pruebas de Concepto | 2021, 2022 |
| `TED` | PRTR / Next-Generation EU — Transición Ecológica y Digital | 2021 |

We do **not** recognize:

- `RYC` (Ramón y Cajal fellowships) — personal grants, no data mandate.
- `PRE` / `PRE_` (pre-doctoral fellowships) — personal grants.
- `MDM` (María de Maeztu excellence award) — paper-only mandate, no
  data deposit required.
- Numeric-only codes (e.g. `818089`, `101213598`) — typically H2020 /
  Horizon Europe EU grants; their data-deposit rules vary by program
  and aren't reliably encoded in the central DB.
- `CB`, `CI`, `019-B1`, ad-hoc codes — no consistent rule.

### Source A — explicit `cff_oaMandate`

If `mandate_id IN (1, 2, 5)`: contributes **`"data"`** (these mandate
strings all contain `"DATA"`).
If `mandate_id = 3`: contributes **`"paper_only"`** (paper required, no
data — `"Yes OA: 6 months"`).
If `mandate_id = 4`: contributes **`"no_oa"`** (`"No OA"` mandate).
The five mandate strings live in the central `cff_oaMandate` table:

| id | type |
|---|---|
| 1 | `Yes OA: 0 months, DATA ` |
| 2 | `Yes OA: 6 months, DATA` |
| 3 | `Yes OA: 6 months ` |
| 4 | `No OA` |
| 5 | `Yes OA: 0 months, DATA, OA Journal Cost` |

### Otherwise

If neither Source B nor Source A fires, the project contributes
**`"unknown"`**.

### Why Source B dominates

In the central DB, **every AEI grant we've examined has
`cff_funding.id_oa_mandate = NULL`** — even when the intranet webpage
clearly shows it as data-required. The webpage's PHP appears to
prefix-match `project_code` for AEI grants exactly the way we do; it
only consults `cff_oaMandate` for **non-AEI** funders (Foundation
grants, EU grants, etc.).

So in practice:

- AEI grants (PID/PDC/TED) → Source B fires; Source A is irrelevant.
- Non-AEI grants with populated mandate (e.g. some Foundation grants)
  → Source A fires.
- Everything else → `"unknown"`.

## Publication-level aggregation

After classifying every linked project, `derive_oa_requirement`
combines the signals:

- **`oa_data_required = True`** if any project contributes `"data"`.
- **`oa_data_required = False`** if every project contributes
  `"paper_only"` or `"no_oa"` (no `"data"` *and* no `"unknown"`).
- **`oa_data_required = None`** if any project is `"unknown"` and no
  project contributes `"data"`. We don't assume "no data needed" in
  the face of ignorance.

`oa_paper_required` follows the analogous logic on `"data"` + `"paper_only"`.

`max_embargo_months = MIN` across mandates that mention embargo
(strictest wins).

`oa_mandate_source` records the contribution per project for the audit
log (e.g. `"proj=1410:data(0mo); proj=505:unknown"`).

**`oa_mandate_missing = True`** iff every linked project contributes
`"unknown"`. Or if the publication has zero `project_publis` rows at
all.

## How that maps to action-sheet rows (in `sheet.py`)

| Cached state | Action sheet behavior | Auto-note |
|---|---|---|
| `oa_data_required = 1` | Standard pipeline row (qa_pass, etc.) + reminders | (none) |
| `oa_data_required = 0` AND `oa_paper_required = 0` | One `close_publication_only` row, no reminders | `"No OA mandate on linked project(s); no data archiving required."` |
| `oa_data_required = 0` AND `oa_paper_required = 1` (paper-only) AND status is `OPEN_INACTIVE` | One `close_publication_only` row | `"PAPER ONLY mandate: data not required and folder still empty — consider closing as publication-only."` |
| `oa_data_required = 0` AND `oa_paper_required = 1` (paper-only) AND status is `OPEN_ACTIVE` or later | Standard pipeline + reminders suppressed | `"PAPER ONLY: data not required by mandate; processing as if data were required."` |
| `oa_data_required = NULL` AND `oa_paper_required = 1` (paper-only with some unknowns) | Same as paper-only (above two rows by status) | Same as above |
| `oa_mandate_missing = 1` | One `mandate_missing` row, no pipeline, no reminders | `"No mandate found in cff_oaMandate or AEI rule — investigate before closing."` |
| `oa_data_required = NULL` AND `oa_paper_required = NULL` AND `oa_mandate_missing = 0` (ambiguous mix of no_oa and unknown projects) | `mandate_missing` row | `"Mandate signal ambiguous (mixed no-OA and unknown projects) — confirm with PO/IT before closing or pursuing."` |
| No `pub_db_last_refreshed_at` (legacy / scan never reached pub-DB) | Legacy behavior — standard pipeline | (none) |

## When something disagrees with the central webpage

The webpage at
`https://intranet.cicbiomagune.es/research_database/admin/publication/publi_bootgrid/edit.php?id=X`
shows OA flags rendered as colored labels:

- **Red `label-danger`** — `"Open Data Required for project NAME / CODE"`.
  Driven by Source B (AEI prefix match). If we say `mandate_missing`
  and the webpage shows red, almost always means we missed an AEI
  prefix — extend `_AEI_PATTERN` and add a test case.
- **Blue `label-primary`** — full `cff_oaMandate.type` string rendered
  verbatim. Driven by Source A. If we say `mandate_missing` and the
  webpage shows blue, the publication's project has a populated
  `cff_oaMandate.id_oa_mandate` that we somehow aren't joining
  correctly. Check the JOIN path.
- **Yellow `label-warning`** — `"X / Y: No data is required, but the
  call indicates that it must have a maximum embargo of N months"`.
  This is rendered for older/foreign grants the webpage classifies as
  paper-only with embargo. We do **not** replicate this rule —
  paper-only is the Project Office's concern, not the Data Office's.
  These show up as `"unknown"` for us; that's acceptable because
  they're never `oa_data_required=True`.

If you find a publication where our `mandate_missing` disagrees with a
red or blue label, capture the publication ID + the webpage source
(view-source or save the HTML) and we can extend the rule.

## Corresponding-author lookup (related, lives in the same module)

`pub_db.lookup_corresponding_author(conn, pub_id)` resolves
`publi_corr_auth.id_user` to a person's current name and email. Two
critical points about the join, learned the hard way:

1. **`publi_corr_auth.id_user` joins to `center_user.id_user`**, NOT to
   `mdm_personal.id`. The two tables happen to share an `id`-space that
   overlaps by coincidence; e.g. `publi_corr_auth.id_user = 91` correctly
   resolves to *Aitziber López Cortajarena* (`center_user.id_user = 91`,
   `center_user.id = 52`), but `mdm_personal[id=91]` is an entirely
   unrelated person (CONDE 2017 snapshot). We were misjoining and got
   wrong author names for months. The intranet edit page itself uses
   `center_user.id_user` (see e.g. the `arrayCorresponding` JavaScript
   variable in any `edit.php?id=<pub>` source).
2. **`center_user.username + "@cicbiomagune.es"`** gives the institutional
   email. The convention is consistent across all sampled accounts
   (`alcortajarena`, `mliutkus`, `pblesio`, `mprato`, `scarregal`, etc.).

Sentinels and special cases:

- `id_user = -1` or `0` → "no biomaGUNE corresponding author" → return
  `(None, None)`. Operator overrides per pub via
  `oa action <pub> set_data_contact --email ... --name ...`.
- `id_user` not present in `center_user` → return `(None, None)`.
  Person isn't in the canonical personnel table.
- `center_user.endDate < today` → person has departed → return
  `(None, None)`.
- `center_user.name` contains HTML entities (e.g. `Rodr&iacute;guez`).
  We decode them with `html.unescape` for human-readable output.

`mdm_personal` is NOT used for author resolution. It's a per-year
snapshot table used elsewhere; its `id` is unstable across years and
its name format is inconsistent (`empleado` formatting varies). Leave
it alone.

## Historical notes

- **First version (2026-05-07)** — regex was `^(PID|PDC)20(2[2-9]|[3-9]\d)-`
  (PID/PDC, year ≥ 2022). Based on the assumption that the Spanish
  LCTI open-access reform (2022) was the cutoff for AEI data mandates.
- **Second version (2026-05-15)** — added the "ambiguous mix" case
  (no_oa + unknown projects) as `mandate_missing` rather than
  silently falling through to `data_required`. Caught by pub 3271.
- **Third version (2026-05-18, morning)** — broadened the regex to
  `^(PID|PDC|TED)\d{4}-` after finding that pubs 3105/3195 (TED2021),
  3198 (PDC2021), 3204 (PID2020) were red on the webpage but missed
  by the previous regex (wrong prefix or pre-2022 year).
- **Fifth version (2026-05-18, evening — current)** — replaced the
  corresponding-author lookup's table from `mdm_personal` to
  `center_user`, after discovering via the pub 3194 webpage HTML that
  `publi_corr_auth.id_user` actually FKs to `center_user.id_user`
  (not `mdm_personal.id`). The two `id`-spaces happen to overlap by
  coincidence and we were silently returning the wrong people for
  months. New lookup also derives email from `center_user.username +
  "@cicbiomagune.es"` (previously the field was always `None`). Live
  effect: the 14 archives the operator flagged as having wrong
  contact names now resolve correctly — Aitziber López Cortajarena,
  Fernando López Gallego, Jesus Ruiz-Cabello, etc. with their
  matching email addresses.
- **Fourth version (2026-05-18, afternoon)** — switched the
  `cff_funding` JOIN from `project.id_funding` to `project.id_call`
  after finding that **`project.id_funding` is consistently wrong or
  orphaned** in this DB. Concrete trigger: pub 3259 (SPINETRACER)
  showed a blue `label-primary` "Yes OA: 0 months, DATA, OA Journal
  Cost" on the webpage, but our query returned `mandate_id=None`.
  Direct query showed `project.id_funding=250` is Michael J. Fox
  Foundation (NULL mandate) while `project.id_call=2091` is the
  ERC-2024-PoC2 call (mandate=5). The webpage joins on `id_call`.
  This single change recovered correct mandates for 12 of the 14
  spurious `mandate_missing` archives on 2026-05-18.
