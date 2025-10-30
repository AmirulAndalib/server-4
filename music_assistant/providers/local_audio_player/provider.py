"""Local Soundcard Player Provider for Music Assistant."""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from music_assistant_models.enums import PlaybackState, ProviderFeature
from music_assistant_models.errors import AudioError, SetupFailedError

from music_assistant.models.player_provider import PlayerProvider

from .constants import DOMAIN, POWER_CYCLE_DELAY
from .helpers import get_available_devices
from .player import LocalSoundcardPlayer

if TYPE_CHECKING:
    from music_assistant_models.config_entries import PlayerConfig, ProviderConfig
    from music_assistant_models.provider import ProviderManifest

    from music_assistant import MusicAssistant

_LOGGER = logging.getLogger(__name__)


class LocalSoundcardProvider(PlayerProvider):
    """Local Soundcard Player Provider for Music Assistant."""

    def __init__(
        self,
        mass: MusicAssistant,
        manifest: ProviderManifest,
        config: ProviderConfig,
    ) -> None:
        """Initialize LocalSoundcardProvider."""
        super().__init__(mass, manifest, config)
        self._players: dict[str, LocalSoundcardPlayer] = {}

    @property
    def supported_features(self) -> set[ProviderFeature]:
        """Return the features supported by this Provider."""
        return {ProviderFeature.REMOVE_PLAYER}

    async def handle_async_init(self) -> None:
        """Handle async initialization of the provider."""
        # Test basic functionality
        try:
            devices = await get_available_devices()
            if not devices:
                raise SetupFailedError("No compatible audio output devices found")
            _LOGGER.info("Found %d compatible audio output devices", len(devices))
        except (AudioError, SetupFailedError) as err:
            raise SetupFailedError(f"Failed to initialize audio system: {err}") from err

    async def loaded_in_mass(self) -> None:
        """Call after the provider has been loaded."""
        await super().loaded_in_mass()
        # Discover and register players for each available device
        await self.discover_players()

    async def unload(self, is_removed: bool = False) -> None:
        """Handle unload/close of the provider."""
        # Clean up all players
        for player in list(self._players.values()):
            await self.mass.players.unregister(player.player_id)
        self._players.clear()

    async def discover_players(self) -> None:
        """Discover and register local audio devices as players."""
        try:
            devices = await get_available_devices()
        except (AudioError, SetupFailedError) as err:
            _LOGGER.error("Failed to discover audio devices: %s", err)
            return

        for device in devices:
            player_id = f"{DOMAIN}_{device['id']}"

            if player_id in self._players:
                continue

            try:
                # Create player instance
                player = LocalSoundcardPlayer(
                    provider=self,
                    player_id=player_id,
                    device_info=device,
                )

                # Store player reference
                self._players[player_id] = player

                # Register with Music Assistant
                await self.mass.players.register_or_update(player)

            except (SetupFailedError, AudioError) as err:
                _LOGGER.error("Failed to register player for device %s: %s", device["name"], err)

    async def remove_player(self, player_id: str) -> None:
        """Remove a player from this provider."""
        if player_id in self._players:
            del self._players[player_id]
            await self.mass.players.unregister(player_id, permanent=True)

    def on_player_config_change(
        self,
        config: PlayerConfig,
        changed_keys: set[str],
    ) -> None:
        """Handle player configuration changes."""
        player_id = config.player_id
        if player := self._players.get(player_id):
            # If device-related config changed, restart the audio handler
            device_config_keys = {
                "device_id",
                "sample_rate",
                "channels",
                "buffer_size",
            }

            if device_config_keys.intersection(changed_keys):
                # Restart audio handler with new config
                if player.powered:
                    asyncio.create_task(self._restart_player_audio(player))

    async def _restart_player_audio(self, player: LocalSoundcardPlayer) -> None:
        """Restart a player's audio system with new configuration."""
        try:
            was_playing = player.playback_state == PlaybackState.PLAYING
            current_media = player.current_media

            # Power cycle to apply new config
            await player.power(False)
            await asyncio.sleep(POWER_CYCLE_DELAY)
            await player.power(True)

            # Resume playback if it was active
            if was_playing and current_media:
                await player.play_media(current_media)

        except (SetupFailedError, AudioError) as err:
            _LOGGER.error("Failed to restart player audio for %s: %s", player.player_id, err)
