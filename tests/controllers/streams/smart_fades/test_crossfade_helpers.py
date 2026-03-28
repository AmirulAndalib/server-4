"""Tests for crossfade alignment helpers (energy, spectral, phrase snapping)."""

import numpy as np

from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    _snap_to_phrase_boundary,
    calculate_energy_crossfade_duration,
    compute_gradual_tempo_steps,
    extrapolate_downbeats,
    find_fadein_entry,
    find_fadeout_start,
    find_spectral_fadein_entry,
    find_spectral_fadeout_start,
    get_bpm_diff_percentage,
    select_crossfade_curve_type,
)


def test_find_fadeout_start_clear_decline() -> None:
    """Should find an early start point before the energy knee."""
    # 45 seconds: high energy for 20s, then decline
    energy = np.ones(45, dtype=np.float32) * 0.9
    energy[20:] = np.linspace(0.9, 0.1, 25).astype(np.float32)
    # Downbeats every 2s (120 BPM, 4/4)
    downbeats = np.arange(0, 45, 2.0)

    result = find_fadeout_start(energy, downbeats, bpm=120.0)

    # Knee is around sec 22-24. With 4 bars (8s at 120 BPM) early offset,
    # the fade start should be around sec 14-18.
    assert result is not None
    assert 10 <= result <= 22, f"Expected fade start around 10-22s, got {result}"


def test_find_fadeout_start_flat_energy() -> None:
    """Flat energy should fall back to default (None)."""
    energy = np.ones(45, dtype=np.float32) * 0.8
    downbeats = np.arange(0, 45, 2.0)

    result = find_fadeout_start(energy, downbeats)

    # No clear decline — should return None (fallback to current behavior)
    assert result is None


def test_find_fadein_entry_clear_build() -> None:
    """Should find the entry point in the quiet section before the rise."""
    # 45 seconds: quiet for 10s, then build to full energy
    energy = np.zeros(45, dtype=np.float32)
    energy[:10] = 0.1
    energy[10:25] = np.linspace(0.1, 0.9, 15).astype(np.float32)
    energy[25:] = 0.9
    downbeats = np.arange(0, 45, 2.0)

    result = find_fadein_entry(energy, downbeats)

    # Should enter in the quiet section well before the build (~sec 0-10)
    assert result is not None
    assert 0 <= result <= 12, f"Expected entry around 0-12s, got {result}"


def test_find_fadein_entry_already_loud() -> None:
    """Track that starts loud should return None (fallback)."""
    energy = np.ones(45, dtype=np.float32) * 0.9
    downbeats = np.arange(0, 45, 2.0)

    result = find_fadein_entry(energy, downbeats)

    assert result is None


def test_calculate_energy_crossfade_duration() -> None:
    """Duration should cover the energy handoff with musical minimum."""
    # Song A declining from 0.8
    energy_out = np.linspace(0.8, 0.1, 30).astype(np.float32)
    # Song B rising from 0.1 to 0.9
    energy_in = np.linspace(0.1, 0.9, 30).astype(np.float32)

    duration = calculate_energy_crossfade_duration(
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


def test_compute_gradual_tempo_steps_5_percent() -> None:
    """5% tempo change should produce S-curve steps with max 0.5% per step."""
    downbeats = np.arange(0, 20, 2.0)

    steps = compute_gradual_tempo_steps(
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


def test_select_crossfade_curve_type_similar() -> None:
    """Similar energy slopes should select equal-power."""
    out = np.linspace(0.8, 0.2, 10, dtype=np.float32)
    inc = np.linspace(0.2, 0.8, 10, dtype=np.float32)

    curve = select_crossfade_curve_type(out, inc)

    assert curve == "qsin"


def test_select_crossfade_curve_type_divergent() -> None:
    """Divergent slopes should select equal-gain."""
    out = np.linspace(0.8, 0.2, 10, dtype=np.float32)
    inc = np.ones(10, dtype=np.float32) * 0.5

    curve = select_crossfade_curve_type(out, inc)

    assert curve == "tri"


# ---------------------------------------------------------------------------
# Spectral fadeout tests
# ---------------------------------------------------------------------------


def test_find_spectral_fadeout_start_declining() -> None:
    """Declining spectral centroid should find a fade-out position."""
    # 45s: bright (3000 Hz) for 20s, then brightness drops
    spectral = np.ones(45, dtype=np.float32) * 3000.0
    spectral[20:] = np.linspace(3000.0, 500.0, 25).astype(np.float32)
    downbeats = np.arange(0, 45, 2.0)

    result = find_spectral_fadeout_start(spectral, downbeats, bpm=120.0)

    assert result is not None
    assert 8 <= result <= 22, f"Expected fade start around 8-22s, got {result}"


def test_find_spectral_fadeout_start_flat() -> None:
    """Flat spectral centroid should return None."""
    spectral = np.ones(45, dtype=np.float32) * 2000.0
    downbeats = np.arange(0, 45, 2.0)

    result = find_spectral_fadeout_start(spectral, downbeats)

    assert result is None


def test_find_spectral_fadeout_start_near_silence() -> None:
    """Very narrow spectral range should return None."""
    spectral = np.ones(45, dtype=np.float32) * 100.0
    spectral += np.random.default_rng(42).uniform(-0.3, 0.3, 45).astype(np.float32)
    downbeats = np.arange(0, 45, 2.0)

    result = find_spectral_fadeout_start(spectral, downbeats)

    assert result is None


def test_find_spectral_fadeout_start_noisy_with_trend() -> None:
    """Noisy spectral curve with clear downward trend should still detect decline."""
    rng = np.random.default_rng(42)
    base = np.ones(45, dtype=np.float32) * 3000.0
    base[20:] = np.linspace(3000.0, 500.0, 25).astype(np.float32)
    noise = rng.uniform(-200, 200, 45).astype(np.float32)
    spectral = base + noise
    downbeats = np.arange(0, 45, 2.0)

    result = find_spectral_fadeout_start(spectral, downbeats, bpm=120.0)

    assert result is not None
    assert 8 <= result <= 24, f"Expected fade start around 8-24s, got {result}"


# ---------------------------------------------------------------------------
# Spectral fadein tests
# ---------------------------------------------------------------------------


def test_find_spectral_fadein_entry_rising() -> None:
    """Rising spectral centroid should find entry before the rise."""
    spectral = np.ones(45, dtype=np.float32) * 500.0
    spectral[:10] = 500.0
    spectral[10:25] = np.linspace(500.0, 3000.0, 15).astype(np.float32)
    spectral[25:] = 3000.0
    downbeats = np.arange(0, 45, 2.0)

    result = find_spectral_fadein_entry(spectral, downbeats)

    assert result is not None
    assert 0 <= result <= 12, f"Expected entry around 0-12s, got {result}"


def test_find_spectral_fadein_entry_flat() -> None:
    """Flat spectral centroid should return None."""
    spectral = np.ones(45, dtype=np.float32) * 2000.0
    downbeats = np.arange(0, 45, 2.0)

    result = find_spectral_fadein_entry(spectral, downbeats)

    assert result is None


def test_find_spectral_fadein_entry_already_bright() -> None:
    """Track that starts bright should return None."""
    spectral = np.ones(45, dtype=np.float32) * 3000.0
    downbeats = np.arange(0, 45, 2.0)

    result = find_spectral_fadein_entry(spectral, downbeats)

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
# get_bpm_diff_percentage tests
# ---------------------------------------------------------------------------


def test_get_bpm_diff_percentage_same_bpm() -> None:
    """Same BPM should give 0% diff."""
    assert get_bpm_diff_percentage(120.0, 120.0) == 0.0


def test_get_bpm_diff_percentage_5_percent() -> None:
    """5% BPM difference should return ~5."""
    result = get_bpm_diff_percentage(120.0, 126.0)
    assert 4.5 <= result <= 5.1


# ---------------------------------------------------------------------------
# extrapolate_downbeats tests
# ---------------------------------------------------------------------------


def test_extrapolate_downbeats_no_extrapolation_needed() -> None:
    """Downbeats near buffer end should not be extrapolated."""
    downbeats = np.arange(0, 44, 2.0)  # Last at 42s, close to 45s buffer
    result = extrapolate_downbeats(downbeats, tempo_factor=1.0)
    np.testing.assert_array_equal(result, downbeats)


def test_extrapolate_downbeats_extends_forward() -> None:
    """Sparse downbeats should be extrapolated forward."""
    downbeats = np.array([0.0, 2.0, 4.0, 6.0, 8.0])
    result = extrapolate_downbeats(downbeats, tempo_factor=1.0)
    assert len(result) > len(downbeats)
    assert result[-1] <= 45.0


def test_extrapolate_downbeats_with_tempo_factor() -> None:
    """Tempo factor should scale downbeat positions."""
    downbeats = np.array([0.0, 2.0, 4.0, 6.0, 8.0])
    result = extrapolate_downbeats(downbeats, tempo_factor=0.5)
    # With tempo_factor=0.5, positions are doubled (audio slowed down)
    assert result[0] == 0.0
    assert result[1] == 4.0  # 2.0 / 0.5
