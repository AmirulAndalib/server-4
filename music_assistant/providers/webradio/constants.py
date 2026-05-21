"""Constants for the Web Radio Broadcast player provider."""

from __future__ import annotations

from typing import Final

from music_assistant_models.config_entries import ConfigEntry

from music_assistant.constants import CONF_ENTRY_ENABLE_ICY_METADATA

CONF_STATIONS: Final[str] = "stations"
"""Provider config key holding the list of station display names."""

PLAYER_ID_PREFIX: Final[str] = "webradio_"
"""Prefix used when deriving an MA player_id from a station slug."""

URL_PATH_PREFIX: Final[str] = "/webradio/"
"""Public URL path prefix under which each station is served."""

CONF_STREAM_URL_LABEL: Final[str] = "stream_url"
"""Per-player config key for the read-only URL display entry."""

# Web radio stations exist specifically to advertise track titles to dumb
# clients, so ICY metadata is on by default here (the platform-wide default
# is "disabled" because most MA players are smart receivers that already
# know what they are playing).
CONF_ENTRY_ENABLE_ICY_METADATA_BASIC: Final[ConfigEntry] = ConfigEntry.from_dict(
    {**CONF_ENTRY_ENABLE_ICY_METADATA.to_dict(), "default_value": "basic"}
)
