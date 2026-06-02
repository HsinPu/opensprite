import base64

from opensprite.tools.saved_media import load_saved_media_data_url, resolve_media_items
from opensprite.tools.result_status import classify_tool_result_status


def test_load_saved_media_data_url_reads_workspace_file(tmp_path):
    media_dir = tmp_path / "images"
    media_dir.mkdir()
    (media_dir / "inbound.png").write_bytes(b"png-bytes")

    result = load_saved_media_data_url(
        tmp_path,
        "images/inbound.png",
        supported_mime_types={"image/png"},
    )

    assert result == "data:image/png;base64," + base64.b64encode(b"png-bytes").decode("utf-8")


def test_load_saved_media_data_url_rejects_outside_workspace(tmp_path):
    workspace = tmp_path / "session"
    workspace.mkdir()
    (tmp_path / "outside.png").write_bytes(b"png-bytes")

    result = load_saved_media_data_url(
        workspace,
        "../outside.png",
        supported_mime_types={"image/png"},
    )

    assert result is None


def test_load_saved_media_data_url_rejects_unsupported_mime(tmp_path):
    media_dir = tmp_path / "images"
    media_dir.mkdir()
    (media_dir / "inbound.txt").write_text("not media")

    result = load_saved_media_data_url(
        tmp_path,
        "images/inbound.txt",
        supported_mime_types={"image/png"},
    )

    assert result is None


def test_resolve_media_items_returns_current_items_without_path():
    items, error = resolve_media_items(
        current_items=["data:image/png;base64,abc"],
        workspace_resolver=None,
        media_path="",
        media_label="image",
        supported_mime_types={"image/png"},
    )

    assert items == ["data:image/png;base64,abc"]
    assert error is None


def test_resolve_media_items_reports_missing_workspace_resolver():
    items, error = resolve_media_items(
        current_items=None,
        workspace_resolver=None,
        media_path="images/inbound.png",
        media_label="image",
        supported_mime_types={"image/png"},
    )

    assert items == []
    status = classify_tool_result_status(error)
    assert status.ok is False
    assert status.error_type == "SavedMediaError"
    assert status.category == "session_workspace_unavailable"
    assert "no session workspace is active" in status.error
