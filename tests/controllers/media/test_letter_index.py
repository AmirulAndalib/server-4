"""Integration tests for the alphabetic letter index (``library_items/letter_index``).

Uses the ``mass`` fixture from ``tests/conftest.py`` which creates a full
MusicAssistant instance with a real SQLite database in a temporary directory.
The key invariant verified here is that the bucket offsets line up exactly with
the positions of the items returned by ``library_items`` using the same filters
and ordering.
"""

from __future__ import annotations

from uuid import uuid4

from music_assistant_models.enums import AlbumType
from music_assistant_models.media_items import Album, Artist, ProviderMapping

from music_assistant.helpers.compare import create_safe_string
from music_assistant.mass import MusicAssistant


def _library_provider_mapping() -> set[ProviderMapping]:
    """Create a provider mapping set with in_library=True and a unique provider_item_id."""
    return {
        ProviderMapping(
            item_id=uuid4().hex,
            provider_domain="library",
            provider_instance="library",
            in_library=True,
        )
    }


async def _add_artist(mass: MusicAssistant, name: str, *, favorite: bool = False) -> Artist:
    """Add a minimal artist to the library."""
    artist = Artist(
        item_id="0",
        provider="library",
        name=name,
        favorite=favorite,
        provider_mappings=_library_provider_mapping(),
    )
    return await mass.music.artists.add_item_to_library(artist)


async def _add_album(mass: MusicAssistant, name: str, album_type: AlbumType) -> Album:
    """Add a minimal album to the library."""
    album = Album(
        item_id="0",
        provider="library",
        name=name,
        album_type=album_type,
        provider_mappings=_library_provider_mapping(),
    )
    return await mass.music.albums.add_item_to_library(album)


def _expected_label(sort_name: str | None) -> str:
    """Mirror the server-side bucketing on the normalized sort key."""
    leading = create_safe_string(sort_name or "", True, True)[:1]
    return leading.upper() if "a" <= leading <= "z" else "#"


async def test_letter_index_offsets_align_with_listing(mass: MusicAssistant) -> None:
    """Each bucket offset points at the first item of that bucket in library_items order."""
    names = [
        "ABBA",
        "AC/DC",
        "Aphex Twin",
        "Beach Boys",
        "Beatles",
        "2Pac",  # number -> "#"
        "!!!",  # only symbols -> empty sort key -> "#"
        "Madonna",
        "Queen",
        "Zappa",
    ]
    for name in names:
        await _add_artist(mass, name)

    index = await mass.music.artists.library_items_letter_index()
    listing = await mass.music.artists.library_items(limit=1000)

    assert index.total == len(names) == len(listing)
    # buckets are ordered ascending by offset and only contain populated letters
    assert [b.offset for b in index.buckets] == sorted(b.offset for b in index.buckets)
    for bucket in index.buckets:
        # the item at the bucket offset must belong to that bucket ...
        assert _expected_label(listing[bucket.offset].sort_name) == bucket.label
        # ... and it must be the *first* such item in the listing
        first = next(
            i for i, item in enumerate(listing) if _expected_label(item.sort_name) == bucket.label
        )
        assert first == bucket.offset

    # "#" must absorb the number- and symbol-led entries and sort first
    labels = [b.label for b in index.buckets]
    assert labels[0] == "#"
    assert "A" in labels
    assert "Z" in labels


async def test_letter_index_empty_library(mass: MusicAssistant) -> None:
    """An empty library yields no buckets and a zero total."""
    index = await mass.music.artists.library_items_letter_index()
    assert index.total == 0
    assert index.buckets == []


async def test_letter_index_respects_favorite_filter(mass: MusicAssistant) -> None:
    """Filtering by favorite changes the offsets/total to match the filtered listing."""
    await _add_artist(mass, "Alpha", favorite=True)
    await _add_artist(mass, "Bravo", favorite=False)
    await _add_artist(mass, "Charlie", favorite=True)

    index = await mass.music.artists.library_items_letter_index(favorite=True)
    listing = await mass.music.artists.library_items(favorite=True, limit=1000)

    assert index.total == 2 == len(listing)
    assert [b.label for b in index.buckets] == ["A", "C"]
    for bucket in index.buckets:
        assert _expected_label(listing[bucket.offset].sort_name) == bucket.label


async def test_letter_index_albums_album_types_filter(mass: MusicAssistant) -> None:
    """The albums override honours the album_types filter, mirroring library_items."""
    await _add_album(mass, "Alpha Album", AlbumType.ALBUM)
    await _add_album(mass, "Sigma Single", AlbumType.SINGLE)
    await _add_album(mass, "Sierra Single", AlbumType.SINGLE)

    index = await mass.music.albums.library_items_letter_index(album_types=[AlbumType.SINGLE])
    listing = await mass.music.albums.library_items(album_types=[AlbumType.SINGLE], limit=1000)

    assert index.total == 2 == len(listing)
    assert [b.label for b in index.buckets] == ["S"]
    assert index.buckets[0].offset == 0
