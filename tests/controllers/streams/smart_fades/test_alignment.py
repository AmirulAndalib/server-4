"""Tests for crossfade alignment resolution (energy, spectral, bar-count cascade)."""

import numpy as np

from music_assistant.controllers.streams.smart_fades.alignment import (
    AlignmentResult,
    _calculate_energy_crossfade_duration,
    _clamp_duration_by_bpm,
    _find_fadein_entry,
    _find_knee,
    _find_spectral_fadein_entry,
    _find_spectral_fadeout_start,
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
    """Flat energy but declining spectral should use spectral strategy."""
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
# Signal-based alignment helper tests (moved from test_crossfade_helpers.py)
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


def test_find_fadein_entry_clear_build() -> None:
    """Should find the entry point in the quiet section before the rise."""
    # 45 seconds: quiet intro for 15s, then gradual 25s build
    energy = np.zeros(45, dtype=np.float32)
    energy[:15] = 0.05
    energy[15:40] = np.linspace(0.05, 0.8, 25).astype(np.float32)
    energy[40:] = 0.8
    downbeats = np.arange(0, 45, 2.0)

    result = _find_fadein_entry(energy, downbeats)

    # Should enter in the quiet section before the build
    assert result is not None
    assert 0 <= result <= 16, f"Expected entry around 0-16s, got {result}"


def test_find_fadein_entry_already_loud() -> None:
    """Track that starts loud should return None (fallback)."""
    energy = np.ones(45, dtype=np.float32) * 0.9
    downbeats = np.arange(0, 45, 2.0)

    result = _find_fadein_entry(energy, downbeats)

    assert result is None


def test_find_fadein_entry_edm_predrop_rejected() -> None:
    """EDM pre-drop section (high starting energy, steep rise) should return None."""
    energy = np.zeros(45, dtype=np.float32)
    energy[:4] = np.linspace(0.27, 0.91, 4).astype(np.float32)
    energy[4:] = 0.91
    downbeats = np.arange(0, 45, 2.0)

    result = _find_fadein_entry(energy, downbeats)

    assert result is None


def test_find_fadein_entry_flat_quiet_no_rise() -> None:
    """Uniformly quiet track (no rise after quiet region) should return None."""
    energy = np.ones(45, dtype=np.float32) * 0.06
    downbeats = np.arange(0, 45, 2.0)

    result = _find_fadein_entry(energy, downbeats)

    assert result is None


def test_find_fadein_entry_prefers_earliest_region() -> None:
    """With intro and breakdown, should pick the intro (earliest)."""
    energy = np.zeros(45, dtype=np.float32)
    energy[:10] = 0.05
    energy[10:25] = 0.80
    energy[25:35] = 0.05
    energy[35:] = 0.80
    downbeats = np.arange(0, 45, 2.0)

    result = _find_fadein_entry(energy, downbeats)

    assert result is not None
    assert result <= 10, f"Expected entry in intro (sec 0-10), got {result}"


def test_find_fadein_entry_short_dip_rejected() -> None:
    """A 3-second energy dip should not qualify as an entry region."""
    energy = np.ones(45, dtype=np.float32) * 0.70
    energy[20:23] = 0.05
    downbeats = np.arange(0, 45, 2.0)

    result = _find_fadein_entry(energy, downbeats)

    assert result is None


def test_find_fadein_entry_too_short_input() -> None:
    """Input shorter than MIN_QUIET_SUSTAIN + DROP_CHECK_WINDOW should return None."""
    energy = np.array([0.05] * 10, dtype=np.float32)
    downbeats = np.arange(0, 10, 2.0)

    result = _find_fadein_entry(energy, downbeats)

    assert result is None


def test_calculate_energy_crossfade_duration() -> None:
    """Duration should cover the energy handoff with musical minimum."""
    # Song A declining from 0.8
    energy_out = np.linspace(0.8, 0.1, 30).astype(np.float32)
    # Song B rising from 0.1 to 0.9
    energy_in = np.linspace(0.1, 0.9, 30).astype(np.float32)

    duration = _calculate_energy_crossfade_duration(
        energy_out=energy_out,
        fadeout_start=0,
        energy_in=energy_in,
        fadein_entry=0,
        bpm=120.0,
    )

    # At 120 BPM, 8 bars = 16s (musical minimum).
    # Song B reaches 0.8 at ~index 26, plus 4 bars (8s) post-match = 34s.
    # So duration should be at least 16s (8-bar minimum) and up to ~34s.
    bar_duration = 4 * (60.0 / 120.0)
    min_musical = 8 * bar_duration  # 16s
    assert duration >= min_musical, f"Expected duration >= {min_musical}s, got {duration}"
    assert duration <= 40, f"Expected duration <= 40s, got {duration}"


# ---------------------------------------------------------------------------
# Spectral fadeout tests
# ---------------------------------------------------------------------------


def test_find_spectral_fadeout_start_declining() -> None:
    """Declining spectral centroid should find a fade-out position."""
    # 45s: bright (3000 Hz) for 20s, then brightness drops
    spectral = np.ones(45, dtype=np.float32) * 3000.0
    spectral[20:] = np.linspace(3000.0, 500.0, 25).astype(np.float32)
    downbeats = np.arange(0, 45, 2.0)

    result = _find_spectral_fadeout_start(spectral, downbeats, bpm=120.0)

    assert result is not None
    assert 8 <= result <= 22, f"Expected fade start around 8-22s, got {result}"


def test_find_spectral_fadeout_start_flat() -> None:
    """Flat spectral centroid should return None."""
    spectral = np.ones(45, dtype=np.float32) * 2000.0
    downbeats = np.arange(0, 45, 2.0)

    result = _find_spectral_fadeout_start(spectral, downbeats)

    assert result is None


def test_find_spectral_fadeout_start_near_silence() -> None:
    """Very narrow spectral range should return None."""
    spectral = np.ones(45, dtype=np.float32) * 100.0
    spectral += np.random.default_rng(42).uniform(-0.3, 0.3, 45).astype(np.float32)
    downbeats = np.arange(0, 45, 2.0)

    result = _find_spectral_fadeout_start(spectral, downbeats)

    assert result is None


def test_find_spectral_fadeout_start_noisy_with_trend() -> None:
    """Noisy spectral curve with clear downward trend should still detect decline."""
    rng = np.random.default_rng(42)
    base = np.ones(45, dtype=np.float32) * 3000.0
    base[20:] = np.linspace(3000.0, 500.0, 25).astype(np.float32)
    noise = rng.uniform(-200, 200, 45).astype(np.float32)
    spectral = base + noise
    downbeats = np.arange(0, 45, 2.0)

    result = _find_spectral_fadeout_start(spectral, downbeats, bpm=120.0)

    assert result is not None
    assert 8 <= result <= 24, f"Expected fade start around 8-24s, got {result}"


# ---------------------------------------------------------------------------
# Spectral fadein tests
# ---------------------------------------------------------------------------


def test_find_spectral_fadein_entry_rising() -> None:
    """Rising spectral centroid should find entry before the rise."""
    # 15s dim, then gradual 25s brightness build
    spectral = np.ones(45, dtype=np.float32) * 500.0
    spectral[:15] = 500.0
    spectral[15:40] = np.linspace(500.0, 3000.0, 25).astype(np.float32)
    spectral[40:] = 3000.0
    downbeats = np.arange(0, 45, 2.0)

    result = _find_spectral_fadein_entry(spectral, downbeats)

    assert result is not None
    assert 0 <= result <= 16, f"Expected entry around 0-16s, got {result}"


def test_find_spectral_fadein_entry_flat() -> None:
    """Flat spectral centroid should return None."""
    spectral = np.ones(45, dtype=np.float32) * 2000.0
    downbeats = np.arange(0, 45, 2.0)

    result = _find_spectral_fadein_entry(spectral, downbeats)

    assert result is None


def test_find_spectral_fadein_entry_already_bright() -> None:
    """Track that starts bright should return None."""
    spectral = np.ones(45, dtype=np.float32) * 3000.0
    downbeats = np.arange(0, 45, 2.0)

    result = _find_spectral_fadein_entry(spectral, downbeats)

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
