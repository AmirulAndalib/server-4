"""Tests for crossfade alignment resolution (energy, spectral, bar-count cascade)."""

import numpy as np

from music_assistant.controllers.streams.smart_fades.alignment import (
    AlignmentResult,
    clamp_duration_by_bpm,
    resolve_alignment,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


def _make_analysis(
    bpm: float = 120.0,
    duration: float = 180.0,
    energy_curve: np.ndarray | None = None,
    spectral_centroid_curve: np.ndarray | None = None,
) -> AudioAnalysisData:
    """Create AudioAnalysisData with sensible defaults."""
    beats = np.arange(0, duration, 60.0 / bpm)
    downbeats = beats[::4]  # Every 4th beat is a downbeat
    return AudioAnalysisData(
        bpm=bpm,
        beats=beats,
        downbeats=downbeats,
        duration=duration,
        energy_curve=energy_curve,
        spectral_centroid_curve=spectral_centroid_curve,
    )


def test_resolve_alignment_energy_path() -> None:
    """Energy curves with clear decline/rise should use energy strategy."""
    # Song A: high energy for 155s, then 25s decline in last 45s of song
    out_energy = np.ones(180, dtype=np.float32) * 0.9
    out_energy[155:] = np.linspace(0.9, 0.1, 25).astype(np.float32)

    # Song B: quiet for 10s, then build
    in_energy = np.zeros(180, dtype=np.float32)
    in_energy[:10] = 0.1
    in_energy[10:25] = np.linspace(0.1, 0.9, 15).astype(np.float32)
    in_energy[25:] = 0.9

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert isinstance(result, AlignmentResult)
    assert result.strategy == "energy"
    assert result.fadeout_start_pos is not None
    assert result.fadein_start_pos is not None
    assert result.crossfade_duration > 0


def test_resolve_alignment_spectral_fallback() -> None:
    """Flat energy but declining spectral should use spectral strategy."""
    # Flat energy (no clear decline)
    flat_energy = np.ones(180, dtype=np.float32) * 0.8

    # Song A: bright for 155s, then brightness drops
    out_spectral = np.ones(180, dtype=np.float32) * 3000.0
    out_spectral[155:] = np.linspace(3000.0, 500.0, 25).astype(np.float32)

    # Song B: dim for 10s, then brightness rises
    in_spectral = np.ones(180, dtype=np.float32) * 500.0
    in_spectral[:10] = 500.0
    in_spectral[10:25] = np.linspace(500.0, 3000.0, 15).astype(np.float32)
    in_spectral[25:] = 3000.0

    fade_out = _make_analysis(energy_curve=flat_energy, spectral_centroid_curve=out_spectral)
    fade_in = _make_analysis(energy_curve=flat_energy, spectral_centroid_curve=in_spectral)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "spectral"


def test_resolve_alignment_bar_count_fallback() -> None:
    """No energy or spectral curves should fall back to bar_count."""
    fade_out = _make_analysis()  # No energy/spectral
    fade_in = _make_analysis()

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "bar_count"
    assert result.crossfade_duration > 0


def test_clamp_duration_by_bpm_small_diff() -> None:
    """Small BPM diff should allow up to 16 bars."""
    bar_duration = 4 * (60.0 / 120.0)  # 2s per bar at 120 BPM
    max_16_bars = 16 * bar_duration  # 32s

    result = clamp_duration_by_bpm(duration=40.0, bpm=120.0, bpm_diff_percent=3.0)
    assert result == max_16_bars


def test_clamp_duration_by_bpm_large_diff() -> None:
    """Large BPM diff should clamp to 4 bars."""
    bar_duration = 4 * (60.0 / 120.0)
    max_4_bars = 4 * bar_duration  # 8s

    result = clamp_duration_by_bpm(duration=40.0, bpm=120.0, bpm_diff_percent=15.0)
    assert result == max_4_bars


def test_clamp_duration_within_limit() -> None:
    """Duration already within limit should not be changed."""
    result = clamp_duration_by_bpm(duration=10.0, bpm=120.0, bpm_diff_percent=3.0)
    assert result == 10.0


def test_alignment_result_positions_in_source_time() -> None:
    """AlignmentResult positions should be in unstretched source-audio time."""
    # Use energy alignment with different BPMs — positions should NOT be compensated
    out_energy = np.ones(180, dtype=np.float32) * 0.9
    out_energy[155:] = np.linspace(0.9, 0.1, 25).astype(np.float32)

    in_energy = np.zeros(180, dtype=np.float32)
    in_energy[:10] = 0.1
    in_energy[10:25] = np.linspace(0.1, 0.9, 15).astype(np.float32)
    in_energy[25:] = 0.9

    # Different BPMs — if compensation were applied, positions would differ
    fade_out = _make_analysis(bpm=120.0, energy_curve=out_energy)
    fade_in = _make_analysis(bpm=126.0, energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    # Positions should be within the 45s buffer range (source time, not stretched)
    if result.fadeout_start_pos is not None:
        assert 0 <= result.fadeout_start_pos <= 45
    if result.fadein_start_pos is not None:
        assert 0 <= result.fadein_start_pos <= 45
