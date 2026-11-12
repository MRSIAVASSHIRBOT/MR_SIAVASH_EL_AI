from el_ai.main import _next_update_offset


def test_next_update_offset_advances_after_handled_update():
    assert _next_update_offset(None, 41) == 42
    assert _next_update_offset(42, 45) == 46


def test_next_update_offset_never_moves_backwards():
    assert _next_update_offset(50, 45) == 50


def test_next_update_offset_ignores_non_integer_update_ids():
    assert _next_update_offset(50, None) == 50
    assert _next_update_offset(None, "51") is None
