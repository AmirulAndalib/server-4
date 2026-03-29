"""Data models for the smart fades controller module.

All dataclasses used across the smart fades module live here.
Logic stays in its own files (alignment.py, time_stretch.py, crossfade_params.py).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import numpy as np
    import numpy.typing as npt


@dataclass
class MusicalKey:
    """Musical key with Camelot wheel compatibility scoring.

    This is the smart fades module's own representation, constructed from
    the raw key data in AudioAnalysisData. It is not coupled to the
    audio analysis model.
    """

    root: str
    mode: str
    confidence: float

    @property
    def camelot_code(self) -> str | None:
        """Return Camelot wheel code (e.g. '8B', '5A'), or None if unrecognized."""
        return _CAMELOT_WHEEL.get((self.root.lower(), self.mode.lower()))

    def compatibility_score(self, other: MusicalKey) -> float:
        """Return 0.0-1.0 harmonic compatibility using Camelot wheel distance.

        :param other: The other musical key to compare against.
        """
        code_a = self.camelot_code
        code_b = other.camelot_code
        if code_a is None or code_b is None:
            return 0.1

        num_a, letter_a = _parse_camelot(code_a)
        num_b, letter_b = _parse_camelot(code_b)
        num_dist = min(abs(num_a - num_b), 12 - abs(num_a - num_b))
        same_letter = letter_a == letter_b

        if num_dist == 0 and same_letter:
            return 1.0
        if num_dist == 0 and not same_letter:
            return 0.85
        if num_dist == 1 and same_letter:
            return 0.9
        if num_dist == 1 and not same_letter:
            return 0.8
        if num_dist == 2:
            return 0.5
        if num_dist == 3:
            return 0.2
        return 0.1


def _parse_camelot(code: str) -> tuple[int, str]:
    """Parse a Camelot code like '8B' into (number, letter).

    :param code: Camelot code string (e.g., '8B', '11A').
    """
    letter = code[-1]
    number = int(code[:-1])
    return number, letter


# Camelot wheel: maps (root_lowercase, mode_lowercase) -> Camelot code.
# Supports both sharp (C#) and flat (Db) notation.
_CAMELOT_WHEEL: dict[tuple[str, str], str] = {
    # Major keys (B ring)
    ("b", "major"): "1B",
    ("f#", "major"): "2B",
    ("gb", "major"): "2B",
    ("db", "major"): "3B",
    ("c#", "major"): "3B",
    ("ab", "major"): "4B",
    ("g#", "major"): "4B",
    ("eb", "major"): "5B",
    ("d#", "major"): "5B",
    ("bb", "major"): "6B",
    ("a#", "major"): "6B",
    ("f", "major"): "7B",
    ("c", "major"): "8B",
    ("g", "major"): "9B",
    ("d", "major"): "10B",
    ("a", "major"): "11B",
    ("e", "major"): "12B",
    # Minor keys (A ring)
    ("ab", "minor"): "1A",
    ("g#", "minor"): "1A",
    ("eb", "minor"): "2A",
    ("d#", "minor"): "2A",
    ("bb", "minor"): "3A",
    ("a#", "minor"): "3A",
    ("f", "minor"): "4A",
    ("c", "minor"): "5A",
    ("g", "minor"): "6A",
    ("d", "minor"): "7A",
    ("a", "minor"): "8A",
    ("e", "minor"): "9A",
    ("b", "minor"): "10A",
    ("f#", "minor"): "11A",
    ("gb", "minor"): "11A",
    ("c#", "minor"): "12A",
    ("db", "minor"): "12A",
}


@dataclass
class AlignmentResult:
    """Result of crossfade alignment resolution.

    All positions are in source-audio time (unstretched).
    Compensation for time-stretching happens separately via compensate_for_stretch().
    """

    strategy: str
    fadeout_start_pos: float | None
    fadein_start_pos: float | None
    crossfade_duration: float
    fadeout_downbeats_rel: npt.NDArray[np.float64]


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


@dataclass
class CrossfadeConfig:
    """All tunable parameters for crossfade decisions.

    Every threshold, range, and multiplier is configurable for iterative
    listening tests. Change one number, re-run, listen.
    """

    # Path routing
    stretch_threshold_pct: float = 6.0

    # Path A: unstretched (>stretch_threshold_pct BPM diff)
    path_a_min_fade_sec: float = 1.5
    path_a_max_fade_sec: float = 2.5
    path_a_crossover_low: int = 2200
    path_a_crossover_high: int = 3000

    # Path B: key compatibility -> fade length tiers (min_bars, max_bars)
    key_tier_compatible: tuple[int, int] = (8, 16)
    key_tier_moderate: tuple[int, int] = (4, 8)
    key_tier_incompatible: tuple[int, int] = (2, 4)
    key_tier_clashing: tuple[int, int] = (2, 2)

    # Key compatibility thresholds for tier selection
    key_threshold_compatible: float = 0.7
    key_threshold_moderate: float = 0.3
    key_threshold_clashing: float = 0.15

    # Path B: crossover frequency
    crossover_key_base: float = 1000.0
    crossover_key_range: float = 2000.0
    crossover_spectral_scale: float = 0.6
    key_urgency_steepness: float = 1.5
    crossover_min: int = 600
    crossover_max: int = 3000

    # Path B: spectral overlap modifier on fade length
    spectral_fade_mult_min: float = 0.8
    spectral_fade_mult_max: float = 1.2

    # Path B: curve selection thresholds
    key_compat_exp_threshold: float = 0.3
    energy_slope_natural_fade: float = -0.3
    energy_slope_building: float = 0.3
    spectral_overlap_linear_threshold: float = 0.7
    key_compat_linear_threshold: float = 0.5

    # Key detection fallback
    key_compat_neutral: float = 0.6


@dataclass
class CrossfadeParams:
    """Resolved crossfade parameters for filter construction."""

    crossover_freq: int
    max_fade_bars: int
    max_fade_seconds: float
    curve_type: str
    use_bar_alignment: bool
