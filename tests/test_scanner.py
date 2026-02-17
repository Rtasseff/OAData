"""Tests for scanner module."""

from oa_tracker.db import get_archive, get_connection
from oa_tracker.scanner import scan_folders


def test_scan_empty_root(test_config):
    result = scan_folders(test_config)
    assert result.summary == "  No folders found."


def test_scan_new_inactive_folder(test_config):
    (test_config.sharepoint_root / "PUB001").mkdir()
    result = scan_folders(test_config)
    assert "PUB001" in result.new_inactive

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB001")
        assert archive["status"] == "OPEN_INACTIVE"


def test_scan_new_active_folder(test_config):
    pub_dir = test_config.sharepoint_root / "PUB002"
    pub_dir.mkdir()
    (pub_dir / "data.zip").write_text("content")

    result = scan_folders(test_config)
    assert "PUB002" in result.new_active

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB002")
        assert archive["status"] == "OPEN_ACTIVE"
        assert archive["became_active_at"] is not None
        assert archive["next_reminder_at"] is not None


def test_scan_activation(test_config):
    pub_dir = test_config.sharepoint_root / "PUB003"
    pub_dir.mkdir()

    # First scan: inactive
    result = scan_folders(test_config)
    assert "PUB003" in result.new_inactive

    # Add a file
    (pub_dir / "readme.txt").write_text("hello")

    # Second scan: activated
    result = scan_folders(test_config)
    assert "PUB003" in result.activated

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB003")
        assert archive["status"] == "OPEN_ACTIVE"


def test_scan_missing_folder(test_config):
    pub_dir = test_config.sharepoint_root / "PUB004"
    pub_dir.mkdir()
    (pub_dir / "data.txt").write_text("stuff")

    scan_folders(test_config)

    # Remove the folder
    (pub_dir / "data.txt").unlink()
    pub_dir.rmdir()

    result = scan_folders(test_config)
    assert "PUB004" in result.missing

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB004")
        assert archive["unexpected_missing_folder"] == 1
        assert archive["missing_folder_detected_at"] is not None


def test_scan_missing_folder_reappears(test_config):
    pub_dir = test_config.sharepoint_root / "PUB005"
    pub_dir.mkdir()
    (pub_dir / "data.txt").write_text("stuff")
    scan_folders(test_config)

    # Remove
    (pub_dir / "data.txt").unlink()
    pub_dir.rmdir()
    scan_folders(test_config)

    # Reappear
    pub_dir.mkdir()
    (pub_dir / "data.txt").write_text("back")
    scan_folders(test_config)

    with get_connection(test_config.database) as conn:
        archive = get_archive(conn, "PUB005")
        assert archive["unexpected_missing_folder"] == 0


def test_scan_nonexistent_root(test_config):
    import shutil
    shutil.rmtree(test_config.sharepoint_root)
    result = scan_folders(test_config)
    assert len(result.errors) == 1
