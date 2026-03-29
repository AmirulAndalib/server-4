"""Smart Fades - Audio fade implementations."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import aiofiles
import shortuuid

from music_assistant.constants import VERBOSE_LOG_LEVEL
from music_assistant.controllers.streams.smart_fades.alignment import (
    resolve_alignment,
)
from music_assistant.controllers.streams.smart_fades.crossfade_params import (
    resolve_crossfade_params,
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
from music_assistant.controllers.streams.smart_fades.models import (
    AlignmentResult,
    TimeStretchDecision,
)
from music_assistant.controllers.streams.smart_fades.time_stretch import (
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
    # Gradual S-curve stretch is imperceptible up to 6% with beat-level stepping
    time_stretch_bpm_percentage_threshold: float = 6.0
    time_stretch_duration: float = 5.0

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
            stretch_duration=self.time_stretch_duration,
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
            "fadein_entry=%.1fs, duration=%.1fs",
            f"{self.fade_out_analysis.bpm:.0f}->{self.fade_in_analysis.bpm:.0f}",
            alignment.strategy,
            alignment.fadeout_start_pos if alignment.fadeout_start_pos is not None else -1,
            alignment.fadein_start_pos or -1,
            alignment.crossfade_duration or -1,
        )

    def _build_filters(self, alignment: AlignmentResult, stretch: TimeStretchDecision) -> None:
        """Construct the filter chain from alignment and stretch decisions.

        :param alignment: Resolved and compensated alignment result.
        :param stretch: Time-stretch decision.
        """
        params = resolve_crossfade_params(
            fade_out_analysis=self.fade_out_analysis,
            fade_in_analysis=self.fade_in_analysis,
            stretch=stretch,
            logger=self.logger,
        )

        energy_aligned = alignment.strategy in ("energy", "spectral", "energy_partial")

        # Cap crossfade duration: resolver limits based on key/spectral,
        # alignment limits based on available audio and energy positioning
        crossfade_duration = min(alignment.crossfade_duration, params.fade_seconds)
        # EQ sweep curve from resolver (controls transition character)
        eq_curve = params.curve_type

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
            and alignment.fadein_start_pos + crossfade_duration <= SMART_CROSSFADE_DURATION
        ):
            self.filters.append(
                TrimFilter(logger=self.logger, fadein_start_pos=alignment.fadein_start_pos)
            )
        elif alignment.fadein_start_pos is not None:
            self.logger.log(
                VERBOSE_LOG_LEVEL,
                "Skipping beat alignment: not enough audio after trim (%.1fs + %.1fs > %.1fs)",
                alignment.fadein_start_pos,
                crossfade_duration,
                SMART_CROSSFADE_DURATION,
            )

        # Fadeout end position (energy-aligned path)
        fadeout_end_pos: float | None = None
        if energy_aligned and alignment.fadeout_start_pos is not None:
            fadeout_end_pos = alignment.fadeout_start_pos + crossfade_duration
            fadeout_end_pos = min(fadeout_end_pos, SMART_CROSSFADE_DURATION)

        # Lowpass on outgoing track
        if fadeout_end_pos is not None:
            fadeout_eq_duration = min(max(crossfade_duration * 2.5, 8.0), fadeout_end_pos)
            fadeout_eq_start = max(0, fadeout_end_pos - fadeout_eq_duration)
        else:
            fadeout_eq_duration = min(max(crossfade_duration * 2.5, 8.0), SMART_CROSSFADE_DURATION)
            fadeout_eq_start = max(0, SMART_CROSSFADE_DURATION - fadeout_eq_duration)

        self.filters.append(
            FrequencySweepFilter(
                logger=self.logger,
                sweep_type="lowpass",
                target_freq=params.crossover_freq,
                duration=fadeout_eq_duration,
                start_time=fadeout_eq_start,
                sweep_direction="fade_in",
                poles=1,
                curve_type=eq_curve,
                stream_type="fadeout",
            )
        )

        # Highpass on incoming track
        fadein_eq_duration = crossfade_duration / 1.5
        self.filters.append(
            FrequencySweepFilter(
                logger=self.logger,
                sweep_type="highpass",
                target_freq=params.crossover_freq,
                duration=fadein_eq_duration,
                start_time=0,
                sweep_direction="fade_out",
                poles=1,
                curve_type=eq_curve,
                stream_type="fadein",
            )
        )

        # Trim Song A to energy knee
        if fadeout_end_pos is not None and fadeout_end_pos < SMART_CROSSFADE_DURATION:
            self.filters.append(
                FadeoutTrimFilter(logger=self.logger, fadeout_end_pos=fadeout_end_pos)
            )

        # Final crossfade — fixed equal-power (qsin) for constant perceived loudness.
        # The FrequencySweepFilter curve handles the transition character.
        self.filters.append(
            CrossfadeFilter(
                logger=self.logger,
                crossfade_duration=crossfade_duration,
                curve_type="qsin",
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
