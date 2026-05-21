"""
Web Radio Broadcast player provider for Music Assistant.

Publishes each configured "station" as a virtual MA player whose queue is
served on a stable HTTP URL. Dumb internet-radio devices, VLC, browsers and
similar clients can tune in directly by URL without going through Home
Assistant or the MPD add-on.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import ConfigEntryType, ProviderFeature

from .constants import CONF_STATIONS
from .provider import WebRadioProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigValueType, ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType


SUPPORTED_FEATURES: set[ProviderFeature] = {ProviderFeature.REMOVE_PLAYER}


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize the provider instance."""
    return WebRadioProvider(mass, manifest, config, SUPPORTED_FEATURES)


async def get_config_entries(
    mass: MusicAssistant,
    instance_id: str | None = None,
    action: str | None = None,
    values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    """Return the provider-level config entries."""
    del mass, instance_id, action, values
    return (
        ConfigEntry(
            key=CONF_STATIONS,
            type=ConfigEntryType.STRING,
            label="Stations",
            description=(
                "One station per line. Each station appears as a virtual MA "
                "player with its own queue, served on its own HTTP URL. "
                "Renaming a station changes its URL; the URL is shown on "
                "the station's player settings page."
            ),
            default_value=[],
            required=False,
            multi_value=True,
        ),
    )
