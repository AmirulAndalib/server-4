"""Tests for smart fades analysis helper functions."""

import numpy as np
import pytest

from music_assistant.providers.smart_fades.analysis_helpers import (
    compute_rms_per_second,
    compute_stft_features,
    detect_key,
    detect_phrase_boundaries,
)


def test_compute_rms_per_second_sine_wave() -> None:
    """RMS of a sine wave at known amplitude should be amplitude / sqrt(2)."""
    sr = 22050
    duration = 5  # seconds
    amplitude = 0.5
    t = np.linspace(0, duration, sr * duration, endpoint=False, dtype=np.float32)
    sine = amplitude * np.sin(2 * np.pi * 440 * t)

    rms = compute_rms_per_second(sine, sr)

    assert len(rms) == duration
    expected_rms = amplitude / np.sqrt(2)
    for val in rms:
        assert abs(val - expected_rms) < 0.01, f"Expected ~{expected_rms:.3f}, got {val:.3f}"


def test_compute_rms_per_second_silence() -> None:
    """RMS of silence should be zero."""
    sr = 22050
    silence = np.zeros(sr * 3, dtype=np.float32)

    rms = compute_rms_per_second(silence, sr)

    assert len(rms) == 3
    for val in rms:
        assert val == 0.0


def test_compute_rms_per_second_partial_second() -> None:
    """Samples less than 1 second should return empty array."""
    sr = 22050
    short = np.ones(sr // 2, dtype=np.float32)

    rms = compute_rms_per_second(short, sr)

    assert len(rms) == 0


def test_compute_stft_features_sine_wave() -> None:
    """Spectral centroid of a 440 Hz sine should be close to 440 Hz."""
    sr = 22050
    duration = 5
    t = np.linspace(0, duration, sr * duration, endpoint=False, dtype=np.float32)
    sine = np.sin(2 * np.pi * 440 * t)

    centroid_per_sec, chroma_per_sec = compute_stft_features(sine, sr)

    assert len(centroid_per_sec) == duration
    # Spectral centroid of a pure 440 Hz tone should be ~440 Hz
    for val in centroid_per_sec:
        assert 400 < val < 480, f"Expected centroid ~440 Hz, got {val:.1f}"

    # Chroma should have 12 bins per second
    assert chroma_per_sec.shape == (duration, 12)
    # 440 Hz = A4, which is chroma bin 9 (A). Should be dominant.
    mean_chroma = chroma_per_sec.mean(axis=0)
    assert np.argmax(mean_chroma) == 9, f"Expected A (bin 9) dominant, got bin {np.argmax(mean_chroma)}"


def test_compute_stft_features_empty() -> None:
    """Empty audio should return empty arrays."""
    sr = 22050
    empty = np.array([], dtype=np.float32)
    centroid, chroma = compute_stft_features(empty, sr)
    assert len(centroid) == 0
    assert chroma.shape[1] == 12


def test_detect_key_c_major() -> None:
    """Chroma weighted toward C, E, G should detect C major."""
    chroma = np.zeros((20, 12), dtype=np.float32)
    chroma[:, 0] = 1.0  # C
    chroma[:, 4] = 0.8  # E
    chroma[:, 7] = 0.6  # G

    key = detect_key(chroma, duration=20.0)

    assert key["root"] == "C"
    assert key["mode"] == "major"
    assert key["confidence"] > 0.5


def test_detect_key_a_minor() -> None:
    """Chroma weighted toward A, C, E should detect A minor."""
    chroma = np.zeros((20, 12), dtype=np.float32)
    chroma[:, 9] = 1.0  # A
    chroma[:, 0] = 0.8  # C
    chroma[:, 4] = 0.6  # E

    key = detect_key(chroma, duration=20.0)

    assert key["root"] == "A"
    assert key["mode"] == "minor"
    assert key["confidence"] > 0.5


def test_detect_key_filters_intro_outro() -> None:
    """First and last 10s should be excluded from key detection."""
    chroma = np.zeros((30, 12), dtype=np.float32)
    # First/last 10s: F major (F=5, A=9, C=0)
    chroma[:10, 5] = 1.0
    chroma[:10, 9] = 0.8
    chroma[:10, 0] = 0.6
    chroma[20:, 5] = 1.0
    chroma[20:, 9] = 0.8
    chroma[20:, 0] = 0.6
    # Middle 10s: C major
    chroma[10:20, 0] = 1.0
    chroma[10:20, 4] = 0.8
    chroma[10:20, 7] = 0.6

    key = detect_key(chroma, duration=30.0)

    assert key["root"] == "C"
    assert key["mode"] == "major"


def test_detect_phrase_boundaries_energy_drop() -> None:
    """Should detect a phrase boundary at a downbeat with energy drop."""
    bpm = 120.0
    bar_duration = 4 * (60.0 / bpm)  # 2.0 seconds per bar
    downbeats = np.arange(32) * bar_duration

    duration_sec = int(32 * bar_duration)
    energy = np.ones(duration_sec, dtype=np.float32) * 0.8
    energy[32:] = 0.2  # Drop at bar 16 (second 32)
    centroid = np.ones(duration_sec, dtype=np.float32) * 1000.0

    boundaries = detect_phrase_boundaries(downbeats, energy, centroid, bpm)

    boundary_times = [b["time"] for b in boundaries]
    assert any(abs(t - 32.0) < 2.0 for t in boundary_times), (
        f"Expected boundary near 32.0s, got {boundary_times}"
    )


def test_detect_phrase_boundaries_spectral_change() -> None:
    """Should detect a boundary from spectral centroid change even without energy change."""
    bpm = 120.0
    bar_duration = 4 * (60.0 / bpm)
    downbeats = np.arange(32) * bar_duration

    duration_sec = int(32 * bar_duration)
    energy = np.ones(duration_sec, dtype=np.float32) * 0.5
    centroid = np.ones(duration_sec, dtype=np.float32) * 500.0
    centroid[32:] = 2000.0  # Big spectral jump at bar 16

    boundaries = detect_phrase_boundaries(downbeats, energy, centroid, bpm)

    boundary_times = [b["time"] for b in boundaries]
    assert any(abs(t - 32.0) < 2.0 for t in boundary_times), (
        f"Expected boundary near 32.0s from spectral change, got {boundary_times}"
    )


def test_detect_phrase_boundaries_too_few_downbeats() -> None:
    """Should return empty list with fewer than 4 downbeats."""
    downbeats = np.array([0.0, 2.0, 4.0])
    energy = np.ones(10, dtype=np.float32)
    centroid = np.ones(10, dtype=np.float32)

    boundaries = detect_phrase_boundaries(downbeats, energy, centroid, 120.0)

    assert boundaries == []


def test_detect_phrase_boundaries_phase_offset() -> None:
    """Boundaries must be detected even when drop is at a non-modulo-aligned downbeat.

    This test proves the anchor-based approach works: an energy drop at downbeat
    index 14 (14 % 4 == 2, NOT a multiple of 4) would be completely invisible
    with a naive i%4==0 gate, but the anchor approach catches it because it
    scores all downbeats and uses the grid as a bonus, not a gate.
    """
    bpm = 120.0
    bar_duration = 4 * (60.0 / bpm)  # 2.0s per bar
    downbeats = np.arange(34) * bar_duration

    duration_sec = int(34 * bar_duration)
    energy = np.ones(duration_sec, dtype=np.float32) * 0.8
    drop_time = downbeats[14]
    drop_sec = int(drop_time)
    energy[drop_sec:] = 0.2
    centroid = np.ones(duration_sec, dtype=np.float32) * 1000.0

    boundaries = detect_phrase_boundaries(downbeats, energy, centroid, bpm)

    boundary_times = [b["time"] for b in boundaries]
    assert any(abs(t - drop_time) < 2.0 for t in boundary_times), (
        f"Expected boundary near {drop_time}s (downbeat 14), got {boundary_times}"
    )
