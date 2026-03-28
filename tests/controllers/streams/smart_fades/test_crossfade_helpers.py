"""Tests for shared crossfade helpers (BPM diff, downbeat extrapolation)."""

import numpy as np

from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    extrapolate_downbeats,
    get_bpm_diff_percentage,
)

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
