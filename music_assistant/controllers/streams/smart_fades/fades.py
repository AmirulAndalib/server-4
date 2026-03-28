"""Smart Fades - Audio fade implementations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import aiofiles
import numpy as np
import shortuuid

from music_assistant.constants import VERBOSE_LOG_LEVEL
from music_assistant.controllers.streams.smart_fades.alignment import (
    AlignmentResult,
    resolve_alignment,
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
from music_assistant.controllers.streams.smart_fades.helpers import (
    SMART_CROSSFADE_DURATION,
)
from music_assistant.controllers.streams.smart_fades.time_stretch import (
    TimeStretchDecision,
    compensate_for_stretch,
    resolve_time_stretch,
)
from music_assistant.helpers.process import communicate
from music_assistant.helpers.util import remove_file
from music_assistant.models.audio_analysis import AudioAnalysisData

if TYPE_CHECKING:
    from music_assistant_models.media_items import AudioFormat


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
    # Gradual S-curve stretch is imperceptible up to 8% with beat-level stepping
    time_stretch_bpm_percentage_threshold: float = 8.0

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
        self.fade_out_analysis = fade_out_analysis
        self.fade_in_analysis = fade_in_analysis
        super().__init__(logger)

    def _build(self) -> None:
        """Build the smart fades filter chain."""
        alignment = resolve_alignment(
            fade_out_analysis=self.fade_out_analysis,
            fade_in_analysis=self.fade_in_analysis,
            logger=self.logger,
        )

        stretch = resolve_time_stretch(
            fade_out_analysis=self.fade_out_analysis,
            fade_in_analysis=self.fade_in_analysis,
            alignment=alignment,
            threshold_percent=self.time_stretch_bpm_percentage_threshold,
            logger=self.logger,
        )

        alignment = compensate_for_stretch(alignment, stretch)

        if stretch.apply:
            self.logger.debug(
                "Adjusted energy fadeout_start for time stretch: %.1fs (ratio=%.4f)",
                alignment.fadeout_start_pos or -1,
                stretch.bpm_ratio,
            )

        self._build_filters(alignment, stretch)

        self.logger.info(
            "Smart crossfade: %s BPM, strategy=%s, fadeout_start=%.1fs, "
            "fadein_entry=%.1fs, duration=%.1fs, curve=%s",
            f"{self.fade_out_analysis.bpm:.0f}->{self.fade_in_analysis.bpm:.0f}",
            alignment.strategy,
            alignment.fadeout_start_pos if alignment.fadeout_start_pos is not None else -1,
            alignment.fadein_start_pos or -1,
            alignment.crossfade_duration or -1,
            alignment.curve_type or "default",
        )

    def _build_filters(self, alignment: AlignmentResult, stretch: TimeStretchDecision) -> None:
        """Construct the filter chain from alignment and stretch decisions.

        :param alignment: Resolved and compensated alignment result.
        :param stretch: Time-stretch decision.
        """
        energy_aligned = alignment.strategy in ("energy", "spectral")
        fade_out_bpm = self.fade_out_analysis.bpm or 120.0
        fade_in_bpm = self.fade_in_analysis.bpm or 120.0

        # Time stretch filter
        if stretch.apply:
            if stretch.tempo_steps:
                self.filters.append(GradualTimeStretchFilter(self.logger, stretch.tempo_steps))
            else:
                self.filters.append(
                    TimeStretchFilter(logger=self.logger, stretch_ratio=stretch.bpm_ratio)
                )

        # Beat alignment trim
        if (
            alignment.fadein_start_pos is not None
            and alignment.fadein_start_pos + alignment.crossfade_duration
            <= SMART_CROSSFADE_DURATION
        ):
            self.filters.append(
                TrimFilter(logger=self.logger, fadein_start_pos=alignment.fadein_start_pos)
            )
        elif alignment.fadein_start_pos is not None:
            self.logger.log(
                VERBOSE_LOG_LEVEL,
                "Skipping beat alignment: not enough audio after trim (%.1fs + %.1fs > %.1fs)",
                alignment.fadein_start_pos,
                alignment.crossfade_duration,
                SMART_CROSSFADE_DURATION,
            )

        # EQ crossover frequency: 90 BPM -> 1500Hz, 140 BPM -> 2500Hz
        avg_bpm = (fade_out_bpm + fade_in_bpm) / 2
        crossover_freq = int(np.clip(1500 + (avg_bpm - 90) * 20, 1500, 2500))
        if abs(stretch.bpm_ratio - 1.0) > 0.3:
            crossover_freq = int(crossover_freq * 0.85)

        # Determine crossfade_bars for curve selection
        bar_duration = 4 * (60.0 / fade_in_bpm)
        crossfade_bars = int(alignment.crossfade_duration / bar_duration) if bar_duration > 0 else 0

        if crossfade_bars < 8:
            fadeout_curve = "exponential"
            fadein_curve = "exponential"
        else:
            fadeout_curve = "logarithmic"
            fadein_curve = "linear"

        # Fadeout end position (energy-aligned path)
        fadeout_end_pos: float | None = None
        if energy_aligned and alignment.fadeout_start_pos is not None:
            fadeout_end_pos = alignment.fadeout_start_pos + alignment.crossfade_duration
            fadeout_end_pos = min(fadeout_end_pos, SMART_CROSSFADE_DURATION)

        # Lowpass on outgoing track
        if fadeout_end_pos is not None:
            fadeout_eq_duration = min(max(alignment.crossfade_duration * 2.5, 8.0), fadeout_end_pos)
            fadeout_eq_start = max(0, fadeout_end_pos - fadeout_eq_duration)
        else:
            fadeout_eq_duration = min(
                max(alignment.crossfade_duration * 2.5, 8.0), SMART_CROSSFADE_DURATION
            )
            fadeout_eq_start = max(0, SMART_CROSSFADE_DURATION - fadeout_eq_duration)

        self.filters.append(
            FrequencySweepFilter(
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
        )

        # Highpass on incoming track
        fadein_eq_duration = alignment.crossfade_duration / 1.5
        self.filters.append(
            FrequencySweepFilter(
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
        )

        # Trim Song A to energy knee
        if fadeout_end_pos is not None and fadeout_end_pos < SMART_CROSSFADE_DURATION:
            self.filters.append(
                FadeoutTrimFilter(logger=self.logger, fadeout_end_pos=fadeout_end_pos)
            )

        # Final crossfade
        self.filters.append(
            CrossfadeFilter(
                logger=self.logger,
                crossfade_duration=alignment.crossfade_duration,
                curve_type=alignment.curve_type,
            )
        )


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
