"""Energy-contour helpers for smart crossfade alignment.

These functions analyze energy curves at mix time to find optimal
crossfade start points, entry points, and duration. They operate on
the ~45-second crossfade buffers, not full songs.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import numpy as np
import numpy.typing as npt

logger = logging.getLogger(__name__)

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


def _smooth(energy: npt.NDArray[np.float32], window: int = _SMOOTH_WINDOW) -> npt.NDArray[np.float32]:
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
    elif direction == "backward":
        candidates = downbeats[downbeats <= target_sec + 0.5]
        return float(candidates[-1]) if len(candidates) > 0 else None
    else:
        idx = int(np.argmin(np.abs(downbeats - target_sec)))
        return float(downbeats[idx])


def find_fadeout_start(
    energy_tail: npt.NDArray[np.float32],
    downbeats: npt.NDArray[np.float64],
) -> float | None:
    """Find where the outgoing track's energy peaks and begins declining.

    Smooths the energy curve, finds the peak, walks forward to where energy
    drops below 85% of peak (the "energy knee"), and snaps to the nearest
    downbeat.

    :param energy_tail: Per-second energy for the last ~45s of the track (buffer-relative).
    :param downbeats: Downbeat timestamps in buffer-relative seconds.
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
        logger.debug("fadeout_start: knee at sec %d is in smoothing edge zone, returning None", knee_idx)
        return None

    # Verify there's actually a meaningful decline (not just a dip)
    remaining_energy = float(np.mean(smoothed[knee_idx:]))
    if remaining_energy > peak_val * 0.9:
        logger.debug(
            "fadeout_start: knee at sec %d is a transient dip (remaining_avg=%.3f > %.3f), returning None",
            knee_idx,
            remaining_energy,
            peak_val * 0.9,
        )
        return None

    snapped = _snap_to_downbeat(float(knee_idx), downbeats, direction="forward")
    logger.debug(
        "fadeout_start: knee at sec %d (energy=%.3f), threshold=%.3f, remaining_avg=%.3f, snapped=%.1fs",
        knee_idx,
        float(smoothed[knee_idx]),
        threshold,
        remaining_energy,
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
                    "fadein_entry: sustained rise at sec %d but energy=%.3f > guard %.3f (50%% of peak), skipping",
                    i,
                    float(smoothed[i]),
                    track_peak * _LOW_ENERGY_GUARD,
                )
                continue
            entry_idx = max(0, i - 1)
            snapped = _snap_to_downbeat(float(entry_idx), downbeats, direction="backward")
            logger.debug(
                "fadein_entry: rise detected at sec %d (energy=%.3f, gradient=[%.3f,%.3f,%.3f]), "
                "entry_idx=%d, snapped=%.1fs",
                i,
                float(smoothed[i]),
                float(gradient[i]),
                float(gradient[i + 1]),
                float(gradient[i + 2]),
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
    min_seconds: float = 4.0,
    max_seconds: float = 40.0,
) -> float:
    """Calculate crossfade duration from energy handoff.

    Duration = time from fade-in entry until Song B's energy matches
    Song A's level at fade start, plus 1 bar blend buffer.

    :param energy_out: Per-second energy for outgoing track (buffer-relative).
    :param fadeout_start: Fade-out start index in energy_out.
    :param energy_in: Per-second energy for incoming track (buffer-relative).
    :param fadein_entry: Fade-in entry index in energy_in.
    :param bpm: BPM of incoming track (for bar-length blend buffer).
    :param min_seconds: Minimum crossfade duration.
    :param max_seconds: Maximum crossfade duration.
    :return: Crossfade duration in seconds.
    """
    if fadeout_start >= len(energy_out):
        fadeout_start = max(0, len(energy_out) - 1)
    if fadein_entry >= len(energy_in):
        fadein_entry = 0

    out_energy_at_start = float(energy_out[fadeout_start])
    bar_duration = 4 * (60.0 / bpm)

    # Find where incoming track reaches outgoing track's energy level
    match_idx = None
    for i in range(fadein_entry, len(energy_in)):
        if energy_in[i] >= out_energy_at_start:
            duration = float(i - fadein_entry)
            match_idx = i
            break
    else:
        duration = 8 * bar_duration

    # Add 1 bar blend buffer
    duration += bar_duration

    # Snap to nearest bar boundary
    duration = round(duration / bar_duration) * bar_duration
    final_duration = float(np.clip(duration, min_seconds, max_seconds))

    logger.debug(
        "crossfade_duration: out_energy_at_fadeout=%.3f, "
        "in_energy_at_entry=%.3f, energy_match_at_sec=%s, "
        "raw_duration=%.1fs, bar_dur=%.1fs, final=%.1fs",
        out_energy_at_start,
        float(energy_in[fadein_entry]) if fadein_entry < len(energy_in) else 0,
        f"{match_idx}" if match_idx is not None else "never (using 8 bars)",
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
        avg_step = total_change / (n_steps - 1)
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
        sigmoid_values = (sigmoid_values - sigmoid_values[0]) / (sigmoid_values[-1] - sigmoid_values[0])

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
