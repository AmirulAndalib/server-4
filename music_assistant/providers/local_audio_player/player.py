"""Local Soundcard Player implementation using FFmpeg subprocess."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from typing import TYPE_CHECKING, Any, cast

from music_assistant_models.config_entries import ConfigEntry
from music_assistant_models.enums import (
    ConfigEntryType,
    PlaybackState,
    PlayerFeature,
    PlayerType,
)
from music_assistant_models.errors import AudioError, PlayerCommandFailed, SetupFailedError
from music_assistant_models.player import DeviceInfo, PlayerMedia

from music_assistant.constants import (
    CONF_ENTRY_FLOW_MODE_ENFORCED,
    INTERNAL_PCM_FORMAT,
    create_sample_rates_config_entry,
)
from music_assistant.helpers.util import TaskManager
from music_assistant.models.player import Player

from .constants import CLEANUP_TIMEOUT, CONF_DEVICE_ID, CONF_READ_AHEAD_BUFFER
from .helpers import AudioStreamHandler, test_device_compatibility

if TYPE_CHECKING:
    from music_assistant_models.config_entries import ConfigValueType

    from .provider import LocalSoundcardProvider

_LOGGER = logging.getLogger(__name__)


class LocalSoundcardPlayer(Player):
    """Local Soundcard Player using FFmpeg subprocess for GIL-free audio."""

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
        self._ffmpeg_proc: asyncio.subprocess.Process | None = None
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
        self._attr_needs_poll = True
        self._attr_poll_interval = 1
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

    async def get_config_entries(
        self,
        action: str | None = None,
        values: dict[str, ConfigValueType] | None = None,
    ) -> list[ConfigEntry]:
        """Return player-specific config entries."""
        base_entries = await super().get_config_entries(action=action, values=values)

        return [
            *base_entries,
            CONF_ENTRY_FLOW_MODE_ENFORCED,
            create_sample_rates_config_entry(
                supported_sample_rates=[44100, 48000, 96000, 192000],
                supported_bit_depths=[16, 24, 32],
            ),
            ConfigEntry(
                key=CONF_READ_AHEAD_BUFFER,
                type=ConfigEntryType.INTEGER,
                label="Read Buffer Size (KB)",
                description=(
                    "Size of read buffer for FFmpeg subprocess in kilobytes. "
                    "Higher values provide more stability but slightly more latency."
                ),
                default_value=256,
                required=True,
                range=(64, 1024),
            ),
        ]

    async def poll(self) -> None:
        """Poll player for state updates."""
        await self.update_attributes()

    async def update_attributes(self) -> None:
        """Update player attributes during playback."""
        # Update elapsed time if playing
        if (
            self._attr_playback_state == PlaybackState.PLAYING
            and self._playback_start_time is not None
        ):
            self._attr_elapsed_time = time.time() - self._playback_start_time
            self._attr_elapsed_time_last_updated = time.time()

        # Call update_state once at the end
        self.update_state()

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

        # Resume elapsed time tracking (only if resuming from pause)
        if self._paused_elapsed_time is not None:
            self._playback_start_time = time.time() - self._paused_elapsed_time
            self._attr_elapsed_time = self._paused_elapsed_time
            self._attr_elapsed_time_last_updated = time.time()
            self._paused_elapsed_time = None

        # Resume audio playback
        self._audio_handler.play()
        self.update_state()

    async def pause(self) -> None:
        """Handle PAUSE command."""
        # Calculate elapsed time at pause moment
        if self._playback_start_time:
            self._paused_elapsed_time = time.time() - self._playback_start_time
            self._attr_elapsed_time = self._paused_elapsed_time
            self._attr_elapsed_time_last_updated = time.time()

        self._attr_playback_state = PlaybackState.PAUSED

        # Pause audio playback
        if self._audio_handler:
            self._audio_handler.pause()

        self.update_state()

    async def stop(self) -> None:
        """Handle STOP command."""
        # Stop audio handler first
        if self._audio_handler:
            try:
                await self._audio_handler.stop()
            except AudioError as err:
                _LOGGER.error("Error stopping audio handler: %s", err)

        # Then cancel the stream
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._stream_task

        # Kill FFmpeg
        await self._kill_ffmpeg_process()

        # Update state
        self._attr_playback_state = PlaybackState.IDLE
        self._attr_current_media = None
        self._attr_active_source = None
        self._attr_elapsed_time = 0
        self._playback_start_time = None
        self.update_state()

    async def seek(self, position: int) -> None:
        """Handle SEEK command."""
        if not self.current_media or not self.active_source:
            raise PlayerCommandFailed("No media loaded for seeking")

        await self.mass.player_queues.seek(self.active_source, position)

    async def play_media(self, media: PlayerMedia) -> None:
        """Handle PLAY MEDIA command using FFmpeg subprocess."""
        # Ensure audio handler is ready
        await self._ensure_audio_handler_ready()
        # Kill existing processes IMMEDIATELY and in parallel
        if self._ffmpeg_proc or self._stream_task:
            async with TaskManager(self.mass) as tg:
                if self._ffmpeg_proc:
                    tg.create_task(self._kill_ffmpeg_process())
                if self._stream_task and not self._stream_task.done():
                    self._stream_task.cancel()

        # Clear audio buffers to prevent glitches between tracks
        if self._audio_handler:
            self._audio_handler.clear_buffer()

        # Ensure player is powered on
        if not self._attr_powered:
            await self.power(True)

        # Validate media
        if not media.queue_item_id:
            raise PlayerCommandFailed("Media must have a queue_item_id for playback")

        # Set state and start immediately
        self._attr_current_media = media
        self._attr_playback_state = PlaybackState.PLAYING
        self._attr_active_source = self.active_source
        self._attr_elapsed_time = 0
        self._attr_elapsed_time_last_updated = time.time()
        self._playback_start_time = time.time()
        self._paused_elapsed_time = None
        self.update_state()

        # Start streaming
        self._stream_task = self.mass.create_task(self._stream_media_subprocess(media))
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

        # Get configuration values using base class config access
        device_id_value = self.config.get_value(CONF_DEVICE_ID, self._device_info_data.get("id", 0))
        device_id = int(cast("int", device_id_value))
        sample_rate = INTERNAL_PCM_FORMAT.sample_rate
        channels = INTERNAL_PCM_FORMAT.channels

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
        # Kill FFmpeg process
        if self._ffmpeg_proc:
            try:
                self._ffmpeg_proc.kill()
                await self._ffmpeg_proc.wait()
            except ProcessLookupError:
                pass
            self._ffmpeg_proc = None

        # Cancel stream task
        if self._stream_task and not self._stream_task.done():
            self._stream_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, asyncio.TimeoutError):
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

    async def _stream_media_subprocess(self, media: PlayerMedia) -> None:
        """Stream media using FFmpeg subprocess - GIL-free audio path."""
        if not self._audio_handler:
            raise PlayerCommandFailed("No audio handler available")

        qid = self.active_source
        if not qid:
            raise PlayerCommandFailed("No active queue for playback")

        try:
            _LOGGER.debug("Starting FFmpeg subprocess stream for: %s", media.uri)

            # Get queue and validate
            queue = self.mass.player_queues.get(qid)
            if not queue:
                raise PlayerCommandFailed(f"Queue not found: {qid}")

            start_queue_item = self.mass.player_queues.get_item(qid, media.queue_item_id)
            if not start_queue_item:
                raise PlayerCommandFailed(f"Queue item not found: {media.queue_item_id}")

            # Set metadata for UI
            self._set_stream_metadata(start_queue_item, media)

            # Resolve stream URL from MA
            stream_url = await self._resolve_stream_url(queue, start_queue_item)

            # Spawn FFmpeg and stream audio
            await self._stream_from_ffmpeg(stream_url)

        except asyncio.CancelledError:
            _LOGGER.debug("Stream cancelled for %s", self.player_id)
            await self._kill_ffmpeg_process()
            raise
        except (OSError, ConnectionError, BrokenPipeError) as err:
            _LOGGER.error("Stream I/O error for %s: %s", self.player_id, err)
            await self.stop()
            raise PlayerCommandFailed(f"Stream I/O error: {err}") from err
        except (ValueError, RuntimeError) as err:
            _LOGGER.error("Stream configuration error for %s: %s", self.player_id, err)
            await self.stop()
            raise PlayerCommandFailed(f"Stream configuration error: {err}") from err
        except (AudioError, PlayerCommandFailed):
            # Already properly formatted errors - just re-raise
            raise
        finally:
            self._ffmpeg_proc = None

    def _set_stream_metadata(self, start_queue_item: Any, media: PlayerMedia) -> None:
        """Set stream metadata for UI display."""
        if start_queue_item.media_item:
            self._attr_stream_title = start_queue_item.name
            self._attr_stream_details = start_queue_item.media_item.metadata.description or ""
        else:
            self._attr_stream_title = media.title or start_queue_item.name
            self._attr_stream_details = ""

        self.update_state()

    async def _resolve_stream_url(self, queue: Any, start_queue_item: Any) -> str:
        """Resolve the stream URL from MA streams controller."""
        # Validate session_id is not None
        if queue.session_id is None:
            raise PlayerCommandFailed("Queue has no session_id")

        stream_url = await self.mass.streams.resolve_stream_url(
            session_id=queue.session_id,
            queue_item=start_queue_item,
            flow_mode=True,
            player_id=self.player_id,
        )

        _LOGGER.debug("Stream URL: %s", stream_url)
        return stream_url

    async def _stream_from_ffmpeg(self, stream_url: str) -> None:
        """Stream audio from FFmpeg subprocess to audio handler."""
        if not self._audio_handler:
            raise PlayerCommandFailed("Audio handler not initialized")

        try:
            # Start FFmpeg subprocess
            self._ffmpeg_proc = await asyncio.create_subprocess_exec(
                "ffmpeg",
                "-i",
                stream_url,
                "-f",
                "f32le",
                "-acodec",
                "pcm_f32le",
                "-ar",
                str(self._audio_handler.sample_rate),
                "-ac",
                str(self._audio_handler.channels),
                "-loglevel",
                "error",
                "pipe:1",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            if not self._ffmpeg_proc or not self._ffmpeg_proc.stdout:
                raise PlayerCommandFailed("Failed to start FFmpeg process")

            _LOGGER.info("FFmpeg subprocess started (PID: %s)", self._ffmpeg_proc.pid)

            # Monitor stderr for errors
            if self._ffmpeg_proc.stderr:
                asyncio.create_task(self._monitor_ffmpeg_stderr(self._ffmpeg_proc.stderr))

            # Consume FFmpeg output
            await self._consume_ffmpeg_stream(
                self._ffmpeg_proc.stdout,
                buffer_size_bytes=16384,
            )

        except asyncio.CancelledError:
            _LOGGER.debug("Stream task cancelled")
            raise
        except Exception as err:
            _LOGGER.exception("Error in FFmpeg stream: %s", err)
            raise
        finally:
            # Cleanup - save reference before setting to None
            ffmpeg_proc = self._ffmpeg_proc
            self._ffmpeg_proc = None

            if ffmpeg_proc and ffmpeg_proc.returncode is None:
                try:
                    ffmpeg_proc.kill()
                    await asyncio.wait_for(ffmpeg_proc.wait(), timeout=2.0)
                except (TimeoutError, ProcessLookupError):
                    pass

    async def _monitor_ffmpeg_stderr(self, stderr: asyncio.StreamReader) -> None:
        """Monitor FFmpeg stderr for errors."""
        try:
            while True:
                line = await stderr.readline()
                if not line:
                    break
                error_msg = line.decode("utf-8", errors="replace").strip()
                if error_msg:
                    _LOGGER.warning("FFmpeg: %s", error_msg)
        except asyncio.CancelledError:
            pass

    async def _consume_ffmpeg_stream(
        self, stdout: asyncio.StreamReader, buffer_size_bytes: int
    ) -> None:
        """Read PCM data from FFmpeg and feed to audio handler."""
        chunk_count = 0
        total_bytes = 0
        playback_started = False

        while True:
            chunk = await stdout.read(buffer_size_bytes)

            if not chunk:
                break

            chunk_count += 1
            total_bytes += len(chunk)

            if self._audio_handler:
                try:
                    self._audio_handler.write_audio(chunk)
                except (ValueError, TypeError) as err:
                    _LOGGER.error("Error writing audio chunk: %s", err)

                if not playback_started and self._audio_handler.queue_size >= 10:
                    _LOGGER.debug(
                        "Buffer pre-filled (%d chunks), starting playback",
                        self._audio_handler.queue_size,
                    )
                    # Start elapsed time tracking NOW when audio actually starts
                    self._playback_start_time = time.time()
                    self._attr_elapsed_time = 0
                    self._attr_elapsed_time_last_updated = time.time()

                    self._audio_handler.play()
                    playback_started = True

    async def _kill_ffmpeg_process(self) -> None:
        """Kill the FFmpeg process gracefully if running."""
        if not self._ffmpeg_proc:
            return

        try:
            # Try graceful termination first
            self._ffmpeg_proc.terminate()
            try:
                await asyncio.wait_for(self._ffmpeg_proc.wait(), timeout=2.0)
            except TimeoutError:
                # Force kill if it doesn't terminate
                _LOGGER.warning("FFmpeg did not terminate gracefully, forcing kill")
                self._ffmpeg_proc.kill()
                await self._ffmpeg_proc.wait()
        except ProcessLookupError:
            pass

    async def _ensure_audio_handler_ready(self) -> None:
        """Ensure audio handler is ready for playback."""
        if not self._audio_handler:
            _LOGGER.debug("Audio handler missing, recreating...")
            await self._setup_audio_handler()
            return

        # Check if stream is actually running
        if not self._audio_handler.is_stream_active:
            _LOGGER.debug("Audio stream not running, restarting...")
            await self._audio_handler.stop()
            await self._audio_handler.start()
            # Restore volume/mute
            volume = self._attr_volume_level if self._attr_volume_level is not None else 100
            self._audio_handler.set_volume(volume / 100.0)
            self._audio_handler.set_muted(self._attr_volume_muted or False)
