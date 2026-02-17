Here’s a concise context summary you can hand to software folks.

---

## Project summary: OA Archive Tracker automation (CIC biomaGUNE)

We run an internal workflow to ensure **publication-associated research outputs** (primarily datasets; sometimes article files) are deposited into **Zenodo** and properly recorded for Open Access reporting. The upstream steps (publication database curation, OA checks, creation of a SharePoint folder per publication ID, and author/data-contact email request) are managed by the Project Office. My scope begins **after** the data contact uploads files to the SharePoint folder.

### What we’re building (Stage 1 MVP)

A lightweight automation system that:

* Monitors a **locally synced mirror of the SharePoint “Publications Data” folders** (one folder per publication, folder name = publication ID).
* Treats **empty folders as OPEN_INACTIVE** (data contact hasn’t acted) and **non-empty folders as OPEN_ACTIVE** (activity started).
* Maintains a durable **SQLite registry** of all archives (open + closed) including:

  * first seen date, date became active, last change, current status,
  * reminder history (last notified, reminder count),
  * closure info (final PID/DOI + URL + optional notes).
* Generates a **weekly report**: what’s new, what’s stuck, reminders due, ready for Zenodo, and integrity warnings (e.g., folder disappeared but archive not closed).
* Generates a text-based **Operator Action Sheet** (`action_sheet.tsv`) listing tasks (QA passed, Zenodo draft created, Zenodo published, internal DB updated, folder removed). I update this sheet as I do manual actions, then a script ingests it to update SQLite—so I never edit SQLite directly.

### Why this approach

* End-to-end automation isn’t realistic right now: authors upload inconsistently, QA is manual, and access to the internal publication database is controlled by IT.
* The folder system is the most reliable operational signal today; the registry prevents “lost history” once folders are removed after Zenodo publication.

### Future stages (once MVP works)

2. Automate Zenodo via API (especially **large-file uploads**, which are slow/error-prone via UI).
3. Read internal publication database for richer metadata and better email templates.
4. Write back to the internal DB automatically (if IT allows).

### Key policy/technical constraint

For dataset deposits, **Zenodo must mint its own DOI/PID**. We must not reuse the publication DOI as the dataset DOI; the paper DOI should be linked as a related identifier in Zenodo metadata.

