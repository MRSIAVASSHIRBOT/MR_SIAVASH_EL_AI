from el_ai.main import _advance_update_cursor, _next_update_offset
from el_ai.telegram.cursor import UpdateCursor


def test_next_update_offset_advances_after_handled_update():
    assert _next_update_offset(None, 41) == 42
    assert _next_update_offset(42, 45) == 46


def test_next_update_offset_never_moves_backwards():
    assert _next_update_offset(50, 45) == 50


def test_next_update_offset_ignores_non_integer_update_ids():
    assert _next_update_offset(50, None) == 50
    assert _next_update_offset(None, "51") is None



def test_boolean_update_ids_are_not_treated_as_integer_offsets():
    assert _next_update_offset(50, True) == 50
    assert _next_update_offset(None, False) is None


def test_update_offset_is_persisted_only_after_it_advances(tmp_path):
    cursor = UpdateCursor(tmp_path / "telegram-update-offset.json")

    assert _advance_update_cursor(cursor, None, 41) == 42
    assert cursor.load() == 42
    assert _advance_update_cursor(cursor, 42, 40) == 42
    assert cursor.load() == 42
