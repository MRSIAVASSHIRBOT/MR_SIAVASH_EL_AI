from el_ai.polling import process_updates


class FakeApplication:
    def __init__(self):
        self.seen = []

    async def handle_update(self, update):
        self.seen.append(update["update_id"])


async def test_process_updates_advances_offset_after_each_valid_update():
    app = FakeApplication()

    next_offset = await process_updates(
        app,
        [{"update_id": 4}, {"update_id": 7}, {"update_id": True}, {"update_id": -1}],
        offset=3,
    )

    assert app.seen == [4, 7]
    assert next_offset == 8


async def test_process_updates_keeps_existing_offset_when_no_valid_updates_exist():
    app = FakeApplication()

    next_offset = await process_updates(app, [{"update_id": "bad"}], offset=12)

    assert app.seen == []
    assert next_offset == 12
