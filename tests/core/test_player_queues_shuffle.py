"""Regression tests for shuffle behavior in the player queue."""

import random
from typing import cast
from unittest.mock import MagicMock

from music_assistant_models.queue_item import QueueItem

from music_assistant.controllers.player_queues import PlayerQueuesController


def _make_items(count: int) -> list[QueueItem]:
    """Build a list of distinct queue items for shuffling."""
    return [
        QueueItem(queue_id="q", queue_item_id=f"item_{i}", name=f"Track {i}", duration=200)
        for i in range(count)
    ]


def _fake_controller() -> tuple[MagicMock, list[QueueItem]]:
    """Return a controller stand-in and the list that captures the loaded items."""
    fake = MagicMock()
    fake._queue_items = {"q": []}
    captured: list[QueueItem] = []
    fake.update_items = MagicMock(side_effect=lambda _qid, items: captured.extend(items))
    return fake, captured


async def test_load_pins_start_item_when_shuffling() -> None:
    """With keep_first, the requested start item stays at index 0 while the rest is shuffled."""
    random.seed(1234)
    fake, captured = _fake_controller()
    items = _make_items(25)
    start_item = items[0]
    await PlayerQueuesController.load(
        cast("PlayerQueuesController", fake),
        "q",
        queue_items=items,
        keep_remaining=False,
        keep_played=False,
        shuffle=True,
        keep_first=True,
    )
    assert captured[0] is start_item
    # all items are preserved, none dropped or duplicated
    assert {item.queue_item_id for item in captured} == {item.queue_item_id for item in items}
    # the tail is actually shuffled, not left in its original order
    assert [item.queue_item_id for item in captured[1:]] != [
        item.queue_item_id for item in items[1:]
    ]


async def test_load_shuffles_full_batch_without_keep_first() -> None:
    """Without keep_first, the whole batch (including the first item) is shuffled."""
    random.seed(1234)
    fake, captured = _fake_controller()
    items = _make_items(25)
    await PlayerQueuesController.load(
        cast("PlayerQueuesController", fake),
        "q",
        queue_items=items,
        keep_remaining=False,
        keep_played=False,
        shuffle=True,
    )
    assert {item.queue_item_id for item in captured} == {item.queue_item_id for item in items}
    assert [item.queue_item_id for item in captured] != [item.queue_item_id for item in items]
