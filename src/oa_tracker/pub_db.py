"""Read-only access to the central publication database (MariaDB).

Stage 2 of the roadmap: enrich each archive on scan with publication
metadata, OA-mandate flags, corresponding-author info, and central
repository references. All writes go to our own SQLite via db.py;
this module only reads.

Credentials live in ``~/.my.cnf`` (mode 600). The path is hardcoded;
the file's permissions are the security boundary.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from typing import Any

import pymysql
import pymysql.cursors


_CNF_PATH = os.path.expanduser("~/.my.cnf")
_USER = "rtasseff"
_DATABASE = "publications"

# cff_oaMandate.id values, by what each implies for our work.
_MANDATE_DATA_AND_PAPER = {1, 2, 5}  # "Yes OA: ... DATA ..."
_MANDATE_PAPER_ONLY = {3}            # "Yes OA: 6 months" — paper, no data
_MANDATE_NO_OA = {4}                 # "No OA"

# Embargo months associated with each cff_oaMandate.id (None when N/A).
_MANDATE_EMBARGO_MONTHS: dict[int, int | None] = {
    1: 0, 2: 6, 3: 6, 4: None, 5: 0,
}

# Spanish AEI / PRTR project-code pattern. Matches project_code starting
# with one of the AEI program prefixes (PID = Plan Estatal Proyectos de
# Investigación, PDC = Pruebas de Concepto, TED = Transición Ecológica
# y Digital / Plan de Recuperación) followed by a 4-digit year and a
# dash. The central DB has cff_funding.id_oa_mandate effectively
# unpopulated for every AEI grant we've seen (PID2019…2024, PDC2021–22,
# TED2021), so prefix-matching is the only working signal — which is
# also what the intranet edit page itself does to render its red
# "Open Data Required" labels. Verified against pubs 3092 (PROTHER,
# ProIMAGE), 3097 (NEUROGEL), 3105/3195 (TED2021), 3198 (PDC2021),
# and 3204 (PID2020).
_AEI_PATTERN = re.compile(r"^(PID|PDC|TED)\d{4}-")

# Sentinel id_user value in publi_corr_auth meaning "no biomaGUNE
# corresponding author" (publication has only an external author).
_NO_AUTHOR_SENTINEL = -1

# Repository name used for auto-seeding the operator-managed
# zenodo_code column. Other repository names are still recorded
# verbatim in central_repository / central_repository_code.
_ZENODO_REPOSITORY_NAME = "Zenodo"


@dataclass
class CachedPubFields:
    """Fields written into ``archives`` rows by ``enrich_archive``."""

    pub_title: str | None
    pub_doi: str | None
    pub_journal: str | None
    pub_year: int | None
    oa_paper_required: bool | None
    oa_data_required: bool | None
    max_embargo_months: int | None
    oa_mandate_source: str | None
    oa_mandate_missing: bool
    corresponding_author_name: str | None
    corresponding_author_email: str | None
    central_repository: str | None
    central_repository_code: str | None
    # auto_zenodo_code carries the Zenodo code (when the central DB has a
    # repo_publis row whose repository name is "Zenodo") so db.upsert can
    # seed the operator-managed zenodo_code. It is *not* itself a stored
    # column — it's a transport field for the eager-cache step.
    auto_zenodo_code: str | None


def get_connection() -> pymysql.connections.Connection:
    """Open a connection using ``~/.my.cnf`` for credentials."""
    return pymysql.connect(
        read_default_file=_CNF_PATH,
        user=_USER,
        database=_DATABASE,
        cursorclass=pymysql.cursors.DictCursor,
    )


# ── Per-project signal classification ────────────────────────────────

def _classify_project_signal(
    mandate_id: int | None,
    project_code: str | None,
) -> tuple[str, int | None]:
    """Return (label, embargo_months) for a single linked project.

    Labels: ``"data"`` (data archiving required), ``"paper_only"``
    (paper required, data not), ``"no_oa"`` (explicit No-OA mandate),
    ``"unknown"`` (no rule applied — ``cff_oaMandate`` NULL and no
    AEI match).
    """
    # Source B (AEI 2022+) wins when it matches — Spanish law mandates
    # both paper and data OA; treat as 0-month embargo (immediate OA).
    if project_code and _AEI_PATTERN.match(project_code):
        return ("data", 0)

    # Source A — explicit cff_oaMandate.
    if mandate_id in _MANDATE_DATA_AND_PAPER:
        return ("data", _MANDATE_EMBARGO_MONTHS[mandate_id])
    if mandate_id in _MANDATE_PAPER_ONLY:
        return ("paper_only", _MANDATE_EMBARGO_MONTHS[mandate_id])
    if mandate_id in _MANDATE_NO_OA:
        return ("no_oa", None)

    return ("unknown", None)


# ── Per-publication queries ──────────────────────────────────────────

def lookup_publication(conn, pub_id: str) -> dict[str, Any] | None:
    """Return basic publication metadata, or None if pub_id not found."""
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id, title, doi, journal, year FROM publication WHERE id = %s",
            (pub_id,),
        )
        return cur.fetchone()


def derive_oa_requirement(
    conn, pub_id: str
) -> tuple[bool | None, bool | None, int | None, str, bool]:
    """Derive OA flags by aggregating signals across all linked projects.

    Returns ``(oa_paper_required, oa_data_required, max_embargo_months,
    oa_mandate_source, oa_mandate_missing)``.

    - ``oa_data_required`` is ``True`` if any project signals data;
      ``False`` if every project signals paper_only or no_oa with no
      ``unknown``; ``None`` if any project is unknown and no project
      signals data (we don't assume "no data" in the face of ignorance).
    - ``oa_paper_required`` follows the analogous logic for paper.
    - ``oa_mandate_missing`` is ``True`` iff every project is unknown.
    - ``oa_mandate_source`` is a human-readable trace of the
      contributions for the audit log.
    """
    with conn.cursor() as cur:
        # JOIN cff_funding via project.id_call, NOT project.id_funding.
        # project.id_funding is empirically unreliable in this DB —
        # often orphaned (no matching cff_funding row) or pointing to
        # the wrong call (e.g. project SPINETRACER has id_funding=250
        # which is Michael J. Fox Foundation, but id_call=2091 which
        # is ERC-2024-PoC2 with mandate=5 — and the central edit page
        # uses the id_call value). This was the cause of 12 of 14
        # spurious mandate_missing classifications in the 2026-05-18
        # weekly run. See docs/mandate_classification.md for details.
        cur.execute(
            """
            SELECT pp.id_project AS proj_id,
                   p.project_code AS project_code,
                   cf.id_oa_mandate AS mandate_id
              FROM project_publis pp
              LEFT JOIN project p      ON p.id  = pp.id_project
              LEFT JOIN cff_funding cf ON cf.id = p.id_call
             WHERE pp.id_publi = %s
            """,
            (pub_id,),
        )
        rows = cur.fetchall()

    if not rows:
        return (None, None, None, "no project_publis rows", True)

    contributions: list[tuple[int, str, int | None]] = []
    for r in rows:
        label, embargo = _classify_project_signal(r["mandate_id"], r["project_code"])
        contributions.append((r["proj_id"], label, embargo))

    labels = [c[1] for c in contributions]
    embargos = [c[2] for c in contributions if c[2] is not None]

    has_data = "data" in labels
    has_paper_only = "paper_only" in labels
    has_unknown = "unknown" in labels

    if has_data:
        oa_data: bool | None = True
    elif has_unknown:
        oa_data = None
    else:
        oa_data = False

    if has_data or has_paper_only:
        oa_paper: bool | None = True
    elif has_unknown:
        oa_paper = None
    else:
        oa_paper = False

    missing = all(label == "unknown" for label in labels)
    max_embargo = min(embargos) if embargos else None

    parts = [
        f"proj={pid}:{label}" + (f"({emb}mo)" if emb is not None else "")
        for pid, label, emb in contributions
    ]
    source = "; ".join(parts)

    return (oa_paper, oa_data, max_embargo, source, missing)


def lookup_corresponding_author(conn, pub_id: str) -> tuple[str | None, str | None]:
    """Return ``(name, email)`` of the corresponding author, or ``(None, None)``.

    Note: ``mdm_personal`` has no email column in the central DB, and
    no personnel-email table joins to it. Email is therefore always
    ``None`` here; the operator manages ``data_contact_email`` directly
    (defaulting to ``'TBD'`` until set).

    A ``publi_corr_auth.id_user`` value of ``-1`` is the sentinel
    meaning "external corresponding author, not in mdm_personal" —
    treated the same as no record.
    """
    with conn.cursor() as cur:
        cur.execute(
            "SELECT id_user FROM publi_corr_auth WHERE id_publi = %s",
            (pub_id,),
        )
        row = cur.fetchone()
        if not row:
            return (None, None)
        uid = row["id_user"]
        if uid == _NO_AUTHOR_SENTINEL or uid is None:
            return (None, None)
        cur.execute("SELECT name FROM mdm_personal WHERE id = %s", (uid,))
        m = cur.fetchone()
        return (m["name"] if m else None, None)


def lookup_central_repositories(conn, pub_id: str) -> list[tuple[str, str]]:
    """Return all ``(repository_name, repository_code)`` pairs.

    Empty list if the publication has no ``repo_publis`` rows. Order
    follows ``repo_publis.id`` so callers can correlate name and code
    positionally.
    """
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT r.name AS name, rp.repository_code AS code
              FROM repo_publis rp
              LEFT JOIN repository r ON r.id = rp.id_repo
             WHERE rp.id_publi = %s
             ORDER BY rp.id
            """,
            (pub_id,),
        )
        return [(r["name"] or "", r["code"] or "") for r in cur.fetchall()]


def enrich_archive(conn, pub_id: str) -> CachedPubFields:
    """One-call entrypoint for the scanner. Aggregates all lookups."""
    pub = lookup_publication(conn, pub_id)
    paper_req, data_req, embargo, mandate_src, missing = derive_oa_requirement(conn, pub_id)
    auth_name, auth_email = lookup_corresponding_author(conn, pub_id)
    repos = lookup_central_repositories(conn, pub_id)

    central_names = "; ".join(name for name, _ in repos) if repos else None
    central_codes = "; ".join(code for _, code in repos) if repos else None
    auto_zenodo = next(
        (code for name, code in repos if name == _ZENODO_REPOSITORY_NAME and code),
        None,
    )

    pub_year: int | None = None
    if pub and pub.get("year") is not None:
        try:
            pub_year = int(pub["year"])
        except (TypeError, ValueError):
            pub_year = None

    return CachedPubFields(
        pub_title=(pub["title"] if pub else None),
        pub_doi=(pub["doi"] if pub else None),
        pub_journal=(pub["journal"] if pub else None),
        pub_year=pub_year,
        oa_paper_required=paper_req,
        oa_data_required=data_req,
        max_embargo_months=embargo,
        oa_mandate_source=mandate_src,
        oa_mandate_missing=missing,
        corresponding_author_name=auth_name,
        corresponding_author_email=auth_email,
        central_repository=central_names,
        central_repository_code=central_codes,
        auto_zenodo_code=auto_zenodo,
    )
