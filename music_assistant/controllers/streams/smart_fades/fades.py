"""Smart Fades - Audio fade implementations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import aiofiles
import numpy as np
import numpy.typing as npt
import shortuuid

from music_assistant.constants import VERBOSE_LOG_LEVEL
from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    calculate_energy_crossfade_duration,
    compute_gradual_tempo_steps,
    find_fadein_entry,
    find_fadeout_start,
    select_crossfade_curve_type,
)
from music_assistant.controllers.streams.smart_fades.filters import (
    CrossfadeFilter,
    FadeoutTrimFilter,
    Filter,
    FrequencySweepFilter,
    GradualTimeStretchFilter,
    TimeStretchFilter,
    TrimFilter,
)
from music_assistant.helpers.process import communicate
from music_assistant.helpers.util import remove_file
from music_assistant.models.audio_analysis import AudioAnalysisData

if TYPE_CHECKING:
    from music_assistant_models.media_items import AudioFormat

SMART_CROSSFADE_DURATION = 45


class SmartFade(ABC):
    """Abstract base class for Smart Fades."""

    filters: list[Filter]

    def __init__(self, logger: logging.Logger) -> None:
        """Initialize SmartFade base class."""
        self.filters = []
        self.logger = logger

    @abstractmethod
    def _build(self) -> None:
        """Build the smart fades filter chain."""
        ...

    def _get_ffmpeg_filters(
        self,
        input_fadein_label: str = "[1]",
        input_fadeout_label: str = "[0]",
    ) -> list[str]:
        """Get FFmpeg filters for smart fades."""
        if not self.filters:
            self._build()
        filters = []
        _cur_fadein_label = input_fadein_label
        _cur_fadeout_label = input_fadeout_label
        for audio_filter in self.filters:
            filter_strings = audio_filter.apply(_cur_fadein_label, _cur_fadeout_label)
            filters.extend(filter_strings)
            _cur_fadein_label = f"[{audio_filter.output_fadein_label}]"
            _cur_fadeout_label = f"[{audio_filter.output_fadeout_label}]"
        return filters

    async def apply(
        self,
        fade_out_part: bytes,
        fade_in_part: bytes,
        pcm_format: AudioFormat,
    ) -> bytes:
        """Apply the smart fade to the given PCM audio parts."""
        # Write the fade_out_part to a temporary file
        fadeout_filename = f"/tmp/{shortuuid.random(20)}.pcm"  # noqa: S108
        async with aiofiles.open(fadeout_filename, "wb") as outfile:
            await outfile.write(fade_out_part)

        args = [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            # Input 1: fadeout part (as file)
            "-acodec",
            pcm_format.content_type.name.lower(),  # e.g., "pcm_f32le" not just "f32le"
            "-ac",
            str(pcm_format.channels),
            "-ar",
            str(pcm_format.sample_rate),
            "-channel_layout",
            "mono" if pcm_format.channels == 1 else "stereo",
            "-f",
            pcm_format.content_type.value,
            "-i",
            fadeout_filename,
            # Input 2: fade_in part (stdin)
            "-acodec",
            pcm_format.content_type.name.lower(),
            "-ac",
            str(pcm_format.channels),
            "-ar",
            str(pcm_format.sample_rate),
            "-channel_layout",
            "mono" if pcm_format.channels == 1 else "stereo",
            "-f",
            pcm_format.content_type.value,
            "-i",
            "-",
        ]
        smart_fade_filters = self._get_ffmpeg_filters()
        self.logger.debug(
            "Applying smartfade: %s",
            self,
        )
        args.extend(
            [
                "-filter_complex",
                ";".join(smart_fade_filters),
                # Output format specification - must match input codec format
                "-acodec",
                pcm_format.content_type.name.lower(),
                "-ac",
                str(pcm_format.channels),
                "-ar",
                str(pcm_format.sample_rate),
                "-channel_layout",
                "mono" if pcm_format.channels == 1 else "stereo",
                "-f",
                pcm_format.content_type.value,
                "-",
            ]
        )
        self.logger.log(VERBOSE_LOG_LEVEL, "FFmpeg command args: %s", " ".join(args))

        try:
            # Execute the enhanced smart fade with full buffer
            _, raw_crossfade_output, stderr = await communicate(args, fade_in_part)

            if raw_crossfade_output:
                return raw_crossfade_output
            stderr_msg = stderr.decode() if stderr else "(no stderr output)"
            raise RuntimeError(f"Smart crossfade failed. FFmpeg stderr: {stderr_msg}")
        finally:
            # Always cleanup temp file, even if ffmpeg fails
            await remove_file(fadeout_filename)

    def __repr__(self) -> str:
        """Return string representation of SmartFade showing the filter chain."""
        if not self.filters:
            return f"<{self.__class__.__name__}: 0 filters>"

        chain = " → ".join(repr(f) for f in self.filters)
        return f"<{self.__class__.__name__}: {len(self.filters)} filters> {chain}"


class SmartCrossFade(SmartFade):
    """Smart fades class that implements a Smart Fade mode."""

    # Only apply time stretching if BPM difference is < this %
    time_stretch_bpm_percentage_threshold: float = 5.0

    def __init__(
        self,
        logger: logging.Logger,
        fade_out_analysis: AudioAnalysisData,
        fade_in_analysis: AudioAnalysisData,
    ) -> None:
        """Initialize SmartFades with analysis data.

        :param logger: Logger for debug output.
        :param fade_out_analysis: Analysis data for the outgoing track.
        :param fade_in_analysis: Analysis data for the incoming track.
        """
        if (
            fade_out_analysis.bpm is None
            or fade_in_analysis.bpm is None
            or fade_out_analysis.beats is None
            or fade_in_analysis.beats is None
        ):
            raise ValueError("AudioAnalysisData must have bpm and beats set for smart crossfade")
        self.fade_out_bpm: float = fade_out_analysis.bpm
        self.fade_in_bpm: float = fade_in_analysis.bpm
        self.fade_out_beats: npt.NDArray[np.float64] = fade_out_analysis.beats
        self.fade_in_beats: npt.NDArray[np.float64] = fade_in_analysis.beats
        self.fade_out_downbeats: npt.NDArray[np.float64] = (
            fade_out_analysis.downbeats if fade_out_analysis.downbeats is not None else np.array([])
        )
        self.fade_in_downbeats: npt.NDArray[np.float64] = (
            fade_in_analysis.downbeats if fade_in_analysis.downbeats is not None else np.array([])
        )
        # Energy curves for energy-contour alignment (may be None)
        self.fade_out_energy: npt.NDArray[np.float32] | None = fade_out_analysis.energy_curve
        self.fade_in_energy: npt.NDArray[np.float32] | None = fade_in_analysis.energy_curve
        self.fade_out_duration: float = fade_out_analysis.duration or 0.0
        self.fade_in_duration: float = fade_in_analysis.duration or 0.0
        super().__init__(logger)

    def _try_energy_alignment(
        self,
    ) -> tuple[bool, float | None, float | None, float, str | None, npt.NDArray[np.float64]]:
        """Attempt energy-contour alignment for crossfade parameters.

        :return: Tuple of (energy_aligned, fadeout_start_pos, fadein_start_pos,
                 crossfade_duration, curve_type, fadeout_downbeats_rel).
        """
        empty_downbeats: npt.NDArray[np.float64] = np.array([])
        if self.fade_out_energy is None or self.fade_in_energy is None:
            self.logger.debug(
                "Energy alignment skipped: fade_out_energy=%s, fade_in_energy=%s",
                "present" if self.fade_out_energy is not None else "None",
                "present" if self.fade_in_energy is not None else "None",
            )
            return False, None, None, 0.0, None, empty_downbeats

        buffer_secs = min(SMART_CROSSFADE_DURATION, int(self.fade_out_duration))
        energy_out = (
            self.fade_out_energy[-buffer_secs:] if buffer_secs > 0 else self.fade_out_energy
        )
        energy_in = self.fade_in_energy[:SMART_CROSSFADE_DURATION]

        # Buffer-relative downbeats for fade-out
        outro_start = max(0.0, self.fade_out_duration - SMART_CROSSFADE_DURATION)
        out_db_mask = self.fade_out_downbeats >= outro_start
        fadeout_downbeats_rel = self.fade_out_downbeats[out_db_mask] - outro_start

        # Fade-in downbeats are already 0-relative
        fadein_downbeats_rel = self.fade_in_downbeats[
            self.fade_in_downbeats < SMART_CROSSFADE_DURATION
        ]

        self.logger.debug(
            "Energy alignment attempt: energy_out=%d values (range %.2f-%.2f), "
            "energy_in=%d values (range %.2f-%.2f), "
            "fadeout_downbeats=%d, fadein_downbeats=%d",
            len(energy_out),
            float(energy_out.min()) if len(energy_out) > 0 else 0,
            float(energy_out.max()) if len(energy_out) > 0 else 0,
            len(energy_in),
            float(energy_in.min()) if len(energy_in) > 0 else 0,
            float(energy_in.max()) if len(energy_in) > 0 else 0,
            len(fadeout_downbeats_rel),
            len(fadein_downbeats_rel),
        )

        fadeout_start = find_fadeout_start(energy_out, fadeout_downbeats_rel, bpm=self.fade_out_bpm)
        fadein_entry = find_fadein_entry(energy_in, fadein_downbeats_rel)

        if fadeout_start is None or fadein_entry is None:
            self.logger.debug(
                "Energy alignment failed: fadeout_start=%s, fadein_entry=%s",
                f"{fadeout_start:.1f}s" if fadeout_start is not None else "None (no clear decline)",
                f"{fadein_entry:.1f}s" if fadein_entry is not None else "None (no clear build)",
            )
            return False, None, None, 0.0, None, fadeout_downbeats_rel

        crossfade_duration = calculate_energy_crossfade_duration(
            energy_out=energy_out,
            fadeout_start=int(fadeout_start),
            energy_in=energy_in,
            fadein_entry=int(fadein_entry),
            bpm=self.fade_in_bpm,
        )
        # Select curve type based on energy slopes in overlap region
        curve_type: str | None = None
        overlap_out = energy_out[int(fadeout_start) : int(fadeout_start) + int(crossfade_duration)]
        overlap_in = energy_in[int(fadein_entry) : int(fadein_entry) + int(crossfade_duration)]
        if len(overlap_out) > 1 and len(overlap_in) > 1:
            curve_type = select_crossfade_curve_type(overlap_out, overlap_in)

        return (
            True,
            fadeout_start,
            fadein_entry,
            crossfade_duration,
            curve_type,
            fadeout_downbeats_rel,
        )

    def _build(self) -> None:
        """Build the smart fades filter chain."""
        # Calculate tempo factor for time stretching
        bpm_ratio = self.fade_in_bpm / self.fade_out_bpm
        bpm_diff_percent = abs(1.0 - bpm_ratio) * 100

        # Extrapolate downbeats for better bar calculation
        self.extrapolated_fadeout_downbeats = extrapolate_downbeats(
            self.fade_out_downbeats,
            tempo_factor=1.0,
            bpm=self.fade_out_bpm,
        )

        self.logger.log(
            VERBOSE_LOG_LEVEL,
            "SmartCrossFade build: fade_out_bpm=%.1f (%d beats), fade_in_bpm=%.1f (%d beats)",
            self.fade_out_bpm,
            len(self.fade_out_beats),
            self.fade_in_bpm,
            len(self.fade_in_beats),
        )

        # === Energy-contour alignment (preferred path) ===
        (
            energy_aligned,
            fadeout_start_pos,
            fadein_start_pos,
            crossfade_duration,
            curve_type,
            fadeout_downbeats_rel,
        ) = self._try_energy_alignment()
        crossfade_bars: int = 0

        # === Fallback: bar-counting approach ===
        if not energy_aligned:
            self.logger.debug(
                "Energy alignment failed, falling back to beat/tempo-based alignment "
                "(BPM diff=%.1f%%, bpm_ratio=%.3f)",
                bpm_diff_percent,
                bpm_ratio,
            )
            crossfade_bars = self._calculate_optimal_crossfade_bars()
            fadein_start_pos = self._calculate_optimal_fade_timing(crossfade_bars)
            crossfade_duration = self._calculate_crossfade_duration(crossfade_bars=crossfade_bars)
        else:
            self.logger.debug(
                "Energy alignment successful: fadeout_start=%.1fs, fadein_start=%.1fs, "
                "duration=%.1fs, curve=%s",
                fadeout_start_pos,
                fadein_start_pos,
                crossfade_duration,
                curve_type or "default",
            )
        # === Build filter chain ===

        # Add time stretch filter if needed
        if 0.1 < bpm_diff_percent <= self.time_stretch_bpm_percentage_threshold and (
            crossfade_bars > 4 or energy_aligned
        ):
            # Use gradual stretch for small BPM differences with sufficient downbeats
            stretch_downbeats = fadeout_downbeats_rel if energy_aligned else self.fade_out_downbeats
            if bpm_diff_percent > 0.5 and len(stretch_downbeats) >= 4:
                tempo_steps = compute_gradual_tempo_steps(
                    start_ratio=1.0,
                    end_ratio=bpm_ratio,
                    downbeats=stretch_downbeats,
                )
                if tempo_steps:
                    self.filters.append(GradualTimeStretchFilter(self.logger, tempo_steps))
                else:
                    self.filters.append(
                        TimeStretchFilter(logger=self.logger, stretch_ratio=bpm_ratio)
                    )
            else:
                self.filters.append(TimeStretchFilter(logger=self.logger, stretch_ratio=bpm_ratio))
            # Re-extrapolate downbeats with actual tempo factor for time-stretched audio
            self.extrapolated_fadeout_downbeats = extrapolate_downbeats(
                self.fade_out_downbeats,
                tempo_factor=bpm_ratio,
                bpm=self.fade_out_bpm,
            )

        if fadein_start_pos and fadein_start_pos + crossfade_duration <= SMART_CROSSFADE_DURATION:
            self.filters.append(TrimFilter(logger=self.logger, fadein_start_pos=fadein_start_pos))
        else:
            self.logger.log(
                VERBOSE_LOG_LEVEL,
                "Skipping beat alignment: not enough audio after trim (%.1fs + %.1fs > %.1fs)",
                fadein_start_pos,
                crossfade_duration,
                SMART_CROSSFADE_DURATION,
            )

        # Adjust crossfade duration to align with outgoing track's downbeats (fallback path only)
        if not energy_aligned:
            crossfade_duration = self._adjust_crossfade_to_downbeats(
                crossfade_duration=crossfade_duration,
                fadein_start_pos=fadein_start_pos,
            )

        # 90 BPM -> 1500Hz, 140 BPM -> 2500Hz
        avg_bpm = (self.fade_out_bpm + self.fade_in_bpm) / 2
        crossover_freq = int(np.clip(1500 + (avg_bpm - 90) * 20, 1500, 2500))

        # Adjust for BPM mismatch
        if abs(bpm_ratio - 1.0) > 0.3:
            crossover_freq = int(crossover_freq * 0.85)

        # For shorter fades, use exp/exp curves to avoid abruptness
        if crossfade_bars < 8:
            fadeout_curve = "exponential"
            fadein_curve = "exponential"
        # For long fades, use log/linear curves
        else:
            # Use logarithmic curve to give the next track more space
            fadeout_curve = "logarithmic"
            # Use linear curve for transition, predictable and not too abrupt
            fadein_curve = "linear"

        # Determine the effective end of Song A for EQ and trim calculations.
        # When energy-aligned, we trim Song A to end at fadeout_start + crossfade_duration
        # so that acrossfade picks up exactly at the energy knee.
        fadeout_end_pos: float | None = None
        if energy_aligned and fadeout_start_pos is not None:
            fadeout_end_pos = fadeout_start_pos + crossfade_duration
            fadeout_end_pos = min(fadeout_end_pos, SMART_CROSSFADE_DURATION)

        # Create lowpass filter on the outgoing track (unfiltered -> low-pass)
        # Extended lowpass effect to gradually remove bass frequencies
        if fadeout_end_pos is not None:
            # Energy-aligned: EQ sweep ends at the trimmed end of Song A
            fadeout_eq_duration = min(max(crossfade_duration * 2.5, 8.0), fadeout_end_pos)
            fadeout_eq_start = max(0, fadeout_end_pos - fadeout_eq_duration)
        else:
            fadeout_eq_duration = min(max(crossfade_duration * 2.5, 8.0), SMART_CROSSFADE_DURATION)
            # The crossfade always happens at the END of the buffer
            fadeout_eq_start = max(0, SMART_CROSSFADE_DURATION - fadeout_eq_duration)
        fadeout_sweep = FrequencySweepFilter(
            logger=self.logger,
            sweep_type="lowpass",
            target_freq=crossover_freq,
            duration=fadeout_eq_duration,
            start_time=fadeout_eq_start,
            sweep_direction="fade_in",
            poles=1,
            curve_type=fadeout_curve,
            stream_type="fadeout",
        )
        self.filters.append(fadeout_sweep)

        # Create high pass filter on the incoming track (high-pass -> unfiltered)
        # Quicker highpass removal to avoid lingering vocals after crossfade
        fadein_eq_duration = crossfade_duration / 1.5
        fadein_sweep = FrequencySweepFilter(
            logger=self.logger,
            sweep_type="highpass",
            target_freq=crossover_freq,
            duration=fadein_eq_duration,
            start_time=0,
            sweep_direction="fade_out",
            poles=1,
            curve_type=fadein_curve,
            stream_type="fadein",
        )
        self.filters.append(fadein_sweep)

        # Trim Song A's end so acrossfade starts at the energy knee
        if fadeout_end_pos is not None and fadeout_end_pos < SMART_CROSSFADE_DURATION:
            self.filters.append(
                FadeoutTrimFilter(logger=self.logger, fadeout_end_pos=fadeout_end_pos)
            )

        # Add final crossfade filter
        crossfade_filter = CrossfadeFilter(
            logger=self.logger,
            crossfade_duration=crossfade_duration,
            curve_type=curve_type,
        )
        self.filters.append(crossfade_filter)

        self.logger.info(
            "Smart crossfade: %s BPM, energy_aligned=%s, fadeout_start=%.1fs, "
            "fadein_entry=%.1fs, duration=%.1fs, curve=%s",
            f"{self.fade_out_bpm:.0f}->{self.fade_in_bpm:.0f}",
            energy_aligned,
            fadeout_start_pos if fadeout_start_pos is not None else (fadein_start_pos or -1),
            fadein_start_pos or -1,
            crossfade_duration or -1,
            curve_type or "default",
        )

    def _calculate_crossfade_duration(self, crossfade_bars: int) -> float:
        """Calculate final crossfade duration based on musical bars and BPM."""
        # Calculate crossfade duration based on incoming track's BPM
        beats_per_bar = 4
        seconds_per_beat = 60.0 / self.fade_in_bpm
        musical_duration = crossfade_bars * beats_per_bar * seconds_per_beat

        # Apply buffer constraint
        actual_duration = min(musical_duration, SMART_CROSSFADE_DURATION)

        # Log if we had to constrain the duration
        if musical_duration > SMART_CROSSFADE_DURATION:
            self.logger.log(
                VERBOSE_LOG_LEVEL,
                "Constraining crossfade duration from %.1fs to %.1fs (buffer limit)",
                musical_duration,
                actual_duration,
            )

        return actual_duration

    def _calculate_optimal_crossfade_bars(self) -> int:
        """Calculate optimal crossfade bars that fit in available buffer."""
        bpm_in = self.fade_in_bpm
        bpm_out = self.fade_out_bpm
        bpm_diff_percent = abs(1.0 - bpm_in / bpm_out) * 100

        # Calculate ideal bars based on BPM compatibility
        ideal_bars = 10 if bpm_diff_percent <= self.time_stretch_bpm_percentage_threshold else 6

        # Reduce bars until it fits in the fadein buffer
        for bars in [ideal_bars, 8, 6, 4, 2, 1]:
            if bars > ideal_bars:
                continue

            fadein_start_pos = self._calculate_optimal_fade_timing(bars)
            if fadein_start_pos is None:
                continue

            # Calculate what the duration would be
            test_duration = self._calculate_crossfade_duration(crossfade_bars=bars)

            # Check if it fits in fadein buffer
            fadein_buffer = SMART_CROSSFADE_DURATION - fadein_start_pos
            if test_duration <= fadein_buffer:
                if bars < ideal_bars:
                    self.logger.log(
                        VERBOSE_LOG_LEVEL,
                        "Reduced crossfade from %d to %d bars (fadein buffer=%.1fs, needed=%.1fs)",
                        ideal_bars,
                        bars,
                        fadein_buffer,
                        test_duration,
                    )
                return bars

        # Fall back to 1 bar if nothing else fits
        return 1

    def _calculate_optimal_fade_timing(self, crossfade_bars: int) -> float | None:
        """Calculate beat positions for alignment."""
        beats_per_bar = 4

        def calculate_beat_positions(
            fade_out_beats: npt.NDArray[np.float64],
            fade_in_beats: npt.NDArray[np.float64],
            num_beats: int,
        ) -> float | None:
            """Calculate start positions from beat arrays."""
            if len(fade_out_beats) < num_beats or len(fade_in_beats) < num_beats:
                return None

            fade_in_slice = fade_in_beats[:num_beats]
            return float(fade_in_slice[0])

        # Try downbeats first for most musical timing
        downbeat_positions = calculate_beat_positions(
            self.extrapolated_fadeout_downbeats, self.fade_in_downbeats, crossfade_bars
        )
        if downbeat_positions:
            return downbeat_positions

        # Try regular beats if downbeats insufficient
        required_beats = crossfade_bars * beats_per_bar
        beat_positions = calculate_beat_positions(
            self.fade_out_beats, self.fade_in_beats, required_beats
        )
        if beat_positions:
            return beat_positions

        # Fallback: No beat alignment possible
        self.logger.log(VERBOSE_LOG_LEVEL, "No beat alignment possible (insufficient beats)")
        return None

    def _adjust_crossfade_to_downbeats(
        self,
        crossfade_duration: float,
        fadein_start_pos: float | None,
    ) -> float:
        """Adjust crossfade duration to align with outgoing track's downbeats."""
        # If we don't have downbeats or beat alignment is disabled, return original duration
        if len(self.extrapolated_fadeout_downbeats) == 0 or fadein_start_pos is None:
            return crossfade_duration

        # Calculate where the crossfade would start in the buffer
        ideal_start_pos = SMART_CROSSFADE_DURATION - crossfade_duration

        # Debug logging
        self.logger.log(
            VERBOSE_LOG_LEVEL,
            "Downbeat adjustment - ideal_start=%.2fs (buffer=%.1fs - crossfade=%.2fs), "
            "fadein_start=%.2fs",
            ideal_start_pos,
            SMART_CROSSFADE_DURATION,
            crossfade_duration,
            fadein_start_pos,
        )

        # Find the closest downbeats (earlier and later)
        earlier_downbeat = None
        later_downbeat = None

        for downbeat in self.extrapolated_fadeout_downbeats:
            if downbeat <= ideal_start_pos:
                earlier_downbeat = downbeat
            elif downbeat > ideal_start_pos and later_downbeat is None:
                later_downbeat = downbeat
                break

        # Try earlier downbeat first (longer crossfade)
        if earlier_downbeat is not None:
            adjusted_duration = float(SMART_CROSSFADE_DURATION - earlier_downbeat)
            if fadein_start_pos + adjusted_duration <= SMART_CROSSFADE_DURATION:
                if abs(adjusted_duration - crossfade_duration) > 0.1:
                    self.logger.log(
                        VERBOSE_LOG_LEVEL,
                        "Adjusted crossfade duration from %.2fs to %.2fs to align with "
                        "downbeat at %.2fs (earlier)",
                        crossfade_duration,
                        adjusted_duration,
                        earlier_downbeat,
                    )
                return adjusted_duration

        # Try later downbeat (shorter crossfade)
        if later_downbeat is not None:
            adjusted_duration = float(SMART_CROSSFADE_DURATION - later_downbeat)
            if fadein_start_pos + adjusted_duration <= SMART_CROSSFADE_DURATION:
                if abs(adjusted_duration - crossfade_duration) > 0.1:
                    self.logger.log(
                        VERBOSE_LOG_LEVEL,
                        "Adjusted crossfade duration from %.2fs to %.2fs to align with "
                        "downbeat at %.2fs (later)",
                        crossfade_duration,
                        adjusted_duration,
                        later_downbeat,
                    )
                return adjusted_duration

        # If no suitable downbeat found, return original duration
        self.logger.log(
            VERBOSE_LOG_LEVEL,
            "Could not adjust crossfade duration to downbeats, using original %.2fs",
            crossfade_duration,
        )
        return crossfade_duration


class StandardCrossFade(SmartFade):
    """Standard crossfade class that implements a standard crossfade mode."""

    def __init__(self, logger: logging.Logger, crossfade_duration: float = 10.0) -> None:
        """Initialize StandardCrossFade with crossfade duration."""
        self.crossfade_duration = crossfade_duration
        super().__init__(logger)

    def _build(self) -> None:
        """Build the standard crossfade filter chain."""
        self.filters = [
            CrossfadeFilter(logger=self.logger, crossfade_duration=self.crossfade_duration),
        ]

    async def apply(
        self, fade_out_part: bytes, fade_in_part: bytes, pcm_format: AudioFormat
    ) -> bytes:
        """Apply the standard crossfade to the given PCM audio parts."""
        # We need to override the default apply here, since standard crossfade only needs to be
        # applied to the overlapping parts, not the full buffers.
        crossfade_size = int(pcm_format.pcm_sample_size * self.crossfade_duration)
        # Pre-crossfade: outgoing track minus the crossfaded portion
        pre_crossfade = fade_out_part[:-crossfade_size]
        # Post-crossfade: incoming track minus the crossfaded portion
        post_crossfade = fade_in_part[crossfade_size:]
        # Adjust portions to exact crossfade size
        adjusted_fade_in_part = fade_in_part[:crossfade_size]
        adjusted_fade_out_part = fade_out_part[-crossfade_size:]
        # Adjust the duration to match actual sizes
        self.crossfade_duration = min(
            len(adjusted_fade_in_part) / pcm_format.pcm_sample_size,
            len(adjusted_fade_out_part) / pcm_format.pcm_sample_size,
        )
        # Crossfaded portion: user's configured duration
        crossfaded_section = await super().apply(
            adjusted_fade_out_part, adjusted_fade_in_part, pcm_format
        )
        # Full result: everything concatenated
        return pre_crossfade + crossfaded_section + post_crossfade


# HELPER METHODS
def get_bpm_diff_percentage(bpm1: float, bpm2: float) -> float:
    """Calculate BPM difference percentage between two BPM values."""
    return abs(1.0 - bpm1 / bpm2) * 100


def extrapolate_downbeats(
    downbeats: npt.NDArray[np.float64],
    tempo_factor: float,
    buffer_size: float = SMART_CROSSFADE_DURATION,
    bpm: float | None = None,
) -> npt.NDArray[np.float64]:
    """Extrapolate downbeats based on actual intervals when detection is incomplete.

    This is needed when we want to perform beat alignment in an 'atmospheric' outro
    that does not have any detected downbeats.

    Args:
        downbeats: Array of detected downbeat positions in seconds
        tempo_factor: Tempo adjustment factor for time stretching
        buffer_size: Maximum buffer size in seconds
        bpm: Optional BPM for validation when extrapolating with only 2 downbeats
    """
    # Handle case with exactly 2 downbeats (with BPM validation)
    if len(downbeats) == 2 and bpm is not None:
        interval = float(downbeats[1] - downbeats[0])

        # Expected interval for this BPM (assuming 4/4 time signature)
        expected_interval = (60.0 / bpm) * 4

        # Only extrapolate if interval matches BPM within 15% tolerance
        if abs(interval - expected_interval) / expected_interval < 0.15:
            # Adjust detected downbeats for time stretching first
            adjusted_downbeats = downbeats / tempo_factor
            last_downbeat = adjusted_downbeats[-1]

            # If the last downbeat is close to the buffer end, no extrapolation needed
            if last_downbeat >= buffer_size - 5:
                return adjusted_downbeats

            # Adjust the interval for time stretching
            adjusted_interval = interval / tempo_factor

            # Extrapolate forward from last adjusted downbeat using adjusted interval
            extrapolated = []
            current_pos = last_downbeat + adjusted_interval
            max_extrapolation_distance = 125.0  # Don't extrapolate more than 25s

            while (
                current_pos < buffer_size
                and (current_pos - last_downbeat) <= max_extrapolation_distance
            ):
                extrapolated.append(current_pos)
                current_pos += adjusted_interval

            if extrapolated:
                # Combine adjusted detected downbeats and extrapolated downbeats
                return np.concatenate([adjusted_downbeats, np.array(extrapolated)])

            return adjusted_downbeats
        # else: interval doesn't match BPM, fall through to return original

    if len(downbeats) < 2:
        # Need at least 2 downbeats to extrapolate
        return downbeats / tempo_factor

    # Adjust detected downbeats for time stretching first
    adjusted_downbeats = downbeats / tempo_factor
    last_downbeat = adjusted_downbeats[-1]

    # If the last downbeat is close to the buffer end, no extrapolation needed
    if last_downbeat >= buffer_size - 5:
        return adjusted_downbeats

    # Calculate intervals from ORIGINAL downbeats (before time stretching)
    intervals = np.diff(downbeats)
    median_interval = float(np.median(intervals))
    std_interval = float(np.std(intervals))

    # Only extrapolate if intervals are consistent (low standard deviation)
    if std_interval > 0.2:
        return adjusted_downbeats

    # Adjust the interval for time stretching
    # When slowing down (tempo_factor < 1.0), intervals get longer
    adjusted_interval = median_interval / tempo_factor

    # Extrapolate forward from last adjusted downbeat using adjusted interval
    extrapolated = []
    current_pos = last_downbeat + adjusted_interval
    max_extrapolation_distance = 25.0  # Don't extrapolate more than 25s

    while current_pos < buffer_size and (current_pos - last_downbeat) <= max_extrapolation_distance:
        extrapolated.append(current_pos)
        current_pos += adjusted_interval

    if extrapolated:
        # Combine adjusted detected downbeats and extrapolated downbeats
        return np.concatenate([adjusted_downbeats, np.array(extrapolated)])

    return adjusted_downbeats
