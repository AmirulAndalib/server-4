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
    extrapolate_downbeats,
    get_bpm_diff_percentage,
)
from music_assistant.models.audio_analysis import AudioAnalysisData

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants for signal-based alignment helpers
# ---------------------------------------------------------------------------

# Smoothing window for energy curve (seconds)
_SMOOTH_WINDOW = 3
# Energy must drop below this fraction of peak to be considered "declining"
_DECLINE_THRESHOLD = 0.85
# Minimum sustained positive gradient to detect a "build" (per second)
_RISE_GRADIENT = 0.05
# Number of consecutive seconds of positive gradient needed
_RISE_SUSTAINED = 3
# Entry point must be below this fraction of track peak to be "low energy"
_LOW_ENERGY_GUARD = 0.5

# Spectral centroid constants (noisier signal, needs wider window + looser thresholds)
_SPECTRAL_SMOOTH_WINDOW = 5
_SPECTRAL_DECLINE_THRESHOLD = 0.75
_SPECTRAL_REMAINING_AVG_GUARD = 0.85


# ---------------------------------------------------------------------------
# Signal-based helpers for crossfade alignment
# ---------------------------------------------------------------------------


def _smooth(
    energy: npt.NDArray[np.float32], window: int = _SMOOTH_WINDOW
) -> npt.NDArray[np.float32]:
    """Apply moving average smoothing to energy curve.

    :param energy: Per-second energy values.
    :param window: Smoothing window in seconds.
    :return: Smoothed energy curve (same length).
    """
    if len(energy) < window:
        return energy
    kernel = np.ones(window, dtype=np.float32) / window
    return np.convolve(energy, kernel, mode="same").astype(np.float32)


def _snap_to_downbeat(
    target_sec: float,
    downbeats: npt.NDArray[np.float64],
    direction: str = "nearest",
) -> float | None:
    """Snap a time position to the nearest downbeat.

    :param target_sec: Target time in seconds (buffer-relative).
    :param downbeats: Downbeat timestamps (buffer-relative).
    :param direction: 'nearest', 'forward' (at or after), or 'backward' (at or before).
    :return: Snapped time, or None if no suitable downbeat found.
    """
    if len(downbeats) == 0:
        return None

    if direction == "forward":
        candidates = downbeats[downbeats >= target_sec - 0.5]
        return float(candidates[0]) if len(candidates) > 0 else None
    if direction == "backward":
        candidates = downbeats[downbeats <= target_sec + 0.5]
        return float(candidates[-1]) if len(candidates) > 0 else None
    idx = int(np.argmin(np.abs(downbeats - target_sec)))
    return float(downbeats[idx])


def _snap_to_phrase_boundary(
    target_sec: float,
    downbeats: npt.NDArray[np.float64],
    phrase_len: int = 8,
    direction: str = "nearest",
) -> float | None:
    """Snap a time position to the nearest phrase boundary.

    A phrase boundary is every Nth downbeat (default 8 bars). Falls back
    to 4-bar boundaries if 8-bar snap moves the position too far, and
    to plain downbeat snapping if too few downbeats are available.

    :param target_sec: Target time in seconds (buffer-relative).
    :param downbeats: Downbeat timestamps (buffer-relative).
    :param phrase_len: Number of bars per phrase (default 8).
    :param direction: 'nearest', 'forward', or 'backward'.
    :return: Snapped time, or None if no suitable boundary found.
    """
    if len(downbeats) < phrase_len:
        return _snap_to_downbeat(target_sec, downbeats, direction)

    phrase_boundaries = downbeats[::phrase_len]
    result = _snap_to_downbeat(target_sec, phrase_boundaries, direction)

    if result is not None and phrase_len == 8 and len(downbeats) >= 4:
        bar_dur = float(np.median(np.diff(downbeats))) if len(downbeats) > 1 else 2.0
        if abs(result - target_sec) > 4 * bar_dur:
            four_bar = _snap_to_downbeat(target_sec, downbeats[::4], direction)
            if four_bar is not None:
                return four_bar

    return result


def find_fadeout_start(
    energy_tail: npt.NDArray[np.float32],
    downbeats: npt.NDArray[np.float64],
    bpm: float = 120.0,
) -> float | None:
    """Find where the outgoing track should begin fading out.

    Finds the energy knee (where energy drops below 85% of peak), then backs
    up by a number of bars so the crossfade starts while Song A is still strong.
    This gives the incoming track time to build under Song A before the handoff.

    :param energy_tail: Per-second energy for the last ~45s of the track (buffer-relative).
    :param downbeats: Downbeat timestamps in buffer-relative seconds.
    :param bpm: BPM of the outgoing track (used for bar-length calculations).
    :return: Fade-out start time in buffer-relative seconds, or None if no clear decline.
    """
    if len(energy_tail) < 4:
        logger.debug("fadeout_start: too short (%d values)", len(energy_tail))
        return None

    smoothed = _smooth(energy_tail)
    peak_idx = int(np.argmax(smoothed))
    peak_val = float(smoothed[peak_idx])

    logger.debug(
        "fadeout_start: smoothed peak=%.3f at sec %d, tail energy=[%s...%s]",
        peak_val,
        peak_idx,
        ", ".join(f"{v:.2f}" for v in smoothed[:5]),
        ", ".join(f"{v:.2f}" for v in smoothed[-5:]),
    )

    if peak_val < 0.05:
        logger.debug("fadeout_start: near-silence (peak=%.3f), returning None", peak_val)
        return None

    threshold = peak_val * _DECLINE_THRESHOLD

    # Walk forward from peak to find energy knee
    knee_idx = None
    for i in range(peak_idx, len(smoothed)):
        if smoothed[i] < threshold:
            knee_idx = i
            break

    if knee_idx is None:
        logger.debug(
            "fadeout_start: no decline found — energy stays above %.3f (85%% of peak) until end",
            threshold,
        )
        return None

    # Ignore edge effects from smoothing (knee in last few samples of a flat signal)
    if knee_idx >= len(smoothed) - _SMOOTH_WINDOW:
        logger.debug(
            "fadeout_start: knee at sec %d is in smoothing edge zone, returning None", knee_idx
        )
        return None

    # Verify there's actually a meaningful decline (not just a dip)
    remaining_energy = float(np.mean(smoothed[knee_idx:]))
    if remaining_energy > peak_val * 0.9:
        logger.debug(
            "fadeout_start: knee at sec %d is a transient dip "
            "(remaining_avg=%.3f > %.3f), returning None",
            knee_idx,
            remaining_energy,
            peak_val * 0.9,
        )
        return None

    # Back up from the knee so the crossfade starts while Song A is still strong.
    # A DJ would begin the crossfade ~4 bars before the energy starts dropping,
    # so the listener hears Song B building under a still-confident Song A.
    bar_duration = 4.0 * (60.0 / bpm)
    early_start_offset = 4.0 * bar_duration  # 4 bars before the knee
    early_start_idx = max(0, knee_idx - early_start_offset)

    snapped = _snap_to_phrase_boundary(float(early_start_idx), downbeats, direction="backward")
    if snapped is None:
        snapped = _snap_to_phrase_boundary(float(early_start_idx), downbeats, direction="forward")

    logger.debug(
        "fadeout_start: knee at sec %d (energy=%.3f), threshold=%.3f, remaining_avg=%.3f, "
        "early_start=%.1fs (4 bars before knee), snapped=%.1fs",
        knee_idx,
        float(smoothed[knee_idx]),
        threshold,
        remaining_energy,
        early_start_idx,
        snapped if snapped is not None else -1,
    )
    return snapped


def find_fadein_entry(
    energy_head: npt.NDArray[np.float32],
    downbeats: npt.NDArray[np.float64],
) -> float | None:
    """Find where the incoming track is low-energy and about to rise.

    Finds the first sustained positive gradient in the smoothed energy
    curve, verifies that absolute energy is low at that point, and
    snaps to the nearest downbeat before the rise.

    :param energy_head: Per-second energy for the first ~45s of the track (buffer-relative).
    :param downbeats: Downbeat timestamps in buffer-relative seconds.
    :return: Fade-in entry time in buffer-relative seconds, or None if no clear build.
    """
    if len(energy_head) < _RISE_SUSTAINED + 2:
        logger.debug("fadein_entry: too short (%d values)", len(energy_head))
        return None

    smoothed = _smooth(energy_head)
    gradient = np.gradient(smoothed)
    track_peak = float(np.max(smoothed))

    logger.debug(
        "fadein_entry: smoothed peak=%.3f, head energy=[%s...%s]",
        track_peak,
        ", ".join(f"{v:.2f}" for v in smoothed[:5]),
        ", ".join(f"{v:.2f}" for v in smoothed[-5:]),
    )

    if track_peak < 0.05:
        logger.debug("fadein_entry: near-silence (peak=%.3f), returning None", track_peak)
        return None

    # Find first sustained positive gradient (building energy)
    for i in range(len(gradient) - _RISE_SUSTAINED):
        if all(gradient[i : i + _RISE_SUSTAINED] > _RISE_GRADIENT):
            # Verify energy is actually low here (guard against "already loud" sections)
            if smoothed[i] > track_peak * _LOW_ENERGY_GUARD:
                logger.debug(
                    "fadein_entry: sustained rise at sec %d but energy=%.3f "
                    "> guard %.3f (50%% of peak), skipping",
                    i,
                    float(smoothed[i]),
                    track_peak * _LOW_ENERGY_GUARD,
                )
                continue

            # Back up into Song B's quiet section before the rise begins.
            # A DJ would start the incoming track during its intro/ambient section,
            # letting it play softly for several bars before the energy build.
            # Look for a quiet region before the rise (energy below 20% of peak).
            quiet_threshold = track_peak * 0.2
            quiet_start = i
            for j in range(i - 1, -1, -1):
                if smoothed[j] <= quiet_threshold:
                    quiet_start = j
                    break
                # Don't go back more than 20 seconds from the rise
                if i - j > 20:
                    quiet_start = j
                    break

            entry_idx = max(0, quiet_start)
            snapped = _snap_to_phrase_boundary(float(entry_idx), downbeats, direction="backward")
            if snapped is None:
                snapped = _snap_to_phrase_boundary(float(entry_idx), downbeats, direction="forward")
            logger.debug(
                "fadein_entry: rise detected at sec %d (energy=%.3f, gradient=[%.3f,%.3f,%.3f]), "
                "quiet_start=%d, entry_idx=%d, snapped=%.1fs",
                i,
                float(smoothed[i]),
                float(gradient[i]),
                float(gradient[i + 1]),
                float(gradient[i + 2]),
                quiet_start,
                entry_idx,
                snapped if snapped is not None else -1,
            )
            return snapped

    logger.debug(
        "fadein_entry: no sustained rise > %.3f found in %d seconds. "
        "Max gradient=%.3f at sec %d. Low energy guard=%.3f",
        _RISE_GRADIENT,
        len(gradient),
        float(np.max(gradient)),
        int(np.argmax(gradient)),
        track_peak * _LOW_ENERGY_GUARD,
    )
    return None


def calculate_energy_crossfade_duration(
    energy_out: npt.NDArray[np.float32],
    fadeout_start: int,
    energy_in: npt.NDArray[np.float32],
    fadein_entry: int,
    bpm: float,
    max_seconds: float = 40.0,
) -> float:
    """Calculate crossfade duration from energy handoff.

    The energy match point (where Song B reaches Song A's level) is treated as
    the approximate midpoint of the crossfade, not the endpoint. The crossfade
    extends beyond the match to allow Song B to fully establish itself. A musical
    minimum of 8 bars ensures the crossfade never feels abrupt.

    :param energy_out: Per-second energy for outgoing track (buffer-relative).
    :param fadeout_start: Fade-out start index in energy_out.
    :param energy_in: Per-second energy for incoming track (buffer-relative).
    :param fadein_entry: Fade-in entry index in energy_in.
    :param bpm: BPM of incoming track (for bar-length blend buffer).
    :param max_seconds: Maximum crossfade duration.
    :return: Crossfade duration in seconds.
    """
    if fadeout_start >= len(energy_out):
        fadeout_start = max(0, len(energy_out) - 1)
    if fadein_entry >= len(energy_in):
        fadein_entry = 0

    out_energy_at_start = float(energy_out[fadeout_start])
    bar_duration = 4 * (60.0 / bpm)
    min_duration = 8 * bar_duration  # Musical minimum: 8 bars

    # Find where incoming track reaches outgoing track's energy level
    match_idx = None
    time_to_match = 0.0
    for i in range(fadein_entry, len(energy_in)):
        if energy_in[i] >= out_energy_at_start:
            time_to_match = float(i - fadein_entry)
            match_idx = i
            break
    else:
        time_to_match = 8 * bar_duration

    # The energy match is the midpoint of the crossfade, not the endpoint.
    # Extend 4 bars past the match so Song A fades out gracefully after
    # Song B has taken over, and Song B's energy is fully established.
    post_match_bars = 4
    duration = time_to_match + post_match_bars * bar_duration

    # Enforce musical minimum of 8 bars
    duration = max(duration, min_duration)

    # Snap to nearest bar boundary
    duration = round(duration / bar_duration) * bar_duration
    final_duration = float(np.clip(duration, min_duration, max_seconds))

    logger.debug(
        "crossfade_duration: out_energy_at_fadeout=%.3f, "
        "in_energy_at_entry=%.3f, energy_match_at_sec=%s, "
        "time_to_match=%.1fs, min_musical=%.1fs (8 bars), "
        "raw_duration=%.1fs, bar_dur=%.1fs, final=%.1fs",
        out_energy_at_start,
        float(energy_in[fadein_entry]) if fadein_entry < len(energy_in) else 0,
        f"{match_idx}" if match_idx is not None else "never (using 8 bars)",
        time_to_match,
        min_duration,
        duration,
        bar_duration,
        final_duration,
    )
    return final_duration


def select_crossfade_curve_type(
    outgoing_energy: npt.NDArray[np.float32],
    incoming_energy: npt.NDArray[np.float32],
) -> str:
    """Select crossfade curve based on energy slope comparison.

    :param outgoing_energy: Per-second energy for outgoing track's crossfade region.
    :param incoming_energy: Per-second energy for incoming track's crossfade region.
    :return: FFmpeg acrossfade curve name ('qsin' or 'tri').
    """
    if len(outgoing_energy) < 2 or len(incoming_energy) < 2:
        return "tri"

    out_slope = float(np.polyfit(np.arange(len(outgoing_energy)), outgoing_energy, 1)[0])
    inc_slope = float(np.polyfit(np.arange(len(incoming_energy)), incoming_energy, 1)[0])

    # Complementary slopes (out declining + in rising at similar rate) -> equal-power
    # Divergent slopes (magnitudes differ significantly) -> equal-gain
    slope_sum = abs(out_slope + inc_slope)

    if slope_sum < 0.05:
        return "qsin"  # Equal-power — slopes are complementary
    return "tri"  # Equal-gain — slopes are divergent


# ---------------------------------------------------------------------------
# Spectral-centroid helpers
# ---------------------------------------------------------------------------


def _normalize_spectral(
    spectral: npt.NDArray[np.float32],
) -> npt.NDArray[np.float32]:
    """Min-max normalize spectral centroid (Hz) to 0-1 within the buffer.

    :param spectral: Per-second spectral centroid values in Hz.
    :return: Normalized curve (0-1), or ones if range is negligible.
    """
    sc_min = float(spectral.min())
    sc_range = float(spectral.max()) - sc_min
    if sc_range < 1.0:
        return np.ones_like(spectral)
    return ((spectral - sc_min) / sc_range).astype(np.float32)


def find_spectral_fadeout_start(
    spectral_tail: npt.NDArray[np.float32],
    downbeats: npt.NDArray[np.float64],
    bpm: float = 120.0,
) -> float | None:
    """Find where the outgoing track's spectral brightness starts declining.

    Same logic as find_fadeout_start but operates on the spectral centroid
    curve with wider smoothing and looser thresholds, since spectral centroid
    is noisier and declines more gradually than RMS energy.

    :param spectral_tail: Per-second spectral centroid for the last ~45s (buffer-relative).
    :param downbeats: Downbeat timestamps in buffer-relative seconds.
    :param bpm: BPM of the outgoing track.
    :return: Fade-out start time in buffer-relative seconds, or None if no clear decline.
    """
    if len(spectral_tail) < _SPECTRAL_SMOOTH_WINDOW + 1:
        logger.debug("spectral_fadeout: too short (%d values)", len(spectral_tail))
        return None

    normalized = _normalize_spectral(spectral_tail)
    smoothed = _smooth(normalized, window=_SPECTRAL_SMOOTH_WINDOW)
    peak_idx = int(np.argmax(smoothed))
    peak_val = float(smoothed[peak_idx])

    if peak_val < 0.05:
        logger.debug("spectral_fadeout: near-flat (peak=%.3f), returning None", peak_val)
        return None

    threshold = peak_val * _SPECTRAL_DECLINE_THRESHOLD

    knee_idx = None
    for i in range(peak_idx, len(smoothed)):
        if smoothed[i] < threshold:
            knee_idx = i
            break

    if knee_idx is None:
        logger.debug(
            "spectral_fadeout: no decline — brightness stays above %.3f (%.0f%% of peak) until end",
            threshold,
            _SPECTRAL_DECLINE_THRESHOLD * 100,
        )
        return None

    if knee_idx >= len(smoothed) - _SPECTRAL_SMOOTH_WINDOW:
        logger.debug(
            "spectral_fadeout: knee at sec %d is in smoothing edge zone, returning None",
            knee_idx,
        )
        return None

    remaining_avg = float(np.mean(smoothed[knee_idx:]))
    if remaining_avg > peak_val * _SPECTRAL_REMAINING_AVG_GUARD:
        logger.debug(
            "spectral_fadeout: knee at sec %d is a transient dip "
            "(remaining_avg=%.3f > %.3f), returning None",
            knee_idx,
            remaining_avg,
            peak_val * _SPECTRAL_REMAINING_AVG_GUARD,
        )
        return None

    bar_duration = 4.0 * (60.0 / bpm)
    early_start_offset = 4.0 * bar_duration
    early_start_idx = max(0, knee_idx - early_start_offset)

    snapped = _snap_to_phrase_boundary(float(early_start_idx), downbeats, direction="backward")
    if snapped is None:
        snapped = _snap_to_phrase_boundary(float(early_start_idx), downbeats, direction="forward")

    logger.debug(
        "spectral_fadeout: knee at sec %d (brightness=%.3f), threshold=%.3f, "
        "remaining_avg=%.3f, early_start=%.1fs, snapped=%.1fs",
        knee_idx,
        float(smoothed[knee_idx]),
        threshold,
        remaining_avg,
        early_start_idx,
        snapped if snapped is not None else -1,
    )
    return snapped


def find_spectral_fadein_entry(
    spectral_head: npt.NDArray[np.float32],
    downbeats: npt.NDArray[np.float64],
) -> float | None:
    """Find where the incoming track's spectral brightness begins rising.

    Same logic as find_fadein_entry but operates on the spectral centroid
    curve with wider smoothing.

    :param spectral_head: Per-second spectral centroid for the first ~45s (buffer-relative).
    :param downbeats: Downbeat timestamps in buffer-relative seconds.
    :return: Fade-in entry time in buffer-relative seconds, or None if no clear build.
    """
    if len(spectral_head) < _RISE_SUSTAINED + 2:
        logger.debug("spectral_fadein: too short (%d values)", len(spectral_head))
        return None

    normalized = _normalize_spectral(spectral_head)
    smoothed = _smooth(normalized, window=_SPECTRAL_SMOOTH_WINDOW)
    gradient = np.gradient(smoothed)
    track_peak = float(np.max(smoothed))

    if track_peak < 0.05:
        logger.debug("spectral_fadein: near-flat (peak=%.3f), returning None", track_peak)
        return None

    for i in range(len(gradient) - _RISE_SUSTAINED):
        if all(gradient[i : i + _RISE_SUSTAINED] > _RISE_GRADIENT):
            if smoothed[i] > track_peak * _LOW_ENERGY_GUARD:
                logger.debug(
                    "spectral_fadein: rise at sec %d but brightness=%.3f > guard %.3f, skip",
                    i,
                    float(smoothed[i]),
                    track_peak * _LOW_ENERGY_GUARD,
                )
                continue

            quiet_threshold = track_peak * 0.2
            quiet_start = i
            for j in range(i - 1, -1, -1):
                if smoothed[j] <= quiet_threshold:
                    quiet_start = j
                    break
                if i - j > 20:
                    quiet_start = j
                    break

            entry_idx = max(0, quiet_start)
            snapped = _snap_to_phrase_boundary(float(entry_idx), downbeats, direction="backward")
            if snapped is None:
                snapped = _snap_to_phrase_boundary(float(entry_idx), downbeats, direction="forward")
            logger.debug(
                "spectral_fadein: rise at sec %d (brightness=%.3f), "
                "quiet_start=%d, entry_idx=%d, snapped=%.1fs",
                i,
                float(smoothed[i]),
                quiet_start,
                entry_idx,
                snapped if snapped is not None else -1,
            )
            return snapped

    logger.debug(
        "spectral_fadein: no sustained rise > %.3f found in %d seconds",
        _RISE_GRADIENT,
        len(gradient),
    )
    return None


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
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
]:
    """Extract buffer-relative downbeats for both tracks.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :return: Tuple of (fadeout_downbeats_rel, fadein_downbeats_rel).
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

    return fadeout_downbeats_rel, fadein_downbeats_rel


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
    fadeout_downbeats_rel, fadein_downbeats_rel = _extract_buffer_and_downbeats(
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
