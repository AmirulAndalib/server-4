"""Tests for resolve_crossfade_params — unified crossfade decision pipeline."""

import numpy as np

from music_assistant.controllers.streams.smart_fades.crossfade_params import (
    _compute_spectral_overlap,
    _extract_key,
    resolve_crossfade_params,
    snap_to_musical_bars,
)
from music_assistant.controllers.streams.smart_fades.models import (
    TimeStretchDecision,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


def _make_analysis(
    bpm: float = 120.0,
    duration: float = 180.0,
    musical_key: dict | None = None,
    energy_curve: np.ndarray | None = None,
    spectral_centroid_curve: np.ndarray | None = None,
) -> AudioAnalysisData:
    """Create AudioAnalysisData with sensible defaults."""
    beats = np.arange(0, duration, 60.0 / bpm)
    downbeats = beats[::4]
    return AudioAnalysisData(
        bpm=bpm,
        beats=beats,
        downbeats=downbeats,
        duration=duration,
        musical_key=musical_key,
        energy_curve=energy_curve,
        spectral_centroid_curve=spectral_centroid_curve,
    )


def _make_stretch(bpm_diff_percent: float = 8.0) -> TimeStretchDecision:
    """Create a TimeStretchDecision for testing."""
    return TimeStretchDecision(
        apply=bpm_diff_percent <= 6.0,
        bpm_ratio=1.0 + bpm_diff_percent / 100.0,
        bpm_diff_percent=bpm_diff_percent,
        tempo_steps=None,
    )


# ── snap_to_musical_bars ─────────────────────────────────────────────


def test_snap_below_1_5_returns_1() -> None:
    """Values at or below 1.5 snap to 1."""
    assert snap_to_musical_bars(0.5) == 1
    assert snap_to_musical_bars(1.0) == 1
    assert snap_to_musical_bars(1.5) == 1


def test_snap_between_1_5_and_3_returns_2() -> None:
    """Values between 1.5 and 3.0 snap to 2."""
    assert snap_to_musical_bars(1.6) == 2
    assert snap_to_musical_bars(2.5) == 2
    assert snap_to_musical_bars(3.0) == 2


def test_snap_between_3_and_6_returns_4() -> None:
    """Values between 3.0 and 6.0 snap to 4."""
    assert snap_to_musical_bars(3.1) == 4
    assert snap_to_musical_bars(5.0) == 4
    assert snap_to_musical_bars(6.0) == 4


def test_snap_between_6_and_12_returns_8() -> None:
    """Values between 6.0 and 12.0 snap to 8."""
    assert snap_to_musical_bars(6.1) == 8
    assert snap_to_musical_bars(10.0) == 8
    assert snap_to_musical_bars(12.0) == 8


def test_snap_above_12_returns_16() -> None:
    """Values above 12.0 snap to 16."""
    assert snap_to_musical_bars(12.1) == 16
    assert snap_to_musical_bars(20.0) == 16


# ── _compute_spectral_overlap ────────────────────────────────────────


def test_spectral_overlap_identical_returns_1() -> None:
    """Identical centroids return overlap 1.0."""
    assert _compute_spectral_overlap(2000.0, 2000.0) == 1.0


def test_spectral_overlap_one_octave_returns_0() -> None:
    """One octave apart (1000 vs 2000 Hz) returns 0.0."""
    result = _compute_spectral_overlap(1000.0, 2000.0)
    assert abs(result) < 0.01


def test_spectral_overlap_two_octaves_returns_0() -> None:
    """Two or more octaves apart returns 0.0."""
    assert _compute_spectral_overlap(500.0, 2000.0) == 0.0


def test_spectral_overlap_similar_centroids_is_high() -> None:
    """Similar centroids return high overlap."""
    result = _compute_spectral_overlap(1800.0, 2200.0)
    assert result > 0.5


# ── _extract_key ─────────────────────────────────────────────────────


def test_extract_key_valid_dict() -> None:
    """Valid dict returns MusicalKey."""
    raw = {"root": "D#", "mode": "major", "confidence": 0.8}
    key = _extract_key(raw)
    assert key is not None
    assert key.root == "D#"
    assert key.mode == "major"
    assert key.confidence == 0.8


def test_extract_key_none_returns_none() -> None:
    """None input returns None."""
    assert _extract_key(None) is None


def test_extract_key_missing_fields_returns_none() -> None:
    """Dict with missing fields returns None."""
    assert _extract_key({"root": "C"}) is None


# ── Path A ───────────────────────────────────────────────────────────


def test_path_a_uses_exponential_curve() -> None:
    """Path A always uses exponential curve without bar alignment."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=130.0)
    stretch = _make_stretch(bpm_diff_percent=8.3)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        stretch=stretch,
    )
    assert result.curve_type == "exponential"
    assert result.use_bar_alignment is False


def test_path_a_fade_between_1_5_and_2_5_seconds() -> None:
    """Path A fade length is within the configured range."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=130.0)
    stretch = _make_stretch(bpm_diff_percent=8.3)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        stretch=stretch,
    )
    assert 1.5 <= result.fade_seconds <= 2.5


def test_path_a_crossover_between_2200_and_3000() -> None:
    """Path A crossover is within the configured range."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=130.0)
    stretch = _make_stretch(bpm_diff_percent=8.3)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        stretch=stretch,
    )
    assert 2200 <= result.crossover_freq <= 3000


def test_path_a_incompatible_key_higher_crossover() -> None:
    """Path A: incompatible key pushes crossover higher than compatible."""
    fade_out = _make_analysis(
        bpm=120.0, musical_key={"root": "C", "mode": "major", "confidence": 0.9}
    )
    fade_in_bad = _make_analysis(
        bpm=130.0, musical_key={"root": "F#", "mode": "major", "confidence": 0.9}
    )
    fade_in_good = _make_analysis(
        bpm=130.0, musical_key={"root": "G", "mode": "major", "confidence": 0.9}
    )
    stretch = _make_stretch(bpm_diff_percent=8.3)

    result_bad = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in_bad, stretch=stretch
    )
    result_good = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in_good, stretch=stretch
    )
    assert result_bad.crossover_freq >= result_good.crossover_freq


# ── Path B: crossover ────────────────────────────────────────────────


def test_path_b_compatible_key_low_crossover() -> None:
    """Path B: compatible key with spectral data drives crossover low."""
    fade_out = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        spectral_centroid_curve=np.full(180, 2000.0, dtype=np.float32),
    )
    fade_in = _make_analysis(
        musical_key={"root": "G", "mode": "major", "confidence": 0.9},
        spectral_centroid_curve=np.full(180, 2200.0, dtype=np.float32),
    )
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.crossover_freq < 1500


def test_path_b_incompatible_key_high_crossover() -> None:
    """Path B: incompatible key pushes crossover high regardless of spectral."""
    fade_out = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        spectral_centroid_curve=np.full(180, 2000.0, dtype=np.float32),
    )
    fade_in = _make_analysis(
        musical_key={"root": "F#", "mode": "major", "confidence": 0.9},
        spectral_centroid_curve=np.full(180, 2000.0, dtype=np.float32),
    )
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.crossover_freq > 2000


# ── Path B: fade length ──────────────────────────────────────────────


def test_path_b_compatible_key_long_fade() -> None:
    """Path B: compatible key allows 8+ bar fade."""
    fade_out = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
    )
    fade_in = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
    )
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.fade_bars >= 8


def test_path_b_incompatible_key_short_fade() -> None:
    """Path B: incompatible key limits fade to 4 bars or less."""
    fade_out = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
    )
    fade_in = _make_analysis(
        musical_key={"root": "F#", "mode": "major", "confidence": 0.9},
    )
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.fade_bars <= 4


def test_path_b_fade_bars_is_power_of_2() -> None:
    """Path B: fade bars are always a power of 2."""
    fade_out = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
    )
    fade_in = _make_analysis(
        musical_key={"root": "D", "mode": "major", "confidence": 0.9},
    )
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.fade_bars in (1, 2, 4, 8, 16)


def test_path_b_bar_alignment_enabled() -> None:
    """Path B: bar alignment is always enabled."""
    fade_out = _make_analysis()
    fade_in = _make_analysis()
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.use_bar_alignment is True


# ── Path B: curve type ───────────────────────────────────────────────


def test_path_b_incompatible_key_exponential() -> None:
    """Path B: incompatible key selects exponential curve."""
    fade_out = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
    )
    fade_in = _make_analysis(
        musical_key={"root": "F#", "mode": "major", "confidence": 0.9},
    )
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.curve_type == "exponential"


def test_path_b_default_curve_is_qsin() -> None:
    """Path B: default curve is qsin (equal-power)."""
    fade_out = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
    )
    fade_in = _make_analysis(
        musical_key={"root": "G", "mode": "major", "confidence": 0.9},
    )
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.curve_type == "qsin"


# ── Graceful degradation ─────────────────────────────────────────────


def test_no_key_uses_neutral_compat() -> None:
    """Missing key data uses neutral compat, producing moderate fade."""
    fade_out = _make_analysis()
    fade_in = _make_analysis()
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.fade_bars in (4, 8)
    assert result.curve_type == "qsin"


def test_no_spectral_still_works() -> None:
    """Missing spectral data still produces valid crossover."""
    fade_out = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
    )
    fade_in = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.9},
    )
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert 600 <= result.crossover_freq <= 3000


def test_low_key_confidence_uses_neutral() -> None:
    """Low confidence keys fall back to neutral compat, not incompatible."""
    fade_out = _make_analysis(
        musical_key={"root": "C", "mode": "major", "confidence": 0.2},
    )
    fade_in = _make_analysis(
        musical_key={"root": "F#", "mode": "major", "confidence": 0.2},
    )
    stretch = _make_stretch(bpm_diff_percent=2.0)
    result = resolve_crossfade_params(
        fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch
    )
    assert result.fade_bars >= 4
