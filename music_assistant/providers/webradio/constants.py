"""Constants for the Web Radio Broadcast player provider."""

from __future__ import annotations

from typing import Final

CONF_STATIONS: Final[str] = "stations"
"""Provider config key holding the list of station display names."""

PLAYER_ID_PREFIX: Final[str] = "webradio_"
"""Prefix used when deriving an MA player_id from a station slug."""

URL_PATH_PREFIX: Final[str] = "/webradio/"
"""Public URL path prefix under which each station is served."""

CONF_STREAM_URL_LABEL: Final[str] = "stream_url"
"""Per-player config key for the read-only URL display entry."""
