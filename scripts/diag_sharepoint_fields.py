#!/usr/bin/env python3
"""Read-only diagnostic: dump how user-column display names resolve to internal
names, and the actual fields a given List item carries.

Use to debug a value that isn't being read back (e.g. Notes not surfacing).
GET-only — writes nothing to SharePoint or the DB.

    python scripts/diag_sharepoint_fields.py --pub-id 990003
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oa_tracker import sharepoint as sp_mod  # noqa: E402
from oa_tracker.config import load_config  # noqa: E402

USER_COLS = [
    ("D_PDONE", sp_mod.D_PDONE), ("D_PEXEMPT", sp_mod.D_PEXEMPT),
    ("D_EXTPID", sp_mod.D_EXTPID), ("D_EXTURL", sp_mod.D_EXTURL),
    ("D_DETAIL", sp_mod.D_DETAIL), ("D_REASSIGN", sp_mod.D_REASSIGN),
    ("D_NOTES", sp_mod.D_NOTES), ("D_PUBID", sp_mod.D_PUBID),
]


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--config")
    p.add_argument("--pub-id", default="990003")
    args = p.parse_args(argv)

    cfg = load_config(config_path=Path(args.config) if args.config else None)
    sp = sp_mod.load_settings(cfg)
    client = sp_mod.GraphClient(sp)
    site_id = client.get_site_id(sp.site)
    lst = sp_mod.get_list(client, site_id, sp.list_name)
    if lst is None:
        print(f"List {sp.list_name!r} not found.")
        return 1
    list_id = lst["id"]

    name_for = sp_mod.resolve_names(client, site_id, list_id)
    print("=== display -> resolved internal name ===")
    for const_name, display in USER_COLS:
        print(f"  {const_name:12} {display!r:42} -> {name_for.get(display)!r}")

    items = sp_mod.fetch_items(client, site_id, list_id, name_for[sp_mod.D_PUBID])
    item = items.get(args.pub_id)
    if item is None:
        print(f"\nNo List row with PubId {args.pub_id}.")
        return 1

    fields = item.get("fields") or {}
    print(f"\n=== ALL fields on row {args.pub_id} (raw keys from Graph) ===")
    for k in sorted(fields):
        print(f"  {k!r}: {fields[k]!r}")

    print("\n=== does each resolved name exist in the row's fields? ===")
    for const_name, display in USER_COLS:
        internal = name_for.get(display)
        present = internal in fields
        val = fields.get(internal)
        print(f"  {display!r:42} key={internal!r:24} present={present} value={val!r}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
