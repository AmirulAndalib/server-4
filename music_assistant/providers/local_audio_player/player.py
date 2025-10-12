"""Local Soundcard Player implementation following MA standard architecture."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any

from music_assistant_models.config_entries import ConfigEntry, ConfigValueOption
from music_assistant_models.enums import ConfigEntryType, PlaybackState, PlayerFeature, PlayerType
from music_assistant_models.errors import AudioError, PlayerCommandFailed, SetupFailedError
from music_assistant_models.player import DeviceInfo, PlayerMedia

from music_assistant.constants import (
    CONF_ENTRY_FLOW_MODE_ENFORCED,
    DEFAULT_PCM_FORMAT,
    create_sample_rates_config_entry,
)
from music_assistant.models.player import Player

from .constants import CLEANUP_TIMEOUT, CONF_DEVICE_ID, CONF_READ_AHEAD_BUFFER
from .helpers import AudioStreamHandler, get_available_devices, test_device_compatibility

if TYPE_CHECKING:
    from .provider import LocalSoundcardProvider

_LOGGER = logging.getLogger(__name__)


class LocalSoundcardPlayer(Player):
    """Local Soundcard Player following MA standard architecture."""

    def __init__(
        self,
        provider: LocalSoundcardProvider,
        player_id: str,
        device_info: dict[str, Any],
    ) -> None:
        """Initialize LocalSoundcardPlayer."""
        super().__init__(provider, player_id)

        self._device_info_data = device_info
        self._audio_handler: AudioStreamHandler | None = None
        self._stream_task: asyncio.Task[None] | None = None
        self._elapsed_time_task: asyncio.Task[None] | None = None
        self._playback_start_time: float | None = None
        self._paused_elapsed_time: float | None = None
        self._shutdown_event = asyncio.Event()

        # Set player attributes
        self._attr_type = PlayerType.PLAYER
        self._attr_name = f"Local Audio ({device_info['name']})"
        self._attr_available = True
        self._attr_powered = False
        self._attr_playback_state = PlaybackState.IDLE
        self._attr_volume_level: int | None = 100
        self._attr_volume_muted: bool | None = False

        # Set device info
        self._attr_device_info = DeviceInfo(
            model=device_info.get("name", "Unknown Audio Device"),
            manufacturer="Local System",
        )

        # Set supported features
        self._attr_supported_features = {
            PlayerFeature.POWER,
            PlayerFeature.VOLUME_SET,
            PlayerFeature.VOLUME_MUTE,
            PlayerFeature.PAUSE,
        }

    async def get_config_entries(self) -> list[ConfigEntry]:
        """Return player-specific config entries."""
        base_entries = await super().get_config_entries()

        # Get available devices for selection
        try:
            devices = await get_available_devices()
            device_options = [
                ConfigValueOption(
                    title=f"{device['name']} ({device['channels']} channels)", value=device["id"]
                )
                for device in devices
            ]
        except (AudioError, SetupFailedError) as err:
            _LOGGER.warning("Failed to get device options: %s", err)
            device_options = []

        return [
            *base_entries,
            CONF_ENTRY_FLOW_MODE_ENFORCED,
            create_sample_rates_config_entry(
                supported_sample_rates=[44100, 48000, 96000, 192000],
                supported_bit_depths=[16, 24, 32],
            ),
            ConfigEntry(
                key=CONF_DEVICE_ID,
                type=ConfigEntryType.STRING,
                label="Audio Device",
                description="Select the audio output device to use",
                default_value=self._device_info_data.get("id", 0),
                required=True,
                options=device_options,
            ),
            ConfigEntry(
                key=CONF_READ_AHEAD_BUFFER,
                type=ConfigEntryType.INTEGER,
                label="Buffer Size (ms)",
                description=(
                    "Audio buffer size in milliseconds (higher = more stable, lower = less latency)"
                ),
                default_value=2000,
                required=True,
                range=(500, 5000),
            ),
        ]

    async def power(self, powered: bool) -> None:
        """Handle POWER command."""
        if powered == self._attr_powered:
            return

        try:
            if powered:
                await self._setup_audio_handler()
                self._attr_powered = True
            else:
                await self._cleanup_audio_handler()
                self._attr_powered = False
        except (AudioError, SetupFailedError) as err:
            _LOGGER.error("Power command failed: %s", err)
            self._attr_powered = False
            await self._cleanup_audio_handler()
            raise PlayerCommandFailed(f"Power command failed: {err}") from err

        self.update_state()

    async def volume_set(self, volume_level: int) -> None:
        """Handle VOLUME_SET command."""
        volume_level = max(0, min(100, volume_level))
        self._attr_volume_level = volume_level
        if self._audio_handler is not None:
            self._audio_handler.set_volume(volume_level / 100.0)
        self.update_state()

    async def volume_mute(self, muted: bool) -> None:
        """Handle VOLUME MUTE command."""
        self._attr_volume_muted = muted
        if self._audio_handler is not None:
            self._audio_handler.set_muted(muted)
        self.update_state()

    async def play(self) -> None:
        """Handle PLAY command."""
        if not self._attr_powered:
            await self.power(True)

        if not self._audio_handler:
            raise PlayerCommandFailed("No audio handler available for play command")

        self._attr_playback_state = PlaybackState.PLAYING

        # Resume elapsed time tracking
        if self._paused_elapsed_time is not None:
            self._playback_start_time = time.time() - self._paused_elapsed_time
            self._paused_elapsed_time = None
        else:
            self._playback_start_time = time.time()

        # Start elapsed time tracking
        if not self._elapsed_time_task or self._elapsed_time_task.done():
            self._elapsed_time_task = asyncio.create_task(self._track_elapsed_time())

        # Resume audio playback
        self._audio_handler.play()
        self.update_state()

    async def pause(self) -> None:
        """Handle PAUSE command."""
        self._attr_playback_state = PlaybackState.PAUSED

        # Store current elapsed time
        if self._playback_start_time:
            self._paused_elapsed_time = time.time() - self._playback_start_time

        # Stop elapsed time tracking
        if self._elapsed_time_task and not self._elapsed_time_task.done():
            self._elapsed_time_task.cancel()

        # Pause audio playback
        if self._audio_handler:
            self._audio_handler.pause()

        self.update_state()

    async def stop(self) -> None:
        """Handle STOP command."""
        # Cancel stream task
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream_task

        # Cancel elapsed time tracking
        if self._elapsed_time_task and not self._elapsed_time_task.done():
            self._elapsed_time_task.cancel()

        # Reset state
        self._attr_playback_state = PlaybackState.IDLE
        self._attr_current_media = None
        self._attr_elapsed_time = None
        self._attr_active_source = None
        self._playback_start_time = None
        self._paused_elapsed_time = None

        self.update_state()

    async def seek(self, position: int) -> None:
        """Handle SEEK command."""
        if not self.current_media or not self.current_media.queue_id:
            raise PlayerCommandFailed("No media loaded for seeking")

        # For flow streams, seeking is handled by restarting the stream at the seek position
        # The queue controller will handle this via play_index
        await self.mass.player_queues.seek(self.current_media.queue_id, position)

    async def play_media(self, media: PlayerMedia) -> None:
        """Handle PLAY MEDIA command using MA standard architecture."""
        # Cancel any existing stream
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream_task

        # Ensure player is powered on
        if not self._attr_powered:
            await self.power(True)

        # Validate media has required information
        if not media.queue_item_id:
            raise PlayerCommandFailed("Media must have a queue_item_id for playback")

        # Set current media and state
        self._attr_current_media = media
        self._attr_playback_state = PlaybackState.PLAYING
        self._attr_active_source = self.active_source  # Use player's active_source
        self._attr_elapsed_time = 0
        self._attr_elapsed_time_last_updated = time.time()
        self.update_state()

        # Start streaming
        self._stream_task = self.mass.create_task(self._stream_media_standard(media))

        # Start elapsed time tracking
        if not self._elapsed_time_task or self._elapsed_time_task.done():
            self._elapsed_time_task = asyncio.create_task(self._track_elapsed_time())

        self.update_state()

    async def on_unload(self) -> None:
        """Handle player unload."""
        self._shutdown_event.set()
        await self._cleanup_audio_handler()
        await super().on_unload()

    async def _setup_audio_handler(self) -> None:
        """Set up the audio handler using device configuration."""
        if self._audio_handler:
            await self._audio_handler.stop()
            self._audio_handler = None

        # Get configuration values
        device_id = self._get_validated_config_int(
            CONF_DEVICE_ID, self._device_info_data.get("id", 0)
        )
        sample_rate = DEFAULT_PCM_FORMAT.sample_rate
        channels = DEFAULT_PCM_FORMAT.channels

        # Test device compatibility first
        if not await test_device_compatibility(device_id, sample_rate, channels, "float32"):
            raise SetupFailedError(
                f"Audio device {device_id} is not compatible with "
                f"{channels} channels at {sample_rate}Hz"
            )

        # Create audio handler
        self._audio_handler = AudioStreamHandler(
            device_id=device_id,
            sample_rate=sample_rate,
            channels=channels,
        )

        # Start the audio stream
        await self._audio_handler.start()

        # Set initial volume and mute state
        volume = self._attr_volume_level if self._attr_volume_level is not None else 100
        self._audio_handler.set_volume(volume / 100.0)
        self._audio_handler.set_muted(self._attr_volume_muted or False)

        _LOGGER.info(
            "Audio handler ready for device %d (%d channels, %d Hz)",
            device_id,
            channels,
            sample_rate,
        )

    async def _cleanup_audio_handler(self) -> None:
        """Clean up the audio handler."""
        # Cancel stream task
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._stream_task, timeout=CLEANUP_TIMEOUT)

        # Cancel elapsed time task
        if self._elapsed_time_task and not self._elapsed_time_task.done():
            self._elapsed_time_task.cancel()

        # Stop audio handler
        if self._audio_handler:
            try:
                await self._audio_handler.stop()
            except AudioError as err:
                _LOGGER.error("Error stopping audio handler: %s", err)
            finally:
                self._audio_handler = None

        # Reset state
        self._attr_playback_state = PlaybackState.IDLE
        self._playback_start_time = None
        self._paused_elapsed_time = None

    async def _stream_media_standard(self, media: PlayerMedia) -> None:
        """Stream media using MA standard architecture."""
        if not self._audio_handler:
            raise PlayerCommandFailed("No audio handler available")

        # Use the player's active source (queue_id)
        qid = self.active_source
        if not qid:
            raise PlayerCommandFailed("No active queue for playback")

        try:
            _LOGGER.debug("Starting stream for: %s", media.uri)

            # Get queue and item
            queue = self.mass.player_queues.get(qid)
            start_queue_item = self.mass.player_queues.get_item(qid, media.queue_item_id)

            if not queue:
                raise PlayerCommandFailed(f"Queue not found: {qid}")
            if not start_queue_item:
                raise PlayerCommandFailed(f"Queue item not found: {media.queue_item_id}")

            # Set initial state - but let MA manage progression
            self._attr_current_media = media
            self._attr_playback_state = PlaybackState.PLAYING
            self._attr_active_source = media.queue_id
            self._attr_elapsed_time = 0  # Set once at start
            self._attr_elapsed_time_last_updated = time.time()  # Set once at start
            self.update_state()

            # Get the audio stream from MA
            audio_source = self.mass.streams.get_queue_flow_stream(
                queue=queue,
                start_queue_item=start_queue_item,
                pcm_format=DEFAULT_PCM_FORMAT,
            )

            # Consume stream with backpressure to maintain real-time playback
            async for audio_chunk in audio_source:
                if not self._audio_handler or not self._audio_handler.is_playing:
                    break

                # Write audio chunk
                await self._audio_handler.write_audio(audio_chunk)

                # Apply backpressure to prevent consuming faster than real-time
                while self._audio_handler.queue_size > 10:
                    await asyncio.sleep(0.1)
                    if not self._audio_handler or not self._audio_handler.is_playing:
                        break

            _LOGGER.debug("Stream completed for %s", self.player_id)

        except asyncio.CancelledError:
            _LOGGER.debug("Stream cancelled for %s", self.player_id)
            raise
        except (AudioError, PlayerCommandFailed):
            raise
        except Exception as err:
            _LOGGER.error("Stream failed for %s: %s", self.player_id, err)
            await self.stop()
            raise PlayerCommandFailed(f"Stream failed: {err}") from err

    def _get_validated_config_int(self, key: str, default: int) -> int:
        """Get and validate integer configuration value."""
        try:
            value = self.config.get_value(key, default)
            if isinstance(value, (int, float, str)):
                return int(value)
            else:
                _LOGGER.warning(
                    "Invalid config value type for %s: %s, using default %d",
                    key,
                    type(value),
                    default,
                )
                return default
        except (ValueError, TypeError):
            _LOGGER.warning("Invalid config value for %s, using default %d", key, default)
            return default

    async def _track_elapsed_time(self) -> None:
        """Track elapsed time during playback."""
        try:
            while (
                self._attr_playback_state == PlaybackState.PLAYING
                and self._playback_start_time
                and not self._shutdown_event.is_set()
            ):
                current_time = time.time()
                self._attr_elapsed_time = current_time - self._playback_start_time
                self.update_state()

                try:
                    await asyncio.wait_for(self._shutdown_event.wait(), timeout=1.0)
                    break
                except TimeoutError:
                    continue

        except asyncio.CancelledError:
            pass
