"""Local Soundcard Player Provider initialization for Music Assistant."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import ConfigEntryType
from music_assistant_models.errors import SetupFailedError

from .constants import CONF_AUTO_START_PLAYERS
from .provider import LocalSoundcardProvider

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigValueType, ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant import MusicAssistant
    from music_assistant.models import ProviderInstanceType

_LOGGER = logging.getLogger(__name__)


async def setup(
    mass: MusicAssistant, manifest: ProviderManifest, config: ProviderConfig
) -> ProviderInstanceType:
    """Initialize provider instance with given configuration."""
    try:
        return LocalSoundcardProvider(mass, manifest, config)
    except Exception as err:
        _LOGGER.error("Failed to setup Local Soundcard Provider: %s", err)
        raise SetupFailedError(f"Provider setup failed: {err}") from err


async def get_config_entries(
    mass: MusicAssistant,  # noqa: ARG001
    instance_id: str | None = None,  # noqa: ARG001
    action: str | None = None,  # noqa: ARG001
    values: dict[str, ConfigValueType] | None = None,  # noqa: ARG001
) -> tuple[ConfigEntry, ...]:
    """
    Return Config entries to setup this provider.

    instance_id: id of an existing provider instance (None if new instance setup).
    action: [optional] action key called from config entries UI.
    values: the (intermediate) raw values for config entries sent with the action.
    """
    return (
        ConfigEntry(
            key=CONF_AUTO_START_PLAYERS,
            type=ConfigEntryType.BOOLEAN,
            label="Auto-start players",
            description=(
                "Automatically power on local soundcard players when Music Assistant starts"
            ),
            default_value=True,
            required=False,
        ),
    )
