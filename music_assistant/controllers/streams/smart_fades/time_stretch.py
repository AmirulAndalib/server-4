"""Time-stretch decision and alignment compensation.

Decides whether to apply time stretching based on BPM difference,
and compensates alignment positions for the stretch ratio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

from music_assistant.controllers.streams.smart_fades.alignment import AlignmentResult
from music_assistant.controllers.streams.smart_fades.helpers import (
    SMART_CROSSFADE_DURATION,
    get_bpm_diff_percentage,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


@dataclass
class TimeStretchDecision:
    """Result of the time-stretch decision.

    If apply is True, the outgoing track should be time-stretched by bpm_ratio.
    tempo_steps contains S-curve steps for gradual stretch, or None for instant stretch.
    """

    apply: bool
    bpm_ratio: float
    bpm_diff_percent: float
    tempo_steps: list[tuple[float, float]] | None


def resolve_time_stretch(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    alignment: AlignmentResult,
    threshold_percent: float = 5.0,
    stretch_duration: float = 10.0,
    logger: logging.Logger | None = None,
) -> TimeStretchDecision:
    """Decide whether and how to apply time stretching.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param alignment: Resolved alignment result.
    :param threshold_percent: Max BPM diff (%) for time stretching. Default 5%.
    :param stretch_duration: How long (seconds) the gradual tempo ramp takes.
    :param logger: Optional logger for debug output.
    """
    fade_out_bpm = fade_out_analysis.bpm or 120.0
    fade_in_bpm = fade_in_analysis.bpm or 120.0
    bpm_ratio = fade_in_bpm / fade_out_bpm
    bpm_diff_percent = get_bpm_diff_percentage(fade_out_bpm, fade_in_bpm)

    if logger:
        logger.debug(
            "Time stretch decision: out_bpm=%.1f, in_bpm=%.1f, "
            "ratio=%.4f, diff=%.1f%%, threshold=%.1f%%",
            fade_out_bpm,
            fade_in_bpm,
            bpm_ratio,
            bpm_diff_percent,
            threshold_percent,
        )

    no_stretch = TimeStretchDecision(
        apply=False,
        bpm_ratio=bpm_ratio,
        bpm_diff_percent=bpm_diff_percent,
        tempo_steps=None,
    )

    # Only stretch if diff is meaningful but within threshold
    energy_aligned = alignment.strategy in ("energy", "spectral")
    if not (0.1 < bpm_diff_percent <= threshold_percent):
        return no_stretch

    # For bar-count alignment, only stretch if we have enough bars
    if not energy_aligned:
        bar_duration = 4 * (60.0 / fade_in_bpm)
        crossfade_bars = int(alignment.crossfade_duration / bar_duration) if bar_duration > 0 else 0
        if crossfade_bars <= 4:
            return no_stretch

    fade_out_beats = (
        fade_out_analysis.beats if fade_out_analysis.beats is not None else np.array([])
    )
    fade_out_duration = fade_out_analysis.duration or 0.0

    # Select timestamps for S-curve steps:
    # >3% BPM diff: use beat-level stepping (more steps = smoother)
    # <=3%: use downbeat-level stepping (fewer steps sufficient)
    if bpm_diff_percent > 3.0:
        if energy_aligned:
            buffer_start = max(0, fade_out_duration - SMART_CROSSFADE_DURATION)
            stretch_timestamps = fade_out_beats[fade_out_beats >= buffer_start] - buffer_start
        else:
            stretch_timestamps = fade_out_beats[fade_out_beats < SMART_CROSSFADE_DURATION]
    else:
        stretch_timestamps = alignment.fadeout_downbeats_rel

    # Limit timestamps to stretch_duration window
    stretch_timestamps = stretch_timestamps[stretch_timestamps <= stretch_duration]

    if bpm_diff_percent > 0.5 and len(stretch_timestamps) >= 4:
        tempo_steps = _compute_gradual_tempo_steps(
            start_ratio=1.0,
            end_ratio=bpm_ratio,
            downbeats=stretch_timestamps,
        )
        if tempo_steps:
            return TimeStretchDecision(
                apply=True,
                bpm_ratio=bpm_ratio,
                bpm_diff_percent=bpm_diff_percent,
                tempo_steps=tempo_steps,
            )

    # Fallback: instant stretch (no gradual steps possible)
    return TimeStretchDecision(
        apply=True,
        bpm_ratio=bpm_ratio,
        bpm_diff_percent=bpm_diff_percent,
        tempo_steps=None,
    )


def compensate_for_stretch(
    alignment: AlignmentResult,
    stretch: TimeStretchDecision,
) -> AlignmentResult:
    """Adjust alignment positions for time-stretching.

    Divides fadeout_start_pos by bpm_ratio when stretching is applied.
    fadein_start_pos and crossfade_duration are left in Song B's time domain.

    :param alignment: Alignment result with positions in source-audio time.
    :param stretch: Time-stretch decision.
    :return: New AlignmentResult with compensated positions.
    """
    if not stretch.apply or alignment.fadeout_start_pos is None:
        return alignment

    return replace(
        alignment,
        fadeout_start_pos=alignment.fadeout_start_pos / stretch.bpm_ratio,
    )


def _compute_gradual_tempo_steps(
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
