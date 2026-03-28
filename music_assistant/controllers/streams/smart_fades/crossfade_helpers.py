"""Signal-based helpers for smart crossfade alignment.

These functions analyze energy and spectral-centroid curves at mix time
to find optimal crossfade start points, entry points, and duration.
They operate on the ~45-second crossfade buffers, not full songs.
"""

from __future__ import annotations

import logging

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

# Buffer size in seconds for crossfade analysis
SMART_CROSSFADE_DURATION = 45

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


def compute_gradual_tempo_steps(
    start_ratio: float,
    end_ratio: float,
    downbeats: npt.NDArray[np.float64],
    max_step_pct: float = 0.005,
) -> list[tuple[float, float]]:
    """Compute S-curve tempo steps aligned to downbeats.

    :param start_ratio: Starting tempo ratio (e.g., 1.0).
    :param end_ratio: Target tempo ratio (e.g., 1.05).
    :param downbeats: Downbeat timestamps to align steps to.
    :param max_step_pct: Maximum tempo change per step as a fraction.
    :return: List of (timestamp_seconds, tempo_ratio) tuples.
    """
    total_change = abs(end_ratio - start_ratio)
    if total_change < 1e-6:
        return []

    min_steps = max(1, int(np.ceil(total_change / max_step_pct)))
    n_steps = min(min_steps, len(downbeats))
    if n_steps < 1:
        return [(0.0, end_ratio)]

    # S-curve (sigmoid) with steepness adapted to keep max step within budget
    if n_steps == 1:
        sigmoid_values = np.array([1.0])
    else:
        # Binary search for the steepest k where max step <= max_step_pct
        k_lo, k_hi = 0.1, 10.0
        for _ in range(20):
            k_mid = (k_lo + k_hi) / 2.0
            x = np.linspace(-1, 1, n_steps)
            s = 1.0 / (1.0 + np.exp(-k_mid * x))
            s = (s - s[0]) / (s[-1] - s[0])
            deltas = np.diff(s) * total_change
            if float(np.max(deltas)) <= max_step_pct:
                k_lo = k_mid
            else:
                k_hi = k_mid
        k = k_lo
        x = np.linspace(-1, 1, n_steps)
        sigmoid_values = 1.0 / (1.0 + np.exp(-k * x))
        sigmoid_values = (sigmoid_values - sigmoid_values[0]) / (
            sigmoid_values[-1] - sigmoid_values[0]
        )

    steps: list[tuple[float, float]] = []
    for i in range(n_steps):
        timestamp = float(downbeats[i]) if i < len(downbeats) else float(downbeats[-1])
        ratio = start_ratio + (end_ratio - start_ratio) * float(sigmoid_values[i])
        steps.append((timestamp, round(ratio, 6)))

    return steps


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

    # Complementary slopes (out declining + in rising at similar rate) → equal-power
    # Divergent slopes (magnitudes differ significantly) → equal-gain
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


def get_bpm_diff_percentage(bpm1: float, bpm2: float) -> float:
    """Calculate BPM difference percentage between two BPM values.

    :param bpm1: First BPM value.
    :param bpm2: Second BPM value.
    """
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

    :param downbeats: Array of detected downbeat positions in seconds.
    :param tempo_factor: Tempo adjustment factor for time stretching.
    :param buffer_size: Maximum buffer size in seconds.
    :param bpm: Optional BPM for validation when extrapolating with only 2 downbeats.
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
            max_extrapolation_distance = 25.0  # Don't extrapolate more than 25s

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
