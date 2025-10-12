"""Local Audio Output handler for integration with FFmpeg via named pipes."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import tempfile
from pathlib import Path
from typing import Any

import numpy as np
import sounddevice as sd

_LOGGER = logging.getLogger(__name__)


class LocalAudioOutput:
    """Handles local audio output through sounddevice with FIFO integration."""

    def __init__(
        self,
        device_id: int | None = None,
        sample_rate: int = 48000,
        channels: int = 2,
        buffer_size: int = 1024,
    ):
        """Initialize the local audio output."""
        self.device_id = device_id
        self.sample_rate = sample_rate
        self.channels = channels
        self.buffer_size = buffer_size
        self.dtype = np.dtype("float32")

        # Audio control
        self._volume = 1.0
        self._muted = False

        # Stream and FIFO handling
        self._stream: sd.OutputStream | None = None
        self._fifo_path: str | None = None
        self._fifo_fd: int | None = None
        self._playback_task: asyncio.Task[None] | None = None
        self._running = False

    def set_volume(self, volume: float) -> None:
        """Set the playback volume (0.0 to 1.0)."""
        self._volume = max(0.0, min(1.0, volume))

    def set_muted(self, muted: bool) -> None:
        """Set the mute state."""
        self._muted = muted

    def get_fifo_path(self) -> str:
        """Get the FIFO path for FFmpeg to write to."""
        if not self._fifo_path:
            # Create a named pipe (FIFO) in temp directory
            temp_dir = tempfile.gettempdir()
            self._fifo_path = os.path.join(temp_dir, f"ma_local_audio_{id(self)}.fifo")

            # Remove existing FIFO if it exists
            fifo_path = Path(self._fifo_path)
            if fifo_path.exists():
                fifo_path.unlink()

            # Create the named pipe
            os.mkfifo(self._fifo_path)
            _LOGGER.debug("Created FIFO at %s", self._fifo_path)

        return self._fifo_path

    async def start_playback(self) -> None:
        """Start audio playback from the FIFO."""
        if self._running:
            return

        self._running = True

        # Start the sounddevice stream
        await self._start_audio_stream()

        # Start the FIFO reader task
        self._playback_task = asyncio.create_task(self._fifo_reader())

        _LOGGER.debug("Started audio playback")

    async def stop_playback(self) -> None:
        """Stop audio playback."""
        self._running = False

        # Cancel the playback task
        if self._playback_task and not self._playback_task.done():
            self._playback_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._playback_task

        # Stop the audio stream
        await self._stop_audio_stream()

        _LOGGER.debug("Stopped audio playback")

    async def close(self) -> None:
        """Close and cleanup the audio output."""
        await self.stop_playback()

        # Close FIFO
        if self._fifo_fd is not None:
            with contextlib.suppress(OSError):
                os.close(self._fifo_fd)
            self._fifo_fd = None

        # Remove FIFO file
        if self._fifo_path:
            fifo_path = Path(self._fifo_path)
            if fifo_path.exists():
                try:
                    fifo_path.unlink()
                    _LOGGER.debug("Removed FIFO at %s", self._fifo_path)
                except OSError as err:
                    _LOGGER.warning("Failed to remove FIFO: %s", err)
            self._fifo_path = None

    async def _start_audio_stream(self) -> None:
        """Start the sounddevice output stream."""
        if self._stream is not None:
            return

        try:
            loop = asyncio.get_event_loop()

            def _create_stream() -> None:
                self._stream = sd.OutputStream(
                    device=self.device_id,
                    samplerate=self.sample_rate,
                    channels=self.channels,
                    dtype=self.dtype,
                    blocksize=self.buffer_size,
                    latency="low",
                    callback=self._audio_callback,
                )
                self._stream.start()

            await loop.run_in_executor(None, _create_stream)
            _LOGGER.debug("Started sounddevice stream")

        except Exception as err:
            _LOGGER.error("Failed to start audio stream: %s", err)
            raise

    async def _stop_audio_stream(self) -> None:
        """Stop the sounddevice output stream."""
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
            _LOGGER.debug("Stopped sounddevice stream")

        except Exception as err:
            _LOGGER.error("Error stopping audio stream: %s", err)

    async def _fifo_reader(self) -> None:
        """Read audio data from FIFO and buffer it for the audio callback."""
        fifo_path = self.get_fifo_path()

        # We'll use a simple buffer to store audio data
        self._audio_buffer = b""

        try:
            # Open FIFO for reading (this will block until FFmpeg opens it for writing)
            loop = asyncio.get_event_loop()
            fifo_fd = await loop.run_in_executor(None, lambda: os.open(fifo_path, os.O_RDONLY))
            self._fifo_fd = fifo_fd

            _LOGGER.debug("Opened FIFO for reading")

            # Read audio data from FIFO
            while self._running:
                try:
                    # Read data from FIFO
                    data = await loop.run_in_executor(None, lambda: os.read(fifo_fd, 8192))

                    if not data:
                        # FFmpeg closed the pipe
                        break

                    # Add to buffer
                    self._audio_buffer += data

                    # Prevent buffer from growing too large
                    max_buffer_size = self.sample_rate * self.channels * 4 * 5  # 5 seconds
                    if len(self._audio_buffer) > max_buffer_size:
                        # Keep only the most recent data
                        self._audio_buffer = self._audio_buffer[-max_buffer_size:]

                except Exception as err:
                    if self._running:
                        _LOGGER.error("Error reading from FIFO: %s", err)
                    break

        except Exception as err:
            _LOGGER.error("Error in FIFO reader: %s", err)
        finally:
            if self._fifo_fd is not None:
                with contextlib.suppress(OSError):
                    os.close(self._fifo_fd)
                self._fifo_fd = None

    def _audio_callback(
        self, outdata: np.ndarray, frames: int, time: Any, status: sd.CallbackFlags
    ) -> None:
        """Sounddevice callback to fill output buffer."""
        if status:
            _LOGGER.warning("Audio callback status: %s", status)

        # Start with silence
        outdata.fill(0)

        if not self._running or self._muted:
            return

        # Calculate how many bytes we need
        bytes_needed = frames * self.channels * self.dtype.itemsize

        # Get data from buffer
        if hasattr(self, "_audio_buffer") and len(self._audio_buffer) >= bytes_needed:
            # Extract the needed bytes
            audio_bytes = self._audio_buffer[:bytes_needed]
            self._audio_buffer = self._audio_buffer[bytes_needed:]

            try:
                # Convert bytes to numpy array
                audio_data = np.frombuffer(audio_bytes, dtype=self.dtype)
                audio_data = audio_data.reshape((-1, self.channels))

                # Copy to output buffer
                output_frames = min(audio_data.shape[0], frames)
                outdata[:output_frames] = audio_data[:output_frames]

                # Apply volume
                if self._volume != 1.0:
                    outdata[:output_frames] *= self._volume

            except (ValueError, TypeError) as err:
                _LOGGER.error("Error processing audio data: %s", err)
