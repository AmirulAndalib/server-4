"""Helper functions for the Local Soundcard Player Provider."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import TYPE_CHECKING, Any, cast

import numpy as np
import sounddevice as sd
from music_assistant_models.errors import AudioError, SetupFailedError

from music_assistant.constants import DEFAULT_PCM_FORMAT

from .constants import AUDIO_FORMAT, AUDIO_LATENCY, MAX_AUDIO_QUEUE_SIZE

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
_LOGGER = logging.getLogger(__name__)


async def get_available_devices() -> list[dict[str, Any]]:
    """Get list of available audio output devices."""
    try:
        loop = asyncio.get_event_loop()
        devices = await loop.run_in_executor(None, sd.query_devices)

        output_devices = []
        for idx, device in enumerate(devices):
            # Cast to Any to work around sounddevice's special type
            dev = cast("Any", device)

            if dev["max_output_channels"] > 0:
                output_devices.append(
                    {
                        "id": idx,
                        "name": dev["name"],
                        "channels": dev["max_output_channels"],
                        "sample_rate": dev["default_samplerate"],
                        "host_api": dev["hostapi"],
                    }
                )

        return output_devices
    except ImportError as err:
        raise SetupFailedError("sounddevice is not available") from err
    except OSError as err:
        raise AudioError(f"Failed to query audio devices: {err}") from err


async def test_device_compatibility(
    device_id: int,
    sample_rate: int | None,
    channels: int | None,
    audio_format: str | None,
) -> bool:
    """Test if a device is compatible with the given parameters."""
    if sample_rate is None:
        sample_rate = DEFAULT_PCM_FORMAT.sample_rate
    if channels is None:
        channels = DEFAULT_PCM_FORMAT.channels
    if audio_format is None:
        audio_format = AUDIO_FORMAT

    try:
        loop = asyncio.get_event_loop()

        def _test_device() -> bool:
            """Test device in executor."""
            try:
                dtype = np.dtype(audio_format)
                with sd.OutputStream(
                    device=device_id,
                    samplerate=sample_rate,
                    channels=channels,
                    dtype=dtype,
                    latency=AUDIO_LATENCY,
                ):
                    pass
                return True
            except (OSError, ValueError) as err:
                _LOGGER.debug("Device %s test failed: %s", device_id, err)
                return False

        return await loop.run_in_executor(None, _test_device)
    except ImportError as err:
        raise SetupFailedError(
            "sounddevice and numpy are required for local soundcard playback"
        ) from err


class AudioStreamHandler:
    """Handles streaming audio data to sounddevice."""

    def __init__(
        self,
        device_id: int | None = None,
        sample_rate: int | None = None,
        channels: int | None = None,
        dtype: np.dtype | None = None,
        buffer_size: int = 1024,
    ):
        """Initialize the audio stream handler."""
        self.device_id = device_id
        self.sample_rate = sample_rate or DEFAULT_PCM_FORMAT.sample_rate
        self.channels = channels or DEFAULT_PCM_FORMAT.channels
        self.dtype = np.dtype(AUDIO_FORMAT) if dtype is None else dtype
        self._buffer_size = buffer_size

        # Audio queuing system
        self._audio_buffer: deque[bytes] = deque(maxlen=MAX_AUDIO_QUEUE_SIZE)
        self._lock = threading.Lock()
        self._bytes_buffer = b""

        # Playback control
        self._volume = 1.0
        self._muted = False
        self._is_playing = False
        self._stream: sd.OutputStream | None = None

    async def start(self) -> None:
        """Start the audio stream (but in paused state until play() is called)."""
        if self._stream is not None:
            return

        try:
            loop = asyncio.get_event_loop()

            def _start_stream() -> None:
                self._stream = sd.OutputStream(
                    device=self.device_id,
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype=self.dtype,
                    blocksize=1024,  # Small blocksize for responsive volume/control changes
                    latency=AUDIO_LATENCY,
                    callback=self._audio_callback,
                )
                self._stream.start()

            await loop.run_in_executor(None, _start_stream)
            # Don't set _is_playing here - wait for explicit play() call
            _LOGGER.debug("Started audio stream on device %s (paused)", self.device_id)

        except (OSError, ValueError, RuntimeError) as err:
            raise AudioError(f"Failed to start audio stream: {err}") from err

    async def stop(self) -> None:
        """Stop the audio stream."""
        if self._stream is None:
            return

        try:
            loop = asyncio.get_event_loop()

            def _stop_stream() -> None:
                if self._stream:
                    self._stream.stop()
                    self._stream.close()

            await loop.run_in_executor(None, _stop_stream)
            self._stream = None
            self._is_playing = False

            # Clear buffers
            self._bytes_buffer = b""
            with self._lock:
                self._audio_buffer.clear()

            _LOGGER.debug("Stopped audio stream on device %s", self.device_id)
        except (OSError, ValueError) as err:
            _LOGGER.error("Error stopping audio stream: %s", err)
        finally:
            self._stream = None
            self._is_playing = False

    def play(self) -> None:
        """Start playing audio."""
        self._is_playing = True

    def pause(self) -> None:
        """Pause audio playback."""
        self._is_playing = False

    def clear_buffer(self) -> None:
        """Clear the audio buffer without stopping the stream."""
        self._bytes_buffer = b""
        with self._lock:
            self._audio_buffer.clear()
        _LOGGER.debug("Cleared audio buffer")

    async def write_audio(self, data: bytes) -> None:
        """Write audio bytes to the queue, aligning to frame boundaries."""
        frame_size = self.dtype.itemsize * self.channels
        self._bytes_buffer += data

        # Only process complete frames
        aligned_size = len(self._bytes_buffer) - (len(self._bytes_buffer) % frame_size)
        if aligned_size > 0:
            aligned_chunk = self._bytes_buffer[:aligned_size]
            self._bytes_buffer = self._bytes_buffer[aligned_size:]

            # Acquire lock and append to the buffer
            with self._lock:
                self._audio_buffer.append(aligned_chunk)

    def set_volume(self, volume: float) -> None:
        """Set the playback volume (0.0 to 1.0)."""
        self._volume = max(0.0, min(1.0, volume))

    def set_muted(self, muted: bool) -> None:
        """Set the mute state."""
        self._muted = muted

    @property
    def is_playing(self) -> bool:
        """Check if audio is playing."""
        return self._is_playing and self._stream is not None

    @property
    def volume(self) -> float:
        """Get the current volume."""
        return self._volume

    @property
    def muted(self) -> bool:
        """Get the mute state."""
        return self._muted

    @property
    def queue_size(self) -> int:
        """Return the number of audio chunks currently in the queue."""
        with self._lock:
            return len(self._audio_buffer)

    def get_output_writer(self) -> Callable[[bytes], Awaitable[None]]:
        """Get a callable for writing audio data directly."""
        return self.write_audio

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, _time: Any, status: sd.CallbackFlags
    ) -> None:
        """Sounddevice callback to fill output buffer."""
        if status:
            _LOGGER.warning("Audio callback status: %s", status)

        # Start with silence
        outdata.fill(0)

        if not self.is_playing or self.muted:
            return

        frames_written = 0
        with self._lock:
            while frames_written < frames and self._audio_buffer:
                chunk = self._audio_buffer.popleft()

                # Calculate frames in this chunk
                frame_size = self.dtype.itemsize * self.channels
                frames_in_chunk = len(chunk) // frame_size

                frames_to_write = min(frames_in_chunk, frames - frames_written)

                if frames_to_write > 0:
                    # Slice only the frames we can write
                    bytes_to_write = frames_to_write * frame_size
                    try:
                        chunk_np = np.frombuffer(chunk[:bytes_to_write], dtype=self.dtype).reshape(
                            (-1, self.channels)
                        )

                        outdata[frames_written : frames_written + frames_to_write] = chunk_np
                        frames_written += frames_to_write

                        # If remainder frames left, push back the unused part
                        if frames_to_write < frames_in_chunk:
                            remainder_bytes = chunk[bytes_to_write:]
                            self._audio_buffer.appendleft(remainder_bytes)
                            break
                    except (ValueError, TypeError) as err:
                        _LOGGER.error("Error processing audio data: %s", err)
                        break

        # Apply volume scaling
        if frames_written > 0 and self._volume != 1.0:
            outdata[:frames_written] *= self._volume
