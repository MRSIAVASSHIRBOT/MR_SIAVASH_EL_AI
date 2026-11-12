import json
import os
import stat

import pytest

from el_ai.telegram.cursor import UpdateCursor, UpdateCursorError


def test_cursor_loads_missing_file_as_uninitialized(tmp_path):
    assert UpdateCursor(tmp_path / "cursor.json").load() is None


def test_cursor_round_trips_next_offset_and_replaces_atomically(tmp_path):
    path = tmp_path / "cursor.json"
    cursor = UpdateCursor(path)

    cursor.save(42)
    assert cursor.load() == 42
    cursor.save(51)
    assert cursor.load() == 51
    assert json.loads(path.read_text(encoding="utf-8")) == {"next_offset": 51}


def test_cursor_file_is_private_on_posix(tmp_path):
    path = tmp_path / "cursor.json"
    UpdateCursor(path).save(9)

    if os.name != "nt":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600


@pytest.mark.parametrize("payload", [
    {},
    {"next_offset": -1},
    {"next_offset": True},
    {"next_offset": "12"},
    [],
    None,
])
def test_cursor_rejects_invalid_stored_values(tmp_path, payload):
    path = tmp_path / "cursor.json"
    path.write_text(json.dumps(payload), encoding="utf-8")

    with pytest.raises(UpdateCursorError):
        UpdateCursor(path).load()


@pytest.mark.parametrize("value", [-1, True, "12", None])
def test_cursor_rejects_invalid_values_to_save(tmp_path, value):
    with pytest.raises(ValueError):
        UpdateCursor(tmp_path / "cursor.json").save(value)


def test_cursor_rejects_symlink_when_supported(tmp_path):
    if not hasattr(os, "O_NOFOLLOW"):
        pytest.skip("O_NOFOLLOW is not available on this platform")
    target = tmp_path / "target.json"
    target.write_text('{"next_offset": 99}', encoding="utf-8")
    link = tmp_path / "cursor.json"
    link.symlink_to(target)

    with pytest.raises(UpdateCursorError):
        UpdateCursor(link).load()
