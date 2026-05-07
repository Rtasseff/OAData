"""Unit tests for pub_db.py — mocked PyMySQL connection, no live DB."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest

from oa_tracker import pub_db


# ── Test plumbing ────────────────────────────────────────────────────

class _FakeCursor:
    """Minimal cursor that returns canned results based on SQL pattern matching."""

    def __init__(self, responses: list[tuple[re.Pattern, list[dict] | dict | None]]):
        # responses: list of (sql_regex, result). result is either a list
        # (returned by fetchall) or a dict / None (returned by fetchone).
        self._responses = responses
        self._next: list[dict] | dict | None = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def execute(self, sql: str, params=()):
        for pattern, result in self._responses:
            if pattern.search(sql):
                self._next = result
                return
        raise AssertionError(f"no fake response for SQL: {sql!r}")

    def fetchone(self):
        nxt = self._next
        if isinstance(nxt, list):
            return nxt[0] if nxt else None
        return nxt

    def fetchall(self):
        nxt = self._next
        if isinstance(nxt, list):
            return nxt
        if nxt is None:
            return []
        return [nxt]


def _conn_with(responses: list[tuple[str, list[dict] | dict | None]]):
    """Build a fake connection where each (sql_substring, result) pair
    returns ``result`` from any cursor execute matching ``sql_substring``."""
    compiled = [(re.compile(pat, re.IGNORECASE | re.DOTALL), res) for pat, res in responses]
    cursor = _FakeCursor(compiled)
    conn = MagicMock()
    conn.cursor = MagicMock(return_value=cursor)
    return conn


# ── _classify_project_signal ──────────────────────────────────────────

@pytest.mark.parametrize(
    "mandate_id, code, expected_label, expected_embargo",
    [
        # Source A — explicit cff_oaMandate
        (1, None, "data", 0),
        (2, None, "data", 6),
        (3, None, "paper_only", 6),
        (4, None, "no_oa", None),
        (5, None, "data", 0),
        # Source B — Spanish AEI 2022+ pattern
        (None, "PID2022-137977OB-I00", "data", 0),
        (None, "PDC2022-133345-I00", "data", 0),
        (None, "PID2025-XXXXX", "data", 0),
        (None, "PID2099-XXXXX", "data", 0),
        # Pre-2022 AEI does not match
        (None, "PID2021-12345", "unknown", None),
        (None, "PID2020-12345", "unknown", None),
        # Non-AEI codes
        (None, "MDM-2017-0720", "unknown", None),
        (None, "100010434", "unknown", None),
        (None, "AXA Chair in Nanobiotechnology", "unknown", None),
        # AEI takes precedence even when cff_oaMandate disagrees
        (4, "PID2022-XXXXX", "data", 0),
        (3, "PID2022-XXXXX", "data", 0),
    ],
)
def test_classify_project_signal(mandate_id, code, expected_label, expected_embargo):
    label, embargo = pub_db._classify_project_signal(mandate_id, code)
    assert label == expected_label
    assert embargo == expected_embargo


# ── lookup_publication ────────────────────────────────────────────────

def test_lookup_publication_found():
    conn = _conn_with([
        (r"FROM publication WHERE id", {
            "id": 3092, "title": "Probing the Biological Identity",
            "doi": "10.1002/smll.202504135", "journal": "Small", "year": 2025,
        }),
    ])
    row = pub_db.lookup_publication(conn, 3092)
    assert row["id"] == 3092
    assert row["doi"] == "10.1002/smll.202504135"


def test_lookup_publication_not_found():
    conn = _conn_with([(r"FROM publication WHERE id", None)])
    assert pub_db.lookup_publication(conn, 9999) is None


# ── derive_oa_requirement ─────────────────────────────────────────────

def _proj_rows(*projs):
    """Helper: build a list of project_publis-join rows."""
    return list(projs)


def test_derive_data_required_via_cff_mandate_1():
    conn = _conn_with([(r"FROM project_publis", _proj_rows(
        {"proj_id": 100, "project_code": "X", "mandate_id": 1},
    ))])
    paper, data, embargo, source, missing = pub_db.derive_oa_requirement(conn, 1)
    assert (paper, data, embargo, missing) == (True, True, 0, False)
    assert "proj=100:data(0mo)" in source


def test_derive_paper_only_via_cff_mandate_3():
    conn = _conn_with([(r"FROM project_publis", _proj_rows(
        {"proj_id": 100, "project_code": "X", "mandate_id": 3},
    ))])
    paper, data, embargo, source, missing = pub_db.derive_oa_requirement(conn, 1)
    assert (paper, data, embargo, missing) == (True, False, 6, False)
    assert "paper_only" in source


def test_derive_no_oa_via_cff_mandate_4():
    conn = _conn_with([(r"FROM project_publis", _proj_rows(
        {"proj_id": 100, "project_code": "X", "mandate_id": 4},
    ))])
    paper, data, embargo, source, missing = pub_db.derive_oa_requirement(conn, 1)
    assert (paper, data, embargo, missing) == (False, False, None, False)
    assert "no_oa" in source


def test_derive_data_required_via_aei_pattern_only():
    """Pub 3092-style: AEI 2022+ project_code with NULL mandate."""
    conn = _conn_with([(r"FROM project_publis", _proj_rows(
        {"proj_id": 1410, "project_code": "PID2022-137977OB-I00", "mandate_id": None},
    ))])
    paper, data, embargo, source, missing = pub_db.derive_oa_requirement(conn, 3092)
    assert (paper, data, embargo, missing) == (True, True, 0, False)
    assert "PID" not in source  # source uses labels, not codes
    assert "proj=1410:data(0mo)" in source


def test_derive_mandate_missing_when_all_unknown():
    conn = _conn_with([(r"FROM project_publis", _proj_rows(
        {"proj_id": 100, "project_code": "MDM-2017-0720", "mandate_id": None},
        {"proj_id": 101, "project_code": "100010434", "mandate_id": None},
    ))])
    paper, data, embargo, source, missing = pub_db.derive_oa_requirement(conn, 1)
    assert paper is None
    assert data is None
    assert missing is True


def test_derive_no_project_links_at_all():
    conn = _conn_with([(r"FROM project_publis", [])])
    paper, data, embargo, source, missing = pub_db.derive_oa_requirement(conn, 1)
    assert (paper, data, embargo, missing) == (None, None, None, True)
    assert source == "no project_publis rows"


def test_derive_multi_project_pub_3092_shape():
    """Pub 3092: AEI projects override unknowns; data is required."""
    conn = _conn_with([(r"FROM project_publis", _proj_rows(
        {"proj_id": 1152, "project_code": "101069356", "mandate_id": None},
        {"proj_id": 505, "project_code": "MDM-2017-0720", "mandate_id": None},
        {"proj_id": 1410, "project_code": "PID2022-137977OB-I00", "mandate_id": None},
        {"proj_id": 1296, "project_code": "PDC2022-133345-I00", "mandate_id": None},
        {"proj_id": 682, "project_code": "2019-FELL-000018-01", "mandate_id": None},
        {"proj_id": 916, "project_code": "100010434", "mandate_id": None},
    ))])
    paper, data, embargo, source, missing = pub_db.derive_oa_requirement(conn, 3092)
    assert (paper, data, missing) == (True, True, False)
    assert embargo == 0  # AEI hits provide 0mo
    # all 6 projects appear in audit trace
    assert source.count("proj=") == 6
    assert "proj=1410:data" in source
    assert "proj=1296:data" in source
    assert "proj=505:unknown" in source


def test_derive_data_required_overrides_unknown_and_paper_only():
    """If any project says data, the publication is data-required."""
    conn = _conn_with([(r"FROM project_publis", _proj_rows(
        {"proj_id": 1, "project_code": None, "mandate_id": 3},  # paper-only
        {"proj_id": 2, "project_code": None, "mandate_id": 1},  # data
        {"proj_id": 3, "project_code": "weird-code", "mandate_id": None},  # unknown
    ))])
    paper, data, embargo, _, missing = pub_db.derive_oa_requirement(conn, 1)
    assert (paper, data, missing) == (True, True, False)
    assert embargo == 0  # min of [6, 0]


def test_derive_unknown_with_paper_only_yields_unknown_data():
    """Don't claim 'no data' when at least one project's status is unknown."""
    conn = _conn_with([(r"FROM project_publis", _proj_rows(
        {"proj_id": 1, "project_code": None, "mandate_id": 3},  # paper-only
        {"proj_id": 2, "project_code": "weird-code", "mandate_id": None},  # unknown
    ))])
    paper, data, embargo, _, missing = pub_db.derive_oa_requirement(conn, 1)
    assert paper is True            # paper_only contributed
    assert data is None             # ambiguous: unknown could be data
    assert embargo == 6
    assert missing is False         # not missing — we know paper_only on proj 1


# ── lookup_corresponding_author ──────────────────────────────────────

def test_lookup_corresponding_author_real_user():
    """publi_corr_auth.id_user → mdm_personal lookup."""
    conn = _conn_with([
        (r"FROM publi_corr_auth", {"id_user": 84}),
        (r"FROM mdm_personal", {"name": "ALCALÁ CAFFARENA MARÍA"}),
    ])
    name, email = pub_db.lookup_corresponding_author(conn, 3097)
    assert name == "ALCALÁ CAFFARENA MARÍA"
    assert email is None  # no email column in this DB


def test_lookup_corresponding_author_external_sentinel():
    """id_user=-1 = external corresponding author (e.g. pub 3092)."""
    conn = _conn_with([(r"FROM publi_corr_auth", {"id_user": -1})])
    assert pub_db.lookup_corresponding_author(conn, 3092) == (None, None)


def test_lookup_corresponding_author_no_row():
    conn = _conn_with([(r"FROM publi_corr_auth", None)])
    assert pub_db.lookup_corresponding_author(conn, 1) == (None, None)


def test_lookup_corresponding_author_user_id_null():
    conn = _conn_with([(r"FROM publi_corr_auth", {"id_user": None})])
    assert pub_db.lookup_corresponding_author(conn, 1) == (None, None)


# ── lookup_central_repositories ──────────────────────────────────────

def test_lookup_central_repositories_zenodo():
    conn = _conn_with([(r"FROM repo_publis", [{"name": "Zenodo", "code": "12345"}])])
    assert pub_db.lookup_central_repositories(conn, 1) == [("Zenodo", "12345")]


def test_lookup_central_repositories_non_zenodo():
    conn = _conn_with([(r"FROM repo_publis", [{"name": "PubMed", "code": "PMC123"}])])
    assert pub_db.lookup_central_repositories(conn, 1) == [("PubMed", "PMC123")]


def test_lookup_central_repositories_multiple():
    conn = _conn_with([(r"FROM repo_publis", [
        {"name": "Zenodo", "code": "12345"},
        {"name": "biorxiv", "code": "abc/def"},
    ])])
    assert pub_db.lookup_central_repositories(conn, 1) == [
        ("Zenodo", "12345"),
        ("biorxiv", "abc/def"),
    ]


def test_lookup_central_repositories_none():
    conn = _conn_with([(r"FROM repo_publis", [])])
    assert pub_db.lookup_central_repositories(conn, 1) == []


def test_lookup_central_repositories_handles_null_fields():
    conn = _conn_with([(r"FROM repo_publis", [{"name": None, "code": None}])])
    assert pub_db.lookup_central_repositories(conn, 1) == [("", "")]


# ── enrich_archive (integration of the four lookups) ─────────────────

def test_enrich_archive_pub_3092_shape():
    """Multi-project, AEI hits, no central repository, external author."""
    conn = _conn_with([
        (r"FROM publication WHERE id", {
            "id": 3092, "title": "Probing the Biological Identity",
            "doi": "10.1002/smll.202504135", "journal": "Small", "year": 2025,
        }),
        (r"FROM project_publis", [
            {"proj_id": 1410, "project_code": "PID2022-137977OB-I00", "mandate_id": None},
            {"proj_id": 505, "project_code": "MDM-2017-0720", "mandate_id": None},
        ]),
        (r"FROM publi_corr_auth", {"id_user": -1}),
        (r"FROM repo_publis", []),
    ])
    fields = pub_db.enrich_archive(conn, 3092)
    assert fields.pub_title == "Probing the Biological Identity"
    assert fields.pub_doi == "10.1002/smll.202504135"
    assert fields.pub_year == 2025
    assert fields.oa_data_required is True
    assert fields.oa_paper_required is True
    assert fields.max_embargo_months == 0
    assert fields.oa_mandate_missing is False
    assert fields.corresponding_author_name is None
    assert fields.corresponding_author_email is None
    assert fields.central_repository is None
    assert fields.central_repository_code is None
    assert fields.auto_zenodo_code is None


def test_enrich_archive_with_zenodo_central():
    conn = _conn_with([
        (r"FROM publication WHERE id", {
            "id": 1, "title": "T", "doi": "d", "journal": "j", "year": 2024,
        }),
        (r"FROM project_publis", [
            {"proj_id": 1, "project_code": None, "mandate_id": 1},
        ]),
        (r"FROM publi_corr_auth", {"id_user": 84}),
        (r"FROM mdm_personal", {"name": "Author Name"}),
        (r"FROM repo_publis", [{"name": "Zenodo", "code": "999"}]),
    ])
    fields = pub_db.enrich_archive(conn, 1)
    assert fields.central_repository == "Zenodo"
    assert fields.central_repository_code == "999"
    assert fields.auto_zenodo_code == "999"
    assert fields.corresponding_author_name == "Author Name"


def test_enrich_archive_with_non_zenodo_central_does_not_seed_zenodo_code():
    conn = _conn_with([
        (r"FROM publication WHERE id", {
            "id": 1, "title": "T", "doi": "d", "journal": "j", "year": 2024,
        }),
        (r"FROM project_publis", [
            {"proj_id": 1, "project_code": None, "mandate_id": 1},
        ]),
        (r"FROM publi_corr_auth", None),
        (r"FROM repo_publis", [{"name": "PubMed", "code": "PMC9"}]),
    ])
    fields = pub_db.enrich_archive(conn, 1)
    assert fields.central_repository == "PubMed"
    assert fields.central_repository_code == "PMC9"
    assert fields.auto_zenodo_code is None  # only seeds for Zenodo


def test_enrich_archive_publication_not_found_yields_nullish_metadata():
    conn = _conn_with([
        (r"FROM publication WHERE id", None),
        (r"FROM project_publis", []),
        (r"FROM publi_corr_auth", None),
        (r"FROM repo_publis", []),
    ])
    fields = pub_db.enrich_archive(conn, 9999)
    assert fields.pub_title is None
    assert fields.pub_doi is None
    assert fields.oa_mandate_missing is True
