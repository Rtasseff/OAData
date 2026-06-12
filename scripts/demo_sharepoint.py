#!/usr/bin/env python3
"""Seed / clear throwaway demo entries for an end-to-end SharePoint List walkthrough.

These are NOT real publications. They let you (or a pilot user) drive the full
operator <-> data-contact round trip on the live List — suggest a new data
contact, propose an exemption, flag "done", leave a note — without touching any
real publication or the /mnt/c/ folder tree.

    # 1. seed demo rows into the DB (you are the data contact + corresponding author)
    python scripts/demo_sharepoint.py seed --email you@cicbiomagune.es --name "Your Name"

    # 2. push them to the List (real pipeline; also re-syncs the real rows)
    oa sharepoint sync

    # 3. open the List, edit a demo row, then pull the proposal back
    oa sharepoint sync          # -> output/sharepoint_proposals.tsv
    #    review, set done=1 on the ones to apply, then:  oa apply output/sharepoint_proposals.tsv

    # 4. tear everything down (DB rows + events + live List items)
    python scripts/demo_sharepoint.py clear

Safety:
  * Demo rows live at PubId >= 990000 so they never collide with real ones.
  * Nothing here writes under /mnt/c/. Demo rows have no folder on disk, so the
    next `oa scan` will flag them `unexpected_missing_folder` (cosmetic,
    non-destructive) until you `clear` them.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

# Allow running as `python scripts/demo_sharepoint.py` from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oa_tracker import db, status as st  # noqa: E402
from oa_tracker.config import load_config  # noqa: E402

DEMO_FLOOR = 990000  # anything at/above this is treated as a demo row by `clear`

# Fake publications spread across pipeline stages so the List shows several
# status labels and every user-facing feature can be exercised. The matching
# SharePoint folders to create (so the Folder link resolves) are named exactly
# by ``pub_id``. ``tests`` documents what each row is for — see
# docs/sharepoint_demo_runbook.md for the step-by-step walkthrough.
STAGED_ROWS = [
    {
        "pub_id": "990001",
        "title": "DEMO: Plasmonic nanoparticles — SERS dataset",
        "status": st.OPEN_ACTIVE,
        "journal": "Demo J. Nanophotonics", "year": 2026,
        "data_required": 1, "embargo": 0, "doi": None, "zenodo": None,
        "tests": "Folder link · 'Suggest a new data contact' · 'I think this is done'",
    },
    {
        "pub_id": "990002",
        "title": "DEMO: MRI contrast agent — biodistribution data",
        "status": st.OPEN_ACTIVE,
        "journal": "Demo J. Mol. Imaging", "year": 2026,
        "data_required": 1, "embargo": 6, "doi": None, "zenodo": None,
        "tests": "Exemption 'All data deposited in another archive' (needs External PID + URL)",
    },
    {
        "pub_id": "990003",
        "title": "DEMO: Multi-center collaboration — imaging study",
        "status": st.OPEN_ACTIVE,
        "journal": "Demo Collab. Reports", "year": 2025,
        "data_required": 1, "embargo": 0, "doi": None, "zenodo": None,
        "tests": "Exemption 'Collaborative AND no biomaGUNE data or lead' · leave a Note",
    },
    {
        "pub_id": "990004",
        "title": "DEMO: Peptide synthesis — characterization (awaiting upload)",
        "status": st.OPEN_INACTIVE,
        "journal": "Demo J. Pept. Sci.", "year": 2026,
        "data_required": 1, "embargo": 0, "doi": None, "zenodo": None,
        "tests": "Status label 'Waiting for data' — keep this SharePoint folder EMPTY",
    },
    {
        "pub_id": "990005",
        "title": "DEMO: Hydrogel rheology dataset",
        "status": st.OPEN_READY_FOR_ZENODO_DRAFT,
        "journal": "Demo Soft Matter", "year": 2025,
        "data_required": 1, "embargo": 0, "doi": "10.0000/demo.990005", "zenodo": None,
        "tests": "Later-stage label 'Ready to archive' · DOI link (fake — will 404)",
    },
    {
        "pub_id": "990006",
        "title": "DEMO: Single-cell RNA-seq — tumor microenvironment",
        "status": st.OPEN_ZENODO_PUBLISHED,
        "journal": "Demo Genomics", "year": 2024,
        "data_required": 1, "embargo": 0, "doi": "10.0000/demo.990006", "zenodo": "9999999",
        "tests": "Label 'Published to Zenodo' · Zenodo link (fake — will 404)",
    },
]


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _demo_ids_in_db(conn) -> list[str]:
    """Every archive row that looks like a demo row (numeric PubId >= floor)."""
    rows = conn.execute("SELECT publication_id FROM archives").fetchall()
    out = []
    for r in rows:
        pid = r["publication_id"]
        if pid.isdigit() and int(pid) >= DEMO_FLOOR:
            out.append(pid)
    return sorted(out, key=int)


def seed(cfg, email: str, name: str, count: int, force: bool) -> None:
    db.init_db(cfg.database)
    now = _now()
    created, updated, skipped = [], [], []
    with db.get_connection(cfg.database) as conn:
        for spec in STAGED_ROWS[:count]:
            pub_id = spec["pub_id"]
            exists = db.get_archive(conn, pub_id) is not None
            if exists and not force:
                skipped.append(pub_id)
                continue
            active = spec["status"] != st.OPEN_INACTIVE
            db.upsert_archive(
                conn,
                publication_id=pub_id,
                folder_path="(demo entry — no folder on disk)",
                first_seen_at=now,
                became_active_at=now if active else None,
                last_seen_at=now,
                last_changed_at=now,
                status=spec["status"],
                pub_title=spec["title"],
                pub_journal=spec["journal"],
                pub_year=spec["year"],
                pub_doi=spec["doi"],
                zenodo_code=spec["zenodo"],
                oa_data_required=spec["data_required"],
                oa_mandate_missing=0,
                max_embargo_months=spec["embargo"],
                # You are both, so both [Me] views light up. overridden=1 keeps
                # a future scan from re-deriving these from the pub-DB (note:
                # title/journal/data-required are NOT protected — re-run with
                # --force to restore them if a scan blanks them).
                corresponding_author_name=name,
                corresponding_author_email=email,
                corresponding_author_overridden=1,
                data_contact_name=name,
                data_contact_email=email,
                data_contact_overridden=1,
                zenodo_code_overridden=1,
            )
            db.insert_event(
                conn, pub_id, "demo_seed", None, spec["status"], "demo"
            )
            (updated if exists else created).append(pub_id)

    if created:
        print(f"Created {len(created)} demo row(s): {', '.join(created)}")
    if updated:
        print(f"Refreshed {len(updated)} existing demo row(s): {', '.join(updated)}")
    if skipped:
        print(f"Skipped {len(skipped)} existing row(s) (use --force to refresh): {', '.join(skipped)}")
    if created or updated:
        print(f"  data contact / corresponding author = {name} <{email}>")
        print("  Create matching SharePoint folders named: " + ", ".join(s["pub_id"] for s in STAGED_ROWS[:count]))
        print("\nNext: push them to the List with")
        print("  oa sharepoint sync")


def clear(cfg, keep_list: bool) -> None:
    # 1) DB side.
    with db.get_connection(cfg.database) as conn:
        ids = _demo_ids_in_db(conn)
        if not ids:
            print("No demo rows in the DB.")
        else:
            qmarks = ",".join("?" for _ in ids)
            conn.execute(f"DELETE FROM events WHERE publication_id IN ({qmarks})", ids)
            conn.execute(f"DELETE FROM archives WHERE publication_id IN ({qmarks})", ids)
            print(f"Removed {len(ids)} demo row(s) from the DB: {', '.join(ids)}")

    # 2) Live List side (best-effort; needs Graph auth).
    if keep_list:
        print("Skipping List cleanup (--keep-list). Delete the DEMO rows in the UI if needed.")
        return
    try:
        from oa_tracker import sharepoint as sp_mod

        sp = sp_mod.load_settings(cfg)
        client = sp_mod.GraphClient(sp)
        site_id = client.get_site_id(sp.site)
        lst = sp_mod.get_list(client, site_id, sp.list_name)
        if lst is None:
            print(f"List {sp.list_name!r} not found — nothing to clean up there.")
            return
        list_id = lst["id"]
        name_for = sp_mod.resolve_names(client, site_id, list_id)
        existing = sp_mod.fetch_items(client, site_id, list_id, name_for[sp_mod.D_PUBID])
        removed = 0
        for pid, item in existing.items():
            if pid.isdigit() and int(pid) >= DEMO_FLOOR:
                client.request("DELETE", f"/sites/{site_id}/lists/{list_id}/items/{item['id']}")
                removed += 1
        print(f"Removed {removed} demo item(s) from the List.")
    except Exception as e:  # noqa: BLE001 - best-effort cleanup; DB is already clean
        print(f"List cleanup skipped ({e}).")
        print("Delete the DEMO rows (PubId >= 990000) in the SharePoint UI, or re-run with auth.")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config", help="path to config.toml (default: project config)")
    p.add_argument("--db", help="override the database path (for safe testing)")
    sub = p.add_subparsers(dest="cmd", required=True)

    ps = sub.add_parser("seed", help="insert staged demo archive rows into the DB")
    ps.add_argument("--email", required=True, help="your institutional email (…@cicbiomagune.es)")
    ps.add_argument("--name", help="your display name (default: derived from email)")
    ps.add_argument("--count", type=int, default=len(STAGED_ROWS),
                    help=f"how many staged rows to seed (default {len(STAGED_ROWS)})")
    ps.add_argument("--force", action="store_true",
                    help="refresh demo rows that already exist (e.g. after a scan blanked them)")

    pc = sub.add_parser("clear", help="remove demo rows from the DB and the live List")
    pc.add_argument("--keep-list", action="store_true", help="only clean the DB; leave the List rows")

    args = p.parse_args(argv)
    cfg = load_config(config_path=Path(args.config) if args.config else None)
    if args.db:
        cfg.database = Path(args.db).resolve()

    if args.cmd == "seed":
        name = args.name or args.email.split("@", 1)[0].replace(".", " ").title()
        seed(cfg, args.email, name, max(1, args.count), args.force)
    elif args.cmd == "clear":
        clear(cfg, args.keep_list)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
