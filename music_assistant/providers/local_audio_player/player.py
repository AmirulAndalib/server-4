"""Local Soundcard Player implementation following MA standard architecture."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from time import time
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
            raise PlayerCommandFailed("No audio output available for play command")

        self._attr_playback_state = PlaybackState.PLAYING

        # Resume audio playback
        # Note: In flow mode, elapsed time is tracked by queue controller
        self._audio_handler.play()

        self.update_state()

    async def pause(self) -> None:
        """Handle PAUSE command."""
        self._attr_playback_state = PlaybackState.PAUSED

        # Pause audio playback
        # Note: In flow mode, elapsed time is tracked by queue controller
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

        # Stop audio output
        if self._audio_handler:
            await self._audio_handler.stop()

        # Reset state
        self._attr_playback_state = PlaybackState.IDLE
        self._attr_current_media = None
        self._attr_active_source = None

        self.update_state()

    async def seek(self, position: int) -> None:
        """Handle SEEK command."""
        if not self.current_media or not self.active_source:
            raise PlayerCommandFailed("No media loaded for seeking")

        await self.mass.player_queues.seek(self.active_source, position)

    async def play_media(self, media: PlayerMedia) -> None:
        """Handle PLAY MEDIA command using FIFO approach."""
        # Stop playback and clear buffer before switching tracks
        if self._audio_handler:
            self._audio_handler.pause()
            self._audio_handler.clear_buffer()

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
        self._attr_active_source = self.active_source
        # In flow mode, queue controller tracks elapsed time, not the player
        self.update_state()

        # Start streaming using FIFO approach
        self._stream_task = self.mass.create_task(self._stream_media_standard(media))

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
        buffer_ms = self._get_validated_config_int(CONF_READ_AHEAD_BUFFER, 2000)
        sample_rate = DEFAULT_PCM_FORMAT.sample_rate
        channels = DEFAULT_PCM_FORMAT.channels

        # Calculate buffer size in frames from milliseconds
        buffer_size = int(sample_rate * buffer_ms / 1000)

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
            buffer_size=buffer_size,
        )

        # Start the audio stream but don't start playing yet
        await self._audio_handler.start()

        # Set initial volume and mute state
        volume = self._attr_volume_level if self._attr_volume_level is not None else 100
        self._audio_handler.set_volume(volume / 100.0)
        self._audio_handler.set_muted(self._attr_volume_muted or False)

        _LOGGER.info(
            "Audio handler ready for device %d (%d channels, %d Hz, buffer %d ms)",
            device_id,
            channels,
            sample_rate,
            buffer_ms,
        )

    async def _cleanup_audio_handler(self) -> None:
        """Clean up the audio output."""
        # Cancel stream task
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            with contextlib.suppress(TimeoutError, asyncio.CancelledError):
                await asyncio.wait_for(self._stream_task, timeout=CLEANUP_TIMEOUT)

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

    async def _stream_media_standard(self, media: PlayerMedia) -> None:
        """Stream media using MA standard architecture."""
        if not self._audio_handler:
            raise PlayerCommandFailed("No audio handler available")

        qid = self.active_source
        if not qid:
            raise PlayerCommandFailed("No active queue for playback")

        try:
            _LOGGER.debug("Starting MA standard stream for: %s", media.uri)

            queue = self.mass.player_queues.get(qid)
            start_queue_item = self.mass.player_queues.get_item(qid, media.queue_item_id)

            if not queue:
                raise PlayerCommandFailed(f"Queue not found: {qid}")
            if not start_queue_item:
                raise PlayerCommandFailed(f"Queue item not found: {media.queue_item_id}")

            # Initialize playback state
            self._init_playback_state(media, qid, start_queue_item)

            # Get the audio stream from MA
            audio_source = self.mass.streams.get_queue_flow_stream(
                queue=queue,
                start_queue_item=start_queue_item,
                pcm_format=DEFAULT_PCM_FORMAT,
            )

            # Stream audio with pre-buffering
            await self._consume_audio_stream(audio_source)

        except asyncio.CancelledError:
            _LOGGER.debug("Stream cancelled for %s", self.player_id)
            raise
        except (AudioError, PlayerCommandFailed):
            raise
        except Exception as err:
            _LOGGER.error("Stream failed for %s: %s", self.player_id, err)
            await self.stop()
            raise PlayerCommandFailed(f"Stream failed: {err}") from err

    def _init_playback_state(self, media: PlayerMedia, qid: str, start_queue_item: Any) -> None:
        """Initialize playback state with metadata."""
        self._attr_current_media = media
        self._attr_playback_state = PlaybackState.PLAYING
        self._attr_active_source = qid
        # In flow mode, set elapsed time to 0 and timestamp
        # corrected_elapsed_time property will calculate real-time advancement
        self._attr_elapsed_time = 0
        self._attr_elapsed_time_last_updated = time()

        if start_queue_item.media_item:
            self._attr_stream_title = start_queue_item.name
            self._attr_stream_details = start_queue_item.media_item.metadata.description or ""
        else:
            self._attr_stream_title = media.title or start_queue_item.name
            self._attr_stream_details = ""

        self.update_state()

    async def _consume_audio_stream(self, audio_source: Any) -> None:
        """Consume audio stream with pre-buffering and real-time pacing."""
        chunk_count = 0
        pre_buffer_chunks = 50  # Pre-buffer enough to start smoothly
        target_buffer_size = 100  # Keep buffer at ~10 seconds during playback
        _LOGGER.debug("Pre-filling audio buffer...")

        # Pre-fill buffer before starting playback
        async for audio_chunk in audio_source:
            if not self._audio_handler:
                break

            await self._audio_handler.write_audio(audio_chunk)
            chunk_count += 1

            if chunk_count >= pre_buffer_chunks:
                _LOGGER.debug("Buffer pre-filled with %d chunks, starting playback", chunk_count)
                self._audio_handler.play()
                break

        # Continue consuming stream with real-time pacing (like ffmpeg -re)
        # Only consume when buffer needs refilling to prevent over-consumption
        async for audio_chunk in audio_source:
            if not self._audio_handler:
                break

            # Wait if buffer is full enough - this paces consumption to ~real-time
            # Similar to how ffmpeg -re works
            while self._audio_handler and self._audio_handler.queue_size >= target_buffer_size:
                await asyncio.sleep(0.1)  # Check every 100ms
                if not self._audio_handler:
                    break

            if self._audio_handler:
                await self._audio_handler.write_audio(audio_chunk)
                chunk_count += 1

        _LOGGER.debug("Stream completed - consumed %d total chunks", chunk_count)

        # Wait for buffered audio to finish playing
        if self._audio_handler and self._audio_handler.is_playing:
            _LOGGER.debug("Waiting for buffered audio to drain...")
            while self._audio_handler.queue_size > 0:
                await asyncio.sleep(0.1)
            _LOGGER.debug("Audio buffer drained")

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
