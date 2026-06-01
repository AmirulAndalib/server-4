"""Constants for the Web Radio Broadcast player provider."""

from __future__ import annotations

from typing import Final

from music_assistant_models.config_entries import ConfigEntry

from music_assistant.constants import CONF_ENTRY_ENABLE_ICY_METADATA

# Provider config key holding the list of station display names.
CONF_STATIONS: Final[str] = "stations"

# Prefix used when deriving an MA player_id from a station slug.
PLAYER_ID_PREFIX: Final[str] = "webradio_"

# Public URL path prefix under which each station is served.
URL_PATH_PREFIX: Final[str] = "/webradio/"

# Per-player config key for the read-only URL display entry.
CONF_STREAM_URL_LABEL: Final[str] = "stream_url"

# Override the platform-wide CONF_ENTRY_ENABLE_ICY_METADATA default for web
# radio players: stations exist to advertise track titles to dumb clients,
# so ICY is on by default. We pick "basic" for the lower-latency 16 KB
# metaint - "full" adds a StreamURL field for cover art but raises the
# server-side chunk buffer to ~8 s, slowing every skip; users with clients
# that render artwork can opt into "full" in the per-player settings.
CONF_ENTRY_ENABLE_ICY_METADATA_BASIC: Final[ConfigEntry] = ConfigEntry.from_dict(
    {**CONF_ENTRY_ENABLE_ICY_METADATA.to_dict(), "default_value": "basic"}
)
