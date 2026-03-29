"""Crossfade alignment resolution.

Routes through three energy contexts to determine crossfade parameters:
  A) Outgoing knee found — fade from knee to buffer end
  B) Both tracks quiet — long key-driven fade
  C) No knee, energy present — ratio-based fade

Falls back to bar-count alignment when no energy data is available.
"""

from __future__ import annotations

import logging
from typing import Any

import numpy as np
import numpy.typing as npt

from music_assistant.controllers.streams.smart_fades.helpers import (
    SMART_CROSSFADE_DURATION,
    get_bpm_diff_percentage,
)
from music_assistant.controllers.streams.smart_fades.models import AlignmentResult
from music_assistant.models.audio_analysis import AudioAnalysisData

__all__ = ["AlignmentResult", "resolve_alignment"]

logger = logging.getLogger(__name__)


# Smoothing window for energy curve (seconds)
_SMOOTH_WINDOW = 3
# Energy must drop below this fraction of peak to be considered "declining"
_DECLINE_THRESHOLD = 0.85

# Spectral centroid constants (noisier signal, needs wider window + looser thresholds)
_SPECTRAL_SMOOTH_WINDOW = 5
_SPECTRAL_DECLINE_THRESHOLD = 0.75

# Incoming track characterization constants
_QUIET_INTRO_THRESHOLD = 0.15
_QUIET_INTRO_WINDOW = 10

# Both-quiet context threshold: peak energy below this means "quiet track"
_QUIET_THRESHOLD = 0.20
# Energy ratio above this triggers a short fade (incoming much louder)
_ENERGY_RATIO_SHORT_FADE = 3.0


def resolve_alignment(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    logger: logging.Logger | None = None,
) -> AlignmentResult:
    """Resolve crossfade alignment using three energy contexts.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param logger: Optional logger for debug output.
    :return: AlignmentResult with positions in source-audio time.
    """
    fadeout_downbeats_rel, fadein_downbeats_rel = _extract_buffer_and_downbeats(
        fade_out_analysis, fade_in_analysis
    )

    fade_out_energy = fade_out_analysis.energy_curve
    fade_in_energy = fade_in_analysis.energy_curve
    fade_out_duration = fade_out_analysis.duration or 0.0
    fade_out_bpm = fade_out_analysis.bpm or 120.0
    fade_in_bpm = fade_in_analysis.bpm or 120.0

    buffer_secs = min(SMART_CROSSFADE_DURATION, int(fade_out_duration))
    energy_out = (
        fade_out_energy[-buffer_secs:] if fade_out_energy is not None and buffer_secs > 0 else None
    )
    energy_in = fade_in_energy[:SMART_CROSSFADE_DURATION] if fade_in_energy is not None else None

    # Step 1: Try to find outgoing knee (energy first, spectral fallback)
    knee = (
        _find_knee(energy_out, fadeout_downbeats_rel, bpm=fade_out_bpm)
        if energy_out is not None
        else None
    )
    if knee is None:
        spectral_out = fade_out_analysis.spectral_centroid_curve
        if spectral_out is not None:
            spectral_buf = spectral_out[-buffer_secs:] if buffer_secs > 0 else spectral_out
            knee = _find_knee(
                _normalize_spectral(spectral_buf), fadeout_downbeats_rel, bpm=fade_out_bpm
            )

    # Step 2: Characterize incoming track + determine fadein entry
    incoming = _characterize_incoming(energy_in)
    fadein_entry = float(fadein_downbeats_rel[0]) if len(fadein_downbeats_rel) > 0 else 0.0

    # Step 3: Route through energy contexts
    bar_duration = 4.0 * (60.0 / fade_in_bpm)

    if knee is not None:
        # Context A: knee found — duration from knee to buffer end
        fadeout_start, _knee_idx = knee
        crossfade_duration = float(len(energy_out) if energy_out is not None else 0) - fadeout_start
        crossfade_duration = max(crossfade_duration, 2.0 * bar_duration)  # min 2 bars

        if not incoming["has_quiet_intro"]:
            # Loud incoming — cap duration so knee does the work
            crossfade_duration = min(crossfade_duration, 4.0 * bar_duration)

        # Snap to bar boundary
        crossfade_duration = round(crossfade_duration / bar_duration) * bar_duration
        strategy = "energy"

    elif energy_out is not None and energy_in is not None and _is_both_quiet(energy_out, energy_in):
        # Context B: both quiet — long key-driven duration
        crossfade_duration = float(SMART_CROSSFADE_DURATION)
        fadeout_start = max(0.0, SMART_CROSSFADE_DURATION - crossfade_duration)
        strategy = "quiet"

    elif energy_out is not None and energy_in is not None:
        # Context C: no knee, energy present — ratio-based
        out_mean = (
            float(np.mean(energy_out[-20:]))
            if len(energy_out) >= 20
            else float(np.mean(energy_out))
        )
        in_mean = (
            float(np.mean(energy_in[:20])) if len(energy_in) >= 20 else float(np.mean(energy_in))
        )
        energy_ratio = in_mean / max(out_mean, 0.01)

        if energy_ratio > _ENERGY_RATIO_SHORT_FADE:
            crossfade_duration = 2.0 * bar_duration
        elif energy_ratio > 1.5:
            crossfade_duration = 4.0 * bar_duration
        else:
            crossfade_duration = 8.0 * bar_duration

        fadeout_start = max(0.0, float(len(energy_out)) - crossfade_duration)
        strategy = "energy_ratio"

    else:
        # No energy data at all — bar-count fallback
        return _bar_count_alignment(
            fade_out_analysis,
            fade_in_analysis,
            fadeout_downbeats_rel,
            fadein_downbeats_rel,
            logger,
        )

    # BPM clamp
    bpm_diff_percent = get_bpm_diff_percentage(fade_out_bpm, fade_in_bpm)
    crossfade_duration = _clamp_duration_by_bpm(
        crossfade_duration, fade_in_bpm, bpm_diff_percent, logger
    )

    return AlignmentResult(
        strategy=strategy,
        fadeout_start_pos=fadeout_start,
        fadein_start_pos=fadein_entry,
        crossfade_duration=crossfade_duration,
        fadeout_downbeats_rel=fadeout_downbeats_rel,
    )


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


def _is_both_quiet(
    energy_out: npt.NDArray[np.float32],
    energy_in: npt.NDArray[np.float32],
) -> bool:
    """Check whether both tracks have peak energy below the quiet threshold.

    :param energy_out: Per-second energy for the outgoing buffer region.
    :param energy_in: Per-second energy for the incoming buffer region.
    :return: True if both peaks are below _QUIET_THRESHOLD.
    """
    out_peak = float(np.max(energy_out)) if len(energy_out) > 0 else 0.0
    in_peak = float(np.max(energy_in)) if len(energy_in) > 0 else 0.0
    return out_peak < _QUIET_THRESHOLD and in_peak < _QUIET_THRESHOLD


def _bar_count_alignment(
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    fadeout_downbeats_rel: npt.NDArray[np.float64],
    fadein_downbeats_rel: npt.NDArray[np.float64],
    _logger: logging.Logger | None = None,
) -> AlignmentResult:
    """Fall back to bar-counting alignment when no energy data is available.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param fadeout_downbeats_rel: Buffer-relative downbeats for Song A.
    :param fadein_downbeats_rel: Buffer-relative downbeats for Song B.
    :param _logger: Optional logger for debug output.
    """
    fade_in_bpm = fade_in_analysis.bpm or 120.0
    fade_out_bpm = fade_out_analysis.bpm or 120.0
    bpm_diff_percent = get_bpm_diff_percentage(fade_in_bpm, fade_out_bpm)

    if bpm_diff_percent <= 5.0:
        bars = 10
    elif bpm_diff_percent <= 10.0:
        bars = 6
    else:
        bars = 3

    bar_duration = 4.0 * (60.0 / fade_in_bpm)
    crossfade_duration = min(bars * bar_duration, float(SMART_CROSSFADE_DURATION))
    fadein_start_pos = float(fadein_downbeats_rel[0]) if len(fadein_downbeats_rel) > 0 else 0.0

    return AlignmentResult(
        strategy="bar_count",
        fadeout_start_pos=None,
        fadein_start_pos=fadein_start_pos,
        crossfade_duration=crossfade_duration,
        fadeout_downbeats_rel=fadeout_downbeats_rel,
    )


def _find_knee(
    energy_tail: npt.NDArray[np.float32],
    downbeats: npt.NDArray[np.float64],
    bpm: float = 120.0,
) -> tuple[float, float] | None:
    """Find where the outgoing track should begin fading out.

    Finds the energy knee (where energy drops below 85% of peak), then backs
    up by a number of bars so the crossfade starts while Song A is still strong.

    :param energy_tail: Per-second energy for the last ~45s of the track (buffer-relative).
    :param downbeats: Downbeat timestamps in buffer-relative seconds.
    :param bpm: BPM of the outgoing track (used for bar-length calculations).
    :return: Tuple of (phrase-snapped start position, raw knee index) in buffer-relative seconds,
        or None if no clear decline.
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
    if snapped is None:
        return None
    return (snapped, float(knee_idx))


def _characterize_incoming(
    energy_head: npt.NDArray[np.float32] | None,
) -> dict[str, Any]:
    """Characterize the incoming track's opening for crossfade aggressiveness.

    :param energy_head: Per-second energy for the incoming track buffer, or None.
    :return: Dict with 'has_quiet_intro' (bool) and 'entry_energy' (float).
    """
    if energy_head is None or len(energy_head) == 0:
        return {"has_quiet_intro": False, "entry_energy": 0.5}

    window = min(_QUIET_INTRO_WINDOW, len(energy_head))
    entry_energy = float(np.mean(energy_head[:window]))
    has_quiet_intro = entry_energy < _QUIET_INTRO_THRESHOLD

    return {"has_quiet_intro": has_quiet_intro, "entry_energy": entry_energy}


def _clamp_duration_by_bpm(
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
