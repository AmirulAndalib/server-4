"""Helper functions for the Local Soundcard Player Provider."""

from __future__ import annotations

import asyncio
import logging
import threading
from collections import deque
from typing import Any, cast

import numpy as np
import sounddevice as sd
from music_assistant_models.errors import AudioError, SetupFailedError

from music_assistant.constants import INTERNAL_PCM_FORMAT

_LOGGER = logging.getLogger(__name__)


async def get_available_devices() -> list[dict[str, Any]]:
    """Get list of available audio output devices."""
    try:
        loop = asyncio.get_running_loop()
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
        sample_rate = INTERNAL_PCM_FORMAT.sample_rate
    if channels is None:
        channels = INTERNAL_PCM_FORMAT.channels
    if audio_format is None:
        audio_format = "float32"

    try:
        loop = asyncio.get_running_loop()

        def _test_device() -> bool:
            """Test device in executor."""
            try:
                dtype = np.dtype(audio_format)
                with sd.OutputStream(
                    device=device_id,
                    samplerate=sample_rate,
                    channels=channels,
                    dtype=dtype,
                    latency="low",
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
    """Handles streaming audio data to sounddevice with minimal GIL interaction."""

    def __init__(
        self,
        device_id: int | None = None,
        sample_rate: int | None = None,
        channels: int | None = None,
        buffer_size: int = 4096,
    ):
        """Initialize the audio stream handler."""
        self.device_id = device_id
        self.sample_rate = sample_rate or INTERNAL_PCM_FORMAT.sample_rate
        self.channels = channels or INTERNAL_PCM_FORMAT.channels
        self.dtype = np.dtype("float32")
        self._buffer_size = buffer_size

        # Simple circular buffer with thread safety
        self._audio_buffer: deque[bytes] = deque(maxlen=200)
        self._lock = threading.Lock()
        self._bytes_buffer = b""

        # Playback control
        self._volume = 1.0
        self._muted = False
        self._is_playing = False
        self._stream: sd.OutputStream | None = None

    @property
    def is_stream_active(self) -> bool:
        """Check if the audio stream is active."""
        return self._stream is not None

    async def start(self) -> None:
        """Start the audio stream."""
        if self._stream is not None:
            return

        try:
            loop = asyncio.get_running_loop()

            def _start_stream() -> None:
                self._stream = sd.OutputStream(
                    device=self.device_id,
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype=self.dtype,
                    blocksize=self._buffer_size,
                    latency="low",
                    callback=self._audio_callback,
                )
                self._stream.start()

            await loop.run_in_executor(None, _start_stream)
            _LOGGER.debug("Started audio stream on device %s", self.device_id)

        except (OSError, ValueError, RuntimeError) as err:
            raise AudioError(f"Failed to start audio stream: {err}") from err

    async def stop(self) -> None:
        """Stop the audio stream."""
        if self._stream is None:
            return

        try:
            loop = asyncio.get_running_loop()

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

    def clear_buffer(self) -> None:
        """Clear all buffered audio data."""
        with self._lock:
            self._audio_buffer.clear()
        self._bytes_buffer = b""

    def play(self) -> None:
        """Start playing audio."""
        self._is_playing = True

    def pause(self) -> None:
        """Pause audio playback."""
        self._is_playing = False

    def write_audio(self, data: bytes) -> None:
        """Write audio bytes to the buffer - synchronous for subprocess pipe reading."""
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

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, time: Any, status: sd.CallbackFlags
    ) -> None:
        """Sounddevice callback to fill output buffer - minimal GIL time."""
        if status:
            _LOGGER.warning(
                "Audio callback status: %s - queue size: %d", status, len(self._audio_buffer)
            )
        # Start with silence
        outdata.fill(0)

        if not self.is_playing or self.muted:
            return

        buffer_size = len(self._audio_buffer)
        if buffer_size < 10:
            _LOGGER.debug("Buffer running low: %d chunks", buffer_size)

        frames_written = 0
        with self._lock:
            while frames_written < frames and self._audio_buffer:
                chunk = self._audio_buffer.popleft()

                # Calculate frames in this chunk
                frame_size = self.dtype.itemsize * self.channels
                frames_in_chunk = len(chunk) // frame_size

                frames_to_write = min(frames_in_chunk, frames - frames_written)

                if frames_to_write > 0:
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
