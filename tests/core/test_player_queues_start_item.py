"""Tests for player queue `start_item` resolution and the podcast `latest` sentinel."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

import pytest
from music_assistant_models.errors import InvalidDataError
from music_assistant_models.media_items import (
    ItemMapping,
    Podcast,
    PodcastEpisode,
    Track,
)
from music_assistant_models.media_items.provider_mapping import ProviderMapping

from music_assistant.controllers.player_queues import (
    PlayerQueuesController,
    _start_item_matches,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _provider_mappings(item_id: str = "x") -> set[ProviderMapping]:
    return {ProviderMapping(item_id=item_id, provider_domain="test", provider_instance="test")}


def _make_podcast(name: str = "My Podcast", item_id: str = "pod1") -> Podcast:
    return Podcast(
        item_id=item_id,
        provider="test",
        name=name,
        provider_mappings=_provider_mappings(item_id),
    )


def _make_episode(name: str, position: int, item_id: str | None = None) -> PodcastEpisode:
    eid = item_id or f"ep{position}"
    return PodcastEpisode(
        item_id=eid,
        provider="test",
        name=name,
        position=position,
        podcast=ItemMapping(
            item_id="pod1",
            provider="test",
            name="My Podcast",
            media_type=Podcast.media_type,
        ),
        provider_mappings=_provider_mappings(eid),
    )


def _make_track(name: str, item_id: str | None = None) -> Track:
    tid = item_id or name.lower().replace(" ", "_")
    return Track(
        item_id=tid,
        provider="test",
        name=name,
        provider_mappings=_provider_mappings(tid),
    )


def _build_controller(episodes: list[PodcastEpisode]) -> PlayerQueuesController:
    """Build a bare controller instance with mocked `mass` for episode lookups.

    Uses `object.__new__` to bypass `__init__`, attaching only the attributes
    that `get_next_podcast_episodes` actually touches.
    """
    controller = object.__new__(PlayerQueuesController)
    controller.logger = MagicMock()

    async def _episodes_iter(*_args: object, **_kwargs: object) -> AsyncIterator[PodcastEpisode]:
        for ep in episodes:
            yield ep

    mass = MagicMock()
    mass.music.podcasts.episodes = _episodes_iter
    mass.music.get_resume_position = AsyncMock(return_value=(False, 0))
    controller.mass = mass
    return controller


# --- _start_item_matches ---


class TestStartItemMatches:
    """Tests for the `_start_item_matches` helper."""

    def test_exact_item_id(self) -> None:
        """Exact match against `item_id` is recognised."""
        track = _make_track("Some Song", item_id="abc123")
        assert _start_item_matches("abc123", track) is True

    def test_exact_uri(self) -> None:
        """Exact match against `uri` is recognised."""
        track = _make_track("Some Song", item_id="abc123")
        assert track.uri is not None
        assert _start_item_matches(track.uri, track) is True

    def test_substring_case_insensitive(self) -> None:
        """Case-insensitive substring of `name` matches."""
        track = _make_track("Enter Sandman")
        assert _start_item_matches("sandman", track) is True
        assert _start_item_matches("ENTER", track) is True

    def test_no_match(self) -> None:
        """Unrelated string does not match."""
        track = _make_track("Enter Sandman")
        assert _start_item_matches("nothing here", track) is False

    def test_item_without_name(self) -> None:
        """An empty name falls through and returns False."""
        track = _make_track("Anything")
        track.name = ""
        assert _start_item_matches("any", track) is False


# --- get_next_podcast_episodes: latest sentinel ---


class TestLatestSentinel:
    """Tests for the `latest` / `newest` sentinel for podcast `start_item`."""

    @pytest.mark.parametrize("sentinel", ["latest", "newest", "Latest", " NEWEST "])
    @pytest.mark.asyncio
    async def test_returns_only_most_recent_episode(self, sentinel: str) -> None:
        """The sentinel resolves to a single episode — the highest-positioned one."""
        episodes = [
            _make_episode("Episode 1", position=1),
            _make_episode("Episode 2", position=2),
            _make_episode("Episode 3", position=3),
        ]
        controller = _build_controller(episodes)
        podcast = _make_podcast()
        result = await controller.get_next_podcast_episodes(podcast, sentinel)
        assert len(result) == 1
        assert result[0].name == "Episode 3"
        assert result[0].position == 3

    @pytest.mark.asyncio
    async def test_refreshes_resume_position(self) -> None:
        """Resume position from the playlog is applied to the resolved episode."""
        episodes = [_make_episode("Only", position=1)]
        controller = _build_controller(episodes)
        controller.mass.music.get_resume_position = AsyncMock(  # type: ignore[method-assign]
            return_value=(False, 12345)
        )
        podcast = _make_podcast()
        result = await controller.get_next_podcast_episodes(podcast, "latest")
        assert result[0].fully_played is False
        assert result[0].resume_position_ms == 12345

    @pytest.mark.asyncio
    async def test_fully_played_zeroes_resume(self) -> None:
        """A fully-played episode is returned with `resume_position_ms == 0`."""
        episodes = [_make_episode("Only", position=1)]
        controller = _build_controller(episodes)
        controller.mass.music.get_resume_position = AsyncMock(  # type: ignore[method-assign]
            return_value=(True, 99999)
        )
        podcast = _make_podcast()
        result = await controller.get_next_podcast_episodes(podcast, "latest")
        assert result[0].fully_played is True
        assert result[0].resume_position_ms == 0

    @pytest.mark.asyncio
    async def test_empty_podcast_raises(self) -> None:
        """A podcast with no episodes raises `InvalidDataError`."""
        controller = _build_controller([])
        with pytest.raises(InvalidDataError):
            await controller.get_next_podcast_episodes(_make_podcast(), "latest")


# --- get_next_podcast_episodes: substring match ---


class TestSubstringMatch:
    """Tests for case-insensitive substring matching of an episode name."""

    @pytest.mark.asyncio
    async def test_returns_match_and_subsequent_episodes(self) -> None:
        """A substring of an episode name returns that episode plus all later ones."""
        episodes = [
            _make_episode("EP 1 - Intro", position=1),
            _make_episode("EP 2 - The Middle", position=2),
            _make_episode("EP 3 - Awesome Sauce", position=3),
            _make_episode("EP 4 - Outro", position=4),
        ]
        controller = _build_controller(episodes)
        result = await controller.get_next_podcast_episodes(_make_podcast(), "awesome")
        assert [e.name for e in result] == [
            "EP 3 - Awesome Sauce",
            "EP 4 - Outro",
        ]

    @pytest.mark.asyncio
    async def test_exact_item_id_still_works(self) -> None:
        """Exact `item_id` lookup is preserved alongside substring matching."""
        episodes = [
            _make_episode("EP 1", position=1, item_id="ep_first"),
            _make_episode("EP 2", position=2, item_id="ep_second"),
            _make_episode("EP 3", position=3, item_id="ep_third"),
        ]
        controller = _build_controller(episodes)
        result = await controller.get_next_podcast_episodes(_make_podcast(), "ep_second")
        assert [e.name for e in result] == ["EP 2", "EP 3"]
