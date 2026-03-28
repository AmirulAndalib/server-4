"""Tests for time-stretch decision and alignment compensation."""

import numpy as np

from music_assistant.controllers.streams.smart_fades.alignment import AlignmentResult
from music_assistant.controllers.streams.smart_fades.time_stretch import (
    TimeStretchDecision,
    _compute_gradual_tempo_steps,
    compensate_for_stretch,
    resolve_time_stretch,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


def _make_analysis(bpm: float = 120.0, duration: float = 180.0) -> AudioAnalysisData:
    """Create AudioAnalysisData with sensible defaults."""
    beats = np.arange(0, duration, 60.0 / bpm)
    downbeats = beats[::4]
    return AudioAnalysisData(
        bpm=bpm,
        beats=beats,
        downbeats=downbeats,
        duration=duration,
    )


def _make_alignment(
    fadeout_start_pos: float | None = 20.0,
    fadein_start_pos: float | None = 5.0,
    crossfade_duration: float = 16.0,
) -> AlignmentResult:
    """Create AlignmentResult with sensible defaults."""
    return AlignmentResult(
        strategy="energy",
        fadeout_start_pos=fadeout_start_pos,
        fadein_start_pos=fadein_start_pos,
        crossfade_duration=crossfade_duration,
        curve_type="qsin",
        fadeout_downbeats_rel=np.arange(0, 45, 2.0),
    )


def test_resolve_time_stretch_within_threshold() -> None:
    """BPM diff within threshold should produce stretch decision with steps."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=124.0)  # ~3.3% diff
    alignment = _make_alignment()

    result = resolve_time_stretch(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        alignment=alignment,
    )

    assert result.apply is True
    assert abs(result.bpm_ratio - 124.0 / 120.0) < 0.001
    assert result.tempo_steps is not None
    assert len(result.tempo_steps) > 0


def test_resolve_time_stretch_above_threshold() -> None:
    """BPM diff above threshold should not apply stretch."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=140.0)  # ~16.7% diff, well above 5%
    alignment = _make_alignment()

    result = resolve_time_stretch(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        alignment=alignment,
    )

    assert result.apply is False


def test_resolve_time_stretch_negligible_diff() -> None:
    """Negligible BPM diff should not apply stretch."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=120.05)  # ~0.04% diff
    alignment = _make_alignment()

    result = resolve_time_stretch(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        alignment=alignment,
    )

    assert result.apply is False


def test_resolve_time_stretch_custom_threshold() -> None:
    """Custom threshold should be respected."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=130.0)  # ~8.3% diff
    alignment = _make_alignment()

    result = resolve_time_stretch(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        alignment=alignment,
        threshold_percent=10.0,  # Higher threshold for DJ mix
    )

    assert result.apply is True


def test_compensate_for_stretch_adjusts_fadeout() -> None:
    """Compensation should divide fadeout_start_pos by bpm_ratio."""
    alignment = _make_alignment(fadeout_start_pos=20.0)
    stretch = TimeStretchDecision(
        apply=True,
        bpm_ratio=0.95,  # Slowing down
        bpm_diff_percent=5.0,
        tempo_steps=[(0.0, 1.0), (10.0, 0.95)],
    )

    result = compensate_for_stretch(alignment, stretch)

    assert result.fadeout_start_pos is not None
    assert abs(result.fadeout_start_pos - 20.0 / 0.95) < 0.01
    # fadein should be unchanged
    assert result.fadein_start_pos == alignment.fadein_start_pos
    # crossfade_duration should be unchanged
    assert result.crossfade_duration == alignment.crossfade_duration


def test_compensate_for_stretch_preserves_fadein() -> None:
    """Compensation should not touch fadein_start_pos."""
    alignment = _make_alignment(fadein_start_pos=5.0)
    stretch = TimeStretchDecision(apply=True, bpm_ratio=1.05, bpm_diff_percent=5.0, tempo_steps=[])

    result = compensate_for_stretch(alignment, stretch)

    assert result.fadein_start_pos == 5.0


def test_compensate_no_stretch() -> None:
    """No stretch should return alignment unchanged."""
    alignment = _make_alignment(fadeout_start_pos=20.0)
    stretch = TimeStretchDecision(
        apply=False, bpm_ratio=1.0, bpm_diff_percent=0.0, tempo_steps=None
    )

    result = compensate_for_stretch(alignment, stretch)

    assert result.fadeout_start_pos == 20.0


def test_compute_gradual_tempo_steps_5_percent() -> None:
    """5% tempo change should produce S-curve steps with max 0.5% per step."""
    downbeats = np.arange(0, 20, 2.0)

    steps = _compute_gradual_tempo_steps(
        start_ratio=1.0,
        end_ratio=1.05,
        downbeats=downbeats,
    )

    assert len(steps) > 0
    ratios = [s[1] for s in steps]
    assert abs(ratios[0] - 1.0) < 0.01
    assert abs(ratios[-1] - 1.05) < 0.001

    # S-curve: middle steps change faster than edges
    if len(ratios) > 4:
        early_delta = abs(ratios[1] - ratios[0])
        mid_idx = len(ratios) // 2
        mid_delta = abs(ratios[mid_idx] - ratios[mid_idx - 1])
        assert mid_delta > early_delta

    # Max step <= 0.5%
    for i in range(1, len(ratios)):
        assert abs(ratios[i] - ratios[i - 1]) <= 0.006
