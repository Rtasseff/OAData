"""Has Zenodo enabled multipart part-uploads yet?

As of 2026-07-04 Zenodo's InvenioRDM deployment ACCEPTS the multipart
init (201, per-part URLs) but DENIES the part PUT itself (403, with a
token that single-PUTs fine) — the v13 scaffolding is there, the
feature isn't enabled for API users. `zenodo.upload_files` probes this
implicitly on every >threshold upload and falls back automatically, so
nothing operational depends on this script; it just answers the
question on demand without waiting for a big deposit to come along.

Usage (harmless: creates a private draft, tries one 5 MiB part,
discards the draft):

    .venv/bin/python scripts/probe_zenodo_multipart.py [--env sandbox|production]

Verdict is printed on the last line: MULTIPART ENABLED / DISABLED.
"""

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from oa_tracker import zenodo
from oa_tracker.config import ZenodoSettings

PART = 5 * 1024 * 1024


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", choices=("sandbox", "production"), default="sandbox")
    env = ap.parse_args().env

    settings = ZenodoSettings(enabled=True, environment=env)
    client = zenodo.get_client(settings)

    _, body = client.request("POST", "/api/records", json_body={
        "metadata": {
            "title": "MULTIPART PROBE - DISCARD",
            "publication_date": date.today().isoformat(),
            "resource_type": {"id": "dataset"},
            "creators": [{"person_or_org": {
                "type": "personal", "family_name": "Probe", "given_name": "Multipart"}}],
        },
    })
    rid = str(body["id"])
    print(f"[{env}] draft {rid} created")

    try:
        try:
            _, resp = client.request(
                "POST", f"/api/records/{rid}/draft/files",
                json_body=[{"key": "probe.bin", "size": 2 * PART,
                            "transfer": {"type": "M", "parts": 2, "part_size": PART}}],
            )
        except zenodo.ZenodoError as e:
            print(f"init rejected ({e}) → MULTIPART DISABLED (at init)")
            return 1
        entry = next(e for e in resp["entries"] if e["key"] == "probe.bin")
        parts = {p["part"]: p["url"] for p in entry["links"].get("parts", [])}
        if not parts:
            print("init accepted but no part URLs → MULTIPART DISABLED (no links)")
            return 1
        print(f"init accepted, {len(parts)} part URLs issued")
        try:
            client.request("PUT", parts[1], data=b"A" * PART,
                           content_type="application/octet-stream",
                           content_length=PART)
        except zenodo.ZenodoError as e:
            print(f"part PUT rejected ({e}) → MULTIPART DISABLED (at part PUT)")
            return 1
        print("part PUT accepted → MULTIPART ENABLED — large uploads will "
              "use it automatically (see zenodo_design.md § Large files)")
        return 0
    finally:
        zenodo.discard_draft(client, rid)
        print(f"draft {rid} discarded")


if __name__ == "__main__":
    raise SystemExit(main())
