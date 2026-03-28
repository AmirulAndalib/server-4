"""Crossfade alignment resolution.

Runs the energy -> spectral -> bar-count alignment cascade and returns
an AlignmentResult with positions in source-audio time (unstretched).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import numpy.typing as npt

from music_assistant.constants import VERBOSE_LOG_LEVEL
from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    SMART_CROSSFADE_DURATION,
    calculate_energy_crossfade_duration,
    extrapolate_downbeats,
    find_fadein_entry,
    find_fadeout_start,
    find_spectral_fadein_entry,
    find_spectral_fadeout_start,
    get_bpm_diff_percentage,
    select_crossfade_curve_type,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


@dataclass
class AlignmentResult:
    """Result of crossfade alignment resolution.

    All positions are in source-audio time (unstretched).
    Compensation for time-stretching happens separately via compensate_for_stretch().
    """

    strategy: str
    fadeout_start_pos: float | None
    fadein_start_pos: float | None
    crossfade_duration: float
    curve_type: str | None
    fadeout_downbeats_rel: npt.NDArray[np.float64]


def clamp_duration_by_bpm(
    duration: float,
    bpm: float,
    bpm_diff_percent: float,
    logger: logging.Logger | None = None,
) -> float:
    """Clamp crossfade duration to a BPM-aware maximum bar count.

    :param duration: Crossfade duration in seconds.
    :param bpm: BPM of the incoming track (used for bar duration).
    :param bpm_diff_percent: BPM difference percentage between tracks.
    :param logger: Optional logger for debug output.
    """
    if duration <= 0:
        return duration
    bar_duration = 4 * (60.0 / bpm)
    if bpm_diff_percent <= 5.0:
        max_bars = 16
    elif bpm_diff_percent <= 10.0:
        max_bars = 12
    else:
        max_bars = 4
    max_duration = max_bars * bar_duration
    if duration > max_duration:
        if logger:
            logger.debug(
                "Clamping duration from %.1fs to %.1fs (max %d bars at %.1f%% BPM diff)",
                duration,
                max_duration,
                max_bars,
                bpm_diff_percent,
            )
        return max_duration
    return duration


def _extract_buffer_and_downbeats(
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
) -> tuple[
    float,
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
]:
    """Extract buffer-relative downbeats for both tracks.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :return: Tuple of (outro_start_offset, fadeout_downbeats_rel, fadein_downbeats_rel).
    """
    fade_out_duration = fade_out_analysis.duration or 0.0
    fade_out_downbeats = (
        fade_out_analysis.downbeats if fade_out_analysis.downbeats is not None else np.array([])
    )
    fade_in_downbeats = (
        fade_in_analysis.downbeats if fade_in_analysis.downbeats is not None else np.array([])
    )

    outro_start = max(0.0, fade_out_duration - SMART_CROSSFADE_DURATION)
    out_db_mask = fade_out_downbeats >= outro_start
    fadeout_downbeats_rel = fade_out_downbeats[out_db_mask] - outro_start

    fadein_downbeats_rel = fade_in_downbeats[fade_in_downbeats < SMART_CROSSFADE_DURATION]

    return outro_start, fadeout_downbeats_rel, fadein_downbeats_rel


def _try_energy_alignment(
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    fadeout_downbeats_rel: npt.NDArray[np.float64],
    fadein_downbeats_rel: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> AlignmentResult | None:
    """Attempt energy-contour alignment for crossfade parameters.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param fadeout_downbeats_rel: Buffer-relative downbeats for Song A.
    :param fadein_downbeats_rel: Buffer-relative downbeats for Song B.
    :param logger: Optional logger for debug output.
    """
    fade_out_energy = fade_out_analysis.energy_curve
    fade_in_energy = fade_in_analysis.energy_curve
    if fade_out_energy is None or fade_in_energy is None:
        if logger:
            logger.debug(
                "Energy alignment skipped: fade_out_energy=%s, fade_in_energy=%s",
                "present" if fade_out_energy is not None else "None",
                "present" if fade_in_energy is not None else "None",
            )
        return None

    fade_out_duration = fade_out_analysis.duration or 0.0
    buffer_secs = min(SMART_CROSSFADE_DURATION, int(fade_out_duration))
    energy_out = fade_out_energy[-buffer_secs:] if buffer_secs > 0 else fade_out_energy
    energy_in = fade_in_energy[:SMART_CROSSFADE_DURATION]

    if logger:
        logger.debug(
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

    fade_out_bpm = fade_out_analysis.bpm or 120.0
    fade_in_bpm = fade_in_analysis.bpm or 120.0

    fadeout_start = find_fadeout_start(energy_out, fadeout_downbeats_rel, bpm=fade_out_bpm)
    fadein_entry = find_fadein_entry(energy_in, fadein_downbeats_rel)

    if fadeout_start is None or fadein_entry is None:
        if logger:
            logger.debug(
                "Energy alignment failed: fadeout_start=%s, fadein_entry=%s",
                f"{fadeout_start:.1f}s" if fadeout_start is not None else "None (no clear decline)",
                f"{fadein_entry:.1f}s" if fadein_entry is not None else "None (no clear build)",
            )
        return None

    crossfade_duration = calculate_energy_crossfade_duration(
        energy_out=energy_out,
        fadeout_start=int(fadeout_start),
        energy_in=energy_in,
        fadein_entry=int(fadein_entry),
        bpm=fade_in_bpm,
    )

    # Select curve type based on energy slopes in overlap region
    curve_type: str | None = None
    overlap_out = energy_out[int(fadeout_start) : int(fadeout_start) + int(crossfade_duration)]
    overlap_in = energy_in[int(fadein_entry) : int(fadein_entry) + int(crossfade_duration)]
    if len(overlap_out) > 1 and len(overlap_in) > 1:
        curve_type = select_crossfade_curve_type(overlap_out, overlap_in)

    bpm_diff_percent = get_bpm_diff_percentage(fade_out_bpm, fade_in_bpm)
    crossfade_duration = clamp_duration_by_bpm(
        crossfade_duration, fade_in_bpm, bpm_diff_percent, logger
    )

    return AlignmentResult(
        strategy="energy",
        fadeout_start_pos=fadeout_start,
        fadein_start_pos=fadein_entry,
        crossfade_duration=crossfade_duration,
        curve_type=curve_type,
        fadeout_downbeats_rel=fadeout_downbeats_rel,
    )


def _try_spectral_alignment(
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    fadeout_downbeats_rel: npt.NDArray[np.float64],
    fadein_downbeats_rel: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> AlignmentResult | None:
    """Attempt spectral-centroid alignment as fallback when energy alignment fails.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param fadeout_downbeats_rel: Buffer-relative downbeats for Song A.
    :param fadein_downbeats_rel: Buffer-relative downbeats for Song B.
    :param logger: Optional logger for debug output.
    """
    fade_out_spectral = fade_out_analysis.spectral_centroid_curve
    fade_in_spectral = fade_in_analysis.spectral_centroid_curve
    if fade_out_spectral is None or fade_in_spectral is None:
        if logger:
            logger.debug(
                "Spectral alignment skipped: fade_out_spectral=%s, fade_in_spectral=%s",
                "present" if fade_out_spectral is not None else "None",
                "present" if fade_in_spectral is not None else "None",
            )
        return None

    fade_out_duration = fade_out_analysis.duration or 0.0
    fade_out_bpm = fade_out_analysis.bpm or 120.0
    fade_in_bpm = fade_in_analysis.bpm or 120.0

    buffer_secs = min(SMART_CROSSFADE_DURATION, int(fade_out_duration))
    spectral_out = fade_out_spectral[-buffer_secs:] if buffer_secs > 0 else fade_out_spectral
    spectral_in = fade_in_spectral[:SMART_CROSSFADE_DURATION]

    if logger:
        logger.debug(
            "Spectral alignment attempt: spectral_out=%d values, spectral_in=%d values",
            len(spectral_out),
            len(spectral_in),
        )

    fadeout_start = find_spectral_fadeout_start(
        spectral_out, fadeout_downbeats_rel, bpm=fade_out_bpm
    )
    fadein_entry = find_spectral_fadein_entry(spectral_in, fadein_downbeats_rel)

    if fadeout_start is None or fadein_entry is None:
        if logger:
            logger.debug(
                "Spectral alignment failed: fadeout_start=%s, fadein_entry=%s",
                f"{fadeout_start:.1f}s" if fadeout_start is not None else "None",
                f"{fadein_entry:.1f}s" if fadein_entry is not None else "None",
            )
        return None

    # Use energy-based duration if energy curves available, else BPM-scaled bars
    fade_out_energy = fade_out_analysis.energy_curve
    fade_in_energy = fade_in_analysis.energy_curve
    if fade_out_energy is not None and fade_in_energy is not None:
        energy_out = fade_out_energy[-buffer_secs:] if buffer_secs > 0 else fade_out_energy
        energy_in = fade_in_energy[:SMART_CROSSFADE_DURATION]
        crossfade_duration = calculate_energy_crossfade_duration(
            energy_out=energy_out,
            fadeout_start=int(fadeout_start),
            energy_in=energy_in,
            fadein_entry=int(fadein_entry),
            bpm=fade_in_bpm,
        )
    else:
        bar_duration = 4.0 * (60.0 / fade_in_bpm)
        if fade_in_bpm < 100:
            bars = 8
        elif fade_in_bpm < 140:
            bars = 12
        else:
            bars = 16
        crossfade_duration = bars * bar_duration

    bpm_diff_percent = get_bpm_diff_percentage(fade_out_bpm, fade_in_bpm)
    crossfade_duration = clamp_duration_by_bpm(
        crossfade_duration, fade_in_bpm, bpm_diff_percent, logger
    )

    if logger:
        logger.debug(
            "Spectral alignment successful: fadeout_start=%.1fs, fadein_start=%.1fs, "
            "duration=%.1fs",
            fadeout_start,
            fadein_entry,
            crossfade_duration,
        )

    return AlignmentResult(
        strategy="spectral",
        fadeout_start_pos=fadeout_start,
        fadein_start_pos=fadein_entry,
        crossfade_duration=crossfade_duration,
        curve_type="qsin",
        fadeout_downbeats_rel=fadeout_downbeats_rel,
    )


def _calculate_optimal_crossfade_bars(
    *,
    fade_in_bpm: float,
    fade_out_bpm: float,
    extrapolated_fadeout_downbeats: npt.NDArray[np.float64],
    fade_in_downbeats: npt.NDArray[np.float64],
    fade_out_beats: npt.NDArray[np.float64],
    fade_in_beats: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> int:
    """Calculate optimal crossfade bars that fit in available buffer."""
    bpm_diff_percent = get_bpm_diff_percentage(fade_in_bpm, fade_out_bpm)

    if bpm_diff_percent <= 5.0:
        ideal_bars = 10
    elif bpm_diff_percent <= 10.0:
        ideal_bars = 6
    else:
        ideal_bars = 3

    for bars in [ideal_bars, 8, 6, 4, 2, 1]:
        if bars > ideal_bars:
            continue

        fadein_start_pos = _calculate_optimal_fade_timing(
            crossfade_bars=bars,
            extrapolated_fadeout_downbeats=extrapolated_fadeout_downbeats,
            fade_in_downbeats=fade_in_downbeats,
            fade_out_beats=fade_out_beats,
            fade_in_beats=fade_in_beats,
            logger=logger,
        )
        if fadein_start_pos is None:
            continue

        test_duration = _calculate_crossfade_duration(
            crossfade_bars=bars, fade_in_bpm=fade_in_bpm, logger=logger
        )
        fadein_buffer = SMART_CROSSFADE_DURATION - fadein_start_pos
        if test_duration <= fadein_buffer:
            if bars < ideal_bars and logger:
                logger.log(
                    VERBOSE_LOG_LEVEL,
                    "Reduced crossfade from %d to %d bars (fadein buffer=%.1fs, needed=%.1fs)",
                    ideal_bars,
                    bars,
                    fadein_buffer,
                    test_duration,
                )
            return bars

    return 1


def _calculate_optimal_fade_timing(
    *,
    crossfade_bars: int,
    extrapolated_fadeout_downbeats: npt.NDArray[np.float64],
    fade_in_downbeats: npt.NDArray[np.float64],
    fade_out_beats: npt.NDArray[np.float64],
    fade_in_beats: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> float | None:
    """Calculate beat positions for alignment."""
    beats_per_bar = 4

    def _calc_beat_positions(
        out_beats: npt.NDArray[np.float64],
        in_beats: npt.NDArray[np.float64],
        num_beats: int,
    ) -> float | None:
        if len(out_beats) < num_beats or len(in_beats) < num_beats:
            return None
        return float(in_beats[:num_beats][0])

    # Try downbeats first
    result = _calc_beat_positions(extrapolated_fadeout_downbeats, fade_in_downbeats, crossfade_bars)
    if result:
        return result

    # Fall back to regular beats
    required_beats = crossfade_bars * beats_per_bar
    result = _calc_beat_positions(fade_out_beats, fade_in_beats, required_beats)
    if result:
        return result

    if logger:
        logger.log(VERBOSE_LOG_LEVEL, "No beat alignment possible (insufficient beats)")
    return None


def _calculate_crossfade_duration(
    *,
    crossfade_bars: int,
    fade_in_bpm: float,
    logger: logging.Logger | None = None,
) -> float:
    """Calculate final crossfade duration based on musical bars and BPM."""
    beats_per_bar = 4
    seconds_per_beat = 60.0 / fade_in_bpm
    musical_duration = crossfade_bars * beats_per_bar * seconds_per_beat
    actual_duration = min(musical_duration, SMART_CROSSFADE_DURATION)

    if musical_duration > SMART_CROSSFADE_DURATION and logger:
        logger.log(
            VERBOSE_LOG_LEVEL,
            "Constraining crossfade duration from %.1fs to %.1fs (buffer limit)",
            musical_duration,
            actual_duration,
        )
    return actual_duration


def _adjust_crossfade_to_downbeats(
    *,
    crossfade_duration: float,
    fadein_start_pos: float | None,
    extrapolated_fadeout_downbeats: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> float:
    """Adjust crossfade duration to align with outgoing track's downbeats."""
    if len(extrapolated_fadeout_downbeats) == 0 or fadein_start_pos is None:
        return crossfade_duration

    ideal_start_pos = SMART_CROSSFADE_DURATION - crossfade_duration

    if logger:
        logger.log(
            VERBOSE_LOG_LEVEL,
            "Downbeat adjustment - ideal_start=%.2fs (buffer=%.1fs - crossfade=%.2fs), "
            "fadein_start=%.2fs",
            ideal_start_pos,
            SMART_CROSSFADE_DURATION,
            crossfade_duration,
            fadein_start_pos,
        )

    earlier_downbeat = None
    later_downbeat = None
    for downbeat in extrapolated_fadeout_downbeats:
        if downbeat <= ideal_start_pos:
            earlier_downbeat = downbeat
        elif downbeat > ideal_start_pos and later_downbeat is None:
            later_downbeat = downbeat
            break

    if earlier_downbeat is not None:
        adjusted_duration = float(SMART_CROSSFADE_DURATION - earlier_downbeat)
        if fadein_start_pos + adjusted_duration <= SMART_CROSSFADE_DURATION:
            if abs(adjusted_duration - crossfade_duration) > 0.1 and logger:
                logger.log(
                    VERBOSE_LOG_LEVEL,
                    "Adjusted crossfade duration from %.2fs to %.2fs (earlier downbeat at %.2fs)",
                    crossfade_duration,
                    adjusted_duration,
                    earlier_downbeat,
                )
            return adjusted_duration

    if later_downbeat is not None:
        adjusted_duration = float(SMART_CROSSFADE_DURATION - later_downbeat)
        if fadein_start_pos + adjusted_duration <= SMART_CROSSFADE_DURATION:
            if abs(adjusted_duration - crossfade_duration) > 0.1 and logger:
                logger.log(
                    VERBOSE_LOG_LEVEL,
                    "Adjusted crossfade duration from %.2fs to %.2fs (later downbeat at %.2fs)",
                    crossfade_duration,
                    adjusted_duration,
                    later_downbeat,
                )
            return adjusted_duration

    if logger:
        logger.log(
            VERBOSE_LOG_LEVEL,
            "Could not adjust crossfade duration to downbeats, using original %.2fs",
            crossfade_duration,
        )
    return crossfade_duration


def _bar_count_alignment(
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    extrapolated_fadeout_downbeats: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> AlignmentResult:
    """Fall back to bar-counting alignment when energy/spectral both fail.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param extrapolated_fadeout_downbeats: Extrapolated downbeats for Song A.
    :param logger: Optional logger for debug output.
    """
    fade_in_bpm = fade_in_analysis.bpm or 120.0
    fade_out_bpm = fade_out_analysis.bpm or 120.0
    fade_out_beats = (
        fade_out_analysis.beats if fade_out_analysis.beats is not None else np.array([])
    )
    fade_in_beats = fade_in_analysis.beats if fade_in_analysis.beats is not None else np.array([])
    fade_in_downbeats = (
        fade_in_analysis.downbeats if fade_in_analysis.downbeats is not None else np.array([])
    )
    fade_out_downbeats = (
        fade_out_analysis.downbeats if fade_out_analysis.downbeats is not None else np.array([])
    )

    bpm_diff_percent = get_bpm_diff_percentage(fade_in_bpm, fade_out_bpm)

    if logger:
        logger.debug(
            "Bar-count alignment fallback (BPM diff=%.1f%%, bpm_ratio=%.3f)",
            bpm_diff_percent,
            fade_in_bpm / fade_out_bpm,
        )

    crossfade_bars = _calculate_optimal_crossfade_bars(
        fade_in_bpm=fade_in_bpm,
        fade_out_bpm=fade_out_bpm,
        extrapolated_fadeout_downbeats=extrapolated_fadeout_downbeats,
        fade_in_downbeats=fade_in_downbeats,
        fade_out_beats=fade_out_beats,
        fade_in_beats=fade_in_beats,
        logger=logger,
    )
    fadein_start_pos = _calculate_optimal_fade_timing(
        crossfade_bars=crossfade_bars,
        extrapolated_fadeout_downbeats=extrapolated_fadeout_downbeats,
        fade_in_downbeats=fade_in_downbeats,
        fade_out_beats=fade_out_beats,
        fade_in_beats=fade_in_beats,
        logger=logger,
    )
    crossfade_duration = _calculate_crossfade_duration(
        crossfade_bars=crossfade_bars,
        fade_in_bpm=fade_in_bpm,
        logger=logger,
    )

    crossfade_duration = _adjust_crossfade_to_downbeats(
        crossfade_duration=crossfade_duration,
        fadein_start_pos=fadein_start_pos,
        extrapolated_fadeout_downbeats=extrapolated_fadeout_downbeats,
        logger=logger,
    )

    # Buffer-relative downbeats for Song A
    fade_out_duration = fade_out_analysis.duration or 0.0
    outro_start = max(0.0, fade_out_duration - SMART_CROSSFADE_DURATION)
    out_db_mask = fade_out_downbeats >= outro_start
    fadeout_downbeats_rel = fade_out_downbeats[out_db_mask] - outro_start

    return AlignmentResult(
        strategy="bar_count",
        fadeout_start_pos=None,
        fadein_start_pos=fadein_start_pos,
        crossfade_duration=crossfade_duration,
        curve_type=None,
        fadeout_downbeats_rel=fadeout_downbeats_rel,
    )


def resolve_alignment(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    logger: logging.Logger | None = None,
) -> AlignmentResult:
    """Resolve crossfade alignment using energy -> spectral -> bar-count cascade.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param logger: Optional logger for debug output.
    :return: AlignmentResult with positions in source-audio time.
    """
    _outro_start, fadeout_downbeats_rel, fadein_downbeats_rel = _extract_buffer_and_downbeats(
        fade_out_analysis, fade_in_analysis
    )

    # 1. Try energy-contour alignment (preferred)
    result = _try_energy_alignment(
        fade_out_analysis,
        fade_in_analysis,
        fadeout_downbeats_rel,
        fadein_downbeats_rel,
        logger,
    )
    if result is not None:
        return result

    # 2. Try spectral-centroid alignment (fallback)
    if logger:
        logger.debug("Energy alignment failed, trying spectral-centroid alignment")
    result = _try_spectral_alignment(
        fade_out_analysis,
        fade_in_analysis,
        fadeout_downbeats_rel,
        fadein_downbeats_rel,
        logger,
    )
    if result is not None:
        return result

    # 3. Bar-counting fallback (always succeeds)
    if logger:
        logger.debug("Energy and spectral alignment failed, falling back to bar-count alignment")
    extrapolated = extrapolate_downbeats(
        fade_out_analysis.downbeats if fade_out_analysis.downbeats is not None else np.array([]),
        tempo_factor=1.0,
        bpm=fade_out_analysis.bpm,
    )
    return _bar_count_alignment(
        fade_out_analysis,
        fade_in_analysis,
        extrapolated,
        logger,
    )
