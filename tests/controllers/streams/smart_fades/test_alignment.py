"""Tests for crossfade alignment resolution (three energy contexts)."""

import numpy as np

from music_assistant.controllers.streams.smart_fades.alignment import (
    AlignmentResult,
    _characterize_incoming,
    _clamp_duration_by_bpm,
    _find_knee,
    _snap_to_phrase_boundary,
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


# ---------------------------------------------------------------------------
# resolve_alignment integration tests
# ---------------------------------------------------------------------------


def test_resolve_alignment_energy_path() -> None:
    """Energy curves with clear decline/rise should use energy strategy."""
    # Song A: high energy for 155s, then 25s decline in last 45s of song
    out_energy = np.ones(180, dtype=np.float32) * 0.9
    out_energy[155:] = np.linspace(0.9, 0.1, 25).astype(np.float32)

    # Song B: quiet intro for 15s, then gradual 25s build
    in_energy = np.zeros(180, dtype=np.float32)
    in_energy[:15] = 0.05
    in_energy[15:40] = np.linspace(0.05, 0.8, 25).astype(np.float32)
    in_energy[40:] = 0.8

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert isinstance(result, AlignmentResult)
    assert result.strategy == "energy"
    assert result.fadeout_start_pos is not None
    assert result.fadein_start_pos is not None
    assert result.crossfade_duration > 0


def test_resolve_alignment_spectral_fallback() -> None:
    """Flat energy but declining spectral should use energy strategy via spectral knee."""
    # Flat energy (no clear decline)
    flat_energy = np.ones(180, dtype=np.float32) * 0.8

    # Song A: bright for 155s, then brightness drops
    out_spectral = np.ones(180, dtype=np.float32) * 3000.0
    out_spectral[155:] = np.linspace(3000.0, 500.0, 25).astype(np.float32)

    # Song B: dim for 15s, then brightness rises gradually over 25s
    in_spectral = np.ones(180, dtype=np.float32) * 500.0
    in_spectral[:15] = 500.0
    in_spectral[15:40] = np.linspace(500.0, 3000.0, 25).astype(np.float32)
    in_spectral[40:] = 3000.0

    fade_out = _make_analysis(energy_curve=flat_energy, spectral_centroid_curve=out_spectral)
    fade_in = _make_analysis(energy_curve=flat_energy, spectral_centroid_curve=in_spectral)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "energy"


def test_resolve_alignment_bar_count_fallback() -> None:
    """No energy or spectral curves should fall back to bar_count."""
    fade_out = _make_analysis()  # No energy/spectral
    fade_in = _make_analysis()

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "bar_count"
    assert result.crossfade_duration > 0


def test_resolve_alignment_context_a_knee_found() -> None:
    """Outgoing knee + incoming quiet intro = energy strategy, moderate duration."""
    out_energy = np.ones(180, dtype=np.float32) * 0.9
    out_energy[155:] = np.linspace(0.9, 0.1, 25).astype(np.float32)
    in_energy = np.zeros(180, dtype=np.float32)
    in_energy[:15] = 0.05
    in_energy[15:40] = np.linspace(0.05, 0.8, 25).astype(np.float32)
    in_energy[40:] = 0.8

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "energy"
    assert result.fadeout_start_pos is not None
    assert result.crossfade_duration > 0


def test_resolve_alignment_context_a_knee_loud_incoming() -> None:
    """Outgoing knee + loud incoming = energy strategy, short duration."""
    out_energy = np.ones(180, dtype=np.float32) * 0.9
    out_energy[155:] = np.linspace(0.9, 0.1, 25).astype(np.float32)
    in_energy = np.ones(180, dtype=np.float32) * 0.8

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "energy"
    assert result.fadeout_start_pos is not None
    # Loud incoming caps duration at 4 bars (8s at 120 BPM)
    assert result.crossfade_duration <= 10


def test_resolve_alignment_context_b_both_quiet() -> None:
    """Both quiet tracks = long key-driven duration."""
    out_energy = np.ones(180, dtype=np.float32) * 0.10
    in_energy = np.ones(180, dtype=np.float32) * 0.08

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "quiet"
    assert result.crossfade_duration >= 20  # will be capped by resolver's max


def test_resolve_alignment_context_c_no_knee_loud() -> None:
    """No knee, both loud = energy_ratio strategy."""
    out_energy = np.ones(180, dtype=np.float32) * 0.7
    in_energy = np.ones(180, dtype=np.float32) * 0.9

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "energy_ratio"


def test_resolve_alignment_no_data_fallback() -> None:
    """No energy or spectral data = bar_count fallback."""
    fade_out = _make_analysis()
    fade_in = _make_analysis()

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "bar_count"


# ---------------------------------------------------------------------------
# BPM clamping tests
# ---------------------------------------------------------------------------


def test_clamp_duration_by_bpm_small_diff() -> None:
    """Small BPM diff should allow up to 16 bars."""
    bar_duration = 4 * (60.0 / 120.0)  # 2s per bar at 120 BPM
    max_16_bars = 16 * bar_duration  # 32s

    result = _clamp_duration_by_bpm(duration=40.0, bpm=120.0, bpm_diff_percent=3.0)
    assert result == max_16_bars


def test_clamp_duration_by_bpm_large_diff() -> None:
    """Large BPM diff should clamp to 4 bars."""
    bar_duration = 4 * (60.0 / 120.0)
    max_4_bars = 4 * bar_duration  # 8s

    result = _clamp_duration_by_bpm(duration=40.0, bpm=120.0, bpm_diff_percent=15.0)
    assert result == max_4_bars


def test_clamp_duration_within_limit() -> None:
    """Duration already within limit should not be changed."""
    result = _clamp_duration_by_bpm(duration=10.0, bpm=120.0, bpm_diff_percent=3.0)
    assert result == 10.0


# ---------------------------------------------------------------------------
# Position / source-time test
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# _find_knee tests
# ---------------------------------------------------------------------------


def test_find_fadeout_start_clear_decline() -> None:
    """Should find an early start point before the energy knee."""
    # 45 seconds: high energy for 20s, then decline
    energy = np.ones(45, dtype=np.float32) * 0.9
    energy[20:] = np.linspace(0.9, 0.1, 25).astype(np.float32)
    # Downbeats every 2s (120 BPM, 4/4)
    downbeats = np.arange(0, 45, 2.0)

    result = _find_knee(energy, downbeats, bpm=120.0)

    # Knee is around sec 22-24. With 4 bars (8s at 120 BPM) early offset,
    # the fade start should be around sec 14-18.
    assert result is not None
    start_pos, _knee_idx = result
    assert 10 <= start_pos <= 22, f"Expected fade start around 10-22s, got {start_pos}"


def test_find_fadeout_start_flat_energy() -> None:
    """Flat energy should fall back to default (None)."""
    energy = np.ones(45, dtype=np.float32) * 0.8
    downbeats = np.arange(0, 45, 2.0)

    result = _find_knee(energy, downbeats)

    # No clear decline — should return None (fallback to current behavior)
    assert result is None


def test_find_knee_clear_decline() -> None:
    """Clear energy decline should return knee position."""
    energy = np.ones(45, dtype=np.float32) * 0.9
    energy[35:] = np.linspace(0.9, 0.1, 10).astype(np.float32)
    downbeats = np.arange(0, 45, 2.0)

    result = _find_knee(energy, downbeats, bpm=120.0)

    assert result is not None
    start_pos, knee_idx = result
    assert 30 <= knee_idx <= 40
    assert start_pos <= knee_idx


def test_find_knee_flat_energy_returns_none() -> None:
    """Flat energy should return None (no knee)."""
    energy = np.ones(45, dtype=np.float32) * 0.5
    downbeats = np.arange(0, 45, 2.0)

    result = _find_knee(energy, downbeats, bpm=120.0)

    assert result is None


def test_find_knee_near_silence_returns_none() -> None:
    """Near-silent track should return None."""
    energy = np.ones(45, dtype=np.float32) * 0.03
    downbeats = np.arange(0, 45, 2.0)

    result = _find_knee(energy, downbeats, bpm=120.0)

    assert result is None


# ---------------------------------------------------------------------------
# Phrase snapping tests
# ---------------------------------------------------------------------------


def test_snap_to_phrase_boundary_8bar() -> None:
    """Should snap to an 8-bar phrase boundary when close enough."""
    downbeats = np.arange(0, 64, 2.0)  # 32 downbeats at 2s intervals

    # Target 17.0s: nearest 8-bar boundary backward is 16.0s (1s away, well within 4 bars)
    result = _snap_to_phrase_boundary(17.0, downbeats, phrase_len=8, direction="backward")

    assert result is not None
    assert result == 16.0, f"Expected 8-bar boundary at 16.0s, got {result}"


def test_snap_to_phrase_boundary_fallback_to_4bar() -> None:
    """Should fall back to 4-bar snap when 8-bar is too far."""
    downbeats = np.arange(0, 64, 2.0)  # 32 downbeats

    # Target at 9.0s: nearest 8-bar boundary is 0s (9s away = 4.5 bars at 2s/bar)
    # That's > 4 bars, so should fall back to 4-bar boundaries (0, 8, 16, 24...)
    result = _snap_to_phrase_boundary(9.0, downbeats, phrase_len=8, direction="backward")

    assert result is not None
    assert result == 8.0, f"Expected 4-bar boundary at 8.0s, got {result}"


def test_snap_to_phrase_boundary_few_downbeats() -> None:
    """With fewer downbeats than phrase_len, should fall back to plain snap."""
    downbeats = np.array([2.0, 4.0, 6.0])  # Only 3 downbeats

    result = _snap_to_phrase_boundary(5.0, downbeats, phrase_len=8, direction="backward")

    # Falls back to _snap_to_downbeat since < 8 downbeats
    assert result is not None
    assert result == 4.0, f"Expected downbeat snap at 4.0s, got {result}"


# ---------------------------------------------------------------------------
# _characterize_incoming tests
# ---------------------------------------------------------------------------


def test_characterize_incoming_quiet_intro() -> None:
    """Track with quiet intro should be detected."""
    energy = np.zeros(45, dtype=np.float32)
    energy[:15] = 0.05
    energy[15:] = 0.8

    result = _characterize_incoming(energy)

    assert result["has_quiet_intro"] is True
    assert result["entry_energy"] < 0.15


def test_characterize_incoming_loud_start() -> None:
    """Track starting loud should not be detected as quiet intro."""
    energy = np.zeros(45, dtype=np.float32)
    energy[:5] = np.linspace(0.27, 0.91, 5).astype(np.float32)
    energy[5:] = 0.91

    result = _characterize_incoming(energy)

    assert result["has_quiet_intro"] is False
    assert result["entry_energy"] > 0.15


def test_characterize_incoming_none_curve() -> None:
    """No energy curve returns neutral characterization."""
    result = _characterize_incoming(None)

    assert result["has_quiet_intro"] is False
    assert result["entry_energy"] == 0.5
