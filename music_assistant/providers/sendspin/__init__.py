"""
Player Provider for the Sendspin Audio Protocol.

https://github.com/Sendspin-Protocol/spec
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import ConfigEntryType

from music_assistant.constants import CONF_ENTRY_MANUAL_DISCOVERY_IPS
from music_assistant.providers.sendspin.constants import CONF_DISABLE_WEB_PLAYERS
from music_assistant.providers.sendspin.provider import SendspinProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigValueType, ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant.mass import MusicAssistant
    from music_assistant.models import ProviderInstanceType


CONF_ENTRY_DISABLE_WEB_PLAYERS = ConfigEntry(
    key=CONF_DISABLE_WEB_PLAYERS,
    type=ConfigEntryType.BOOLEAN,
    label="Do not create players for web/app clients",
    description="When enabled, players are not created for connecting web browser, "
    "PWA or mobile app clients. The web interface can no longer be used as a "
    "playback target on the device it runs on.",
    default_value=False,
    required=False,
    advanced=True,
)


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider(instance) with given configuration."""
    return SendspinProvider(mass, manifest, config)


async def get_config_entries(
    mass: MusicAssistant,
    instance_id: str | None = None,
    action: str | None = None,
    values: dict[str, ConfigValueType] | None = None,
) -> tuple[ConfigEntry, ...]:
    """
    Return Config entries to setup this provider.

    instance_id: id of an existing provider instance (None if new instance setup).
    action: [optional] action key called from config entries UI.
    values: the (intermediate) raw values for config entries sent with the action.
    """
    # ruff: noqa: ARG001
    return (CONF_ENTRY_MANUAL_DISCOVERY_IPS, CONF_ENTRY_DISABLE_WEB_PLAYERS)
