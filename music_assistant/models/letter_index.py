"""Data model for the alphabetic letter index of a library listing.

Stays server-local for now: the shape is only produced by the
``library_items/letter_index`` API command and consumed by the frontend
over JSON, so it does not need to live in music_assistant_models (yet).
"""

from __future__ import annotations

from dataclasses import dataclass, field

from mashumaro import DataClassDictMixin


@dataclass(kw_only=True)
class LetterBucket(DataClassDictMixin):
    """A single alphabetic bucket pointing at the first matching list item."""

    # The bucket label: an uppercase letter A-Z, or "#" for everything that
    # does not start with a letter (numbers, symbols, empty sort names).
    label: str
    # Zero-based offset of the first item of this bucket within the library
    # listing produced by library_items with the same filters/order.
    offset: int


@dataclass(kw_only=True)
class LetterIndex(DataClassDictMixin):
    """Alphabetic jump index for a (filtered) library listing."""

    # Buckets that actually contain items, ordered ascending by offset.
    buckets: list[LetterBucket] = field(default_factory=list)
    # Total number of items in the (filtered) listing.
    total: int = 0
