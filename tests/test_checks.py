"""Tests for the shared archive-state checks (checks.py)."""

from oa_tracker import checks, status as st


def _archive(**over):
    base = {
        "publication_id": "3259",
        "status": st.OPEN_INACTIVE,
        "user_done_flag": 1,
        "oa_data_required": 1,
        "oa_mandate_missing": 0,
        "data_contact_email": "lcardo@cicbiomagune.es",
    }
    base.update(over)
    return base


def test_empty_folder_with_done_tick_is_rejected():
    reasons = checks.reject_reasons(_archive())
    assert len(reasons) == 1
    assert "folder is still empty" in reasons[0]


def test_no_rejection_without_done_tick():
    assert checks.reject_reasons(_archive(user_done_flag=0)) == []


def test_no_rejection_once_files_arrived():
    assert checks.reject_reasons(_archive(status=st.OPEN_ACTIVE)) == []


def test_no_rejection_past_author_owned_statuses():
    assert checks.reject_reasons(_archive(status=st.OPEN_READY_FOR_ZENODO_DRAFT)) == []


def test_no_rejection_when_data_not_mandated():
    """A done tick on an empty paper-only/no-OA folder can mean 'nothing to
    upload' — never rejected automatically."""
    assert checks.reject_reasons(_archive(oa_data_required=0)) == []
    assert checks.reject_reasons(_archive(oa_data_required=None)) == []
    assert checks.reject_reasons(_archive(oa_mandate_missing=1)) == []


def test_new_rules_plug_in(monkeypatch):
    """The rule list is the extension point: one more function, one more reason."""
    monkeypatch.setattr(
        checks, "REJECT_RULES",
        checks.REJECT_RULES + (lambda a: "the README is empty",),
    )
    assert checks.reject_reasons(_archive()) == [
        "the publication folder is still empty (we cannot see any uploaded files)",
        "the README is empty",
    ]


def test_has_data_contact():
    assert checks.has_data_contact(_archive())
    assert not checks.has_data_contact(_archive(data_contact_email="TBD"))
    assert not checks.has_data_contact(_archive(data_contact_email=""))
    assert not checks.has_data_contact(_archive(data_contact_email=None))


def test_missing_package_items():
    assert checks.missing_package_items({
        "package_has_zip": 1, "package_has_readme": 0, "package_has_manuscript": None,
    }) == ["README.txt", "manuscript"]
    assert checks.missing_package_items({
        "package_has_zip": 1, "package_has_readme": 1, "package_has_manuscript": 1,
    }) == []
