# Step 2: Smart Crossfade Enhancements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement phrase-aligned crossfade timing (Priority 1), energy-aware crossfade curves (Priority 2), and gradual time stretching (Priority 3) in the smart crossfade filter chain.

**Architecture:** `SmartCrossFade` is extended to accept the new `AudioAnalysisData` fields (energy_curve, spectral_centroid_curve, phrase_boundaries, musical_key). Phrase alignment replaces the current downbeat-only timing logic. Energy-aware curves replace fixed curve selection. A new `GradualTimeStretchFilter` replaces the instant `TimeStretchFilter` using FFmpeg's `asendcmd` + timeline-editable `rubberband`. All changes fall back to current behavior when extended data is absent.

**Tech Stack:** Python 3.12+, FFmpeg (rubberband, asendcmd, acrossfade, volume filters), numpy

**Spec:** `docs/superpowers/specs/2026-03-26-smart-crossfade-improvements-design.md` — "CURRENT SCOPE (Approach B)" section

**Prerequisite:** Step 1 (Smart Fades Provider Enhancements) must be completed first — the crossfade code depends on the new `AudioAnalysisData` fields.

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `music_assistant/controllers/streams/smart_fades/fades.py` | Modify | Phrase-aligned timing in `_build()` and `_calculate_optimal_fade_timing()`, energy-aware curve selection, gradual stretch integration |
| `music_assistant/controllers/streams/smart_fades/filters.py` | Modify | Add `GradualTimeStretchFilter` class, add `GainCompensationFilter` class |
| `music_assistant/controllers/streams/smart_fades/crossfade_helpers.py` | Create | Pure functions: phrase alignment scoring, energy curve analysis, S-curve tempo computation |
| `tests/controllers/streams/smart_fades/test_crossfade_helpers.py` | Create | Unit tests for all helper functions |

---

### Task 1: Create crossfade_helpers.py with phrase alignment logic

**Files:**
- Create: `music_assistant/controllers/streams/smart_fades/crossfade_helpers.py`
- Create: `tests/controllers/streams/smart_fades/__init__.py`
- Create: `tests/controllers/streams/smart_fades/test_crossfade_helpers.py`

- [ ] **Step 1: Write the failing test for phrase alignment scoring**

Create `tests/controllers/streams/smart_fades/__init__.py` (empty file).

Create `tests/controllers/streams/smart_fades/test_crossfade_helpers.py`:

```python
"""Tests for smart crossfade helper functions."""

import numpy as np
import pytest

from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    find_best_phrase_boundary,
)


def test_find_best_phrase_boundary_energy_decline() -> None:
    """Should find a phrase boundary where outgoing energy is declining."""
    # 60 seconds of energy: high for first 40s, declining from 40-50s, low after
    energy = np.ones(60, dtype=np.float32) * 0.8
    energy[40:50] = np.linspace(0.8, 0.2, 10).astype(np.float32)
    energy[50:] = 0.2

    # Phrase boundaries at 32s, 40s, 48s
    phrase_boundaries = [
        {"time": 32.0, "confidence": 0.7, "boundary_type": "phrase"},
        {"time": 40.0, "confidence": 0.9, "boundary_type": "section"},
        {"time": 48.0, "confidence": 0.6, "boundary_type": "phrase"},
    ]

    result = find_best_phrase_boundary(
        phrase_boundaries=phrase_boundaries,
        energy_curve=energy,
        search_start=30.0,
        search_end=55.0,
        prefer_declining_energy=True,
    )

    # Should pick 40.0 — section boundary with energy decline starting
    assert result is not None
    assert abs(result - 40.0) < 1.0


def test_find_best_phrase_boundary_none_available() -> None:
    """Should return None when no phrase boundaries in search range."""
    energy = np.ones(60, dtype=np.float32) * 0.5
    boundaries = [{"time": 10.0, "confidence": 0.9, "boundary_type": "section"}]

    result = find_best_phrase_boundary(
        phrase_boundaries=boundaries,
        energy_curve=energy,
        search_start=30.0,
        search_end=55.0,
    )

    assert result is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/controllers/streams/smart_fades/test_crossfade_helpers.py::test_find_best_phrase_boundary_energy_decline -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement find_best_phrase_boundary**

Create `music_assistant/controllers/streams/smart_fades/crossfade_helpers.py`:

```python
"""Helper functions for smart crossfade decisions.

Pure functions for phrase alignment, energy analysis, and tempo ramping.
These do not depend on FFmpeg — they compute parameters that feed into
the filter chain.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


def find_best_phrase_boundary(
    phrase_boundaries: list[dict],
    energy_curve: npt.NDArray[np.float32],
    search_start: float,
    search_end: float,
    prefer_declining_energy: bool = False,
) -> float | None:
    """Find the best phrase boundary within a time range for crossfade start.

    Scores boundaries by confidence, boundary_type (section > phrase),
    and optionally by whether energy is declining at that point.

    :param phrase_boundaries: List of PhraseBoundary dicts with 'time', 'confidence', 'boundary_type'.
    :param energy_curve: Normalized [0,1] RMS energy per second.
    :param search_start: Start of search range in seconds.
    :param search_end: End of search range in seconds.
    :param prefer_declining_energy: If True, prefer boundaries where energy is declining.
    :return: Best boundary time in seconds, or None if none found.
    """
    candidates = [
        b for b in phrase_boundaries
        if search_start <= b["time"] <= search_end
    ]

    if not candidates:
        return None

    def _score(boundary: dict) -> float:
        score = boundary["confidence"]
        if boundary["boundary_type"] == "section":
            score += 0.3

        if prefer_declining_energy:
            sec_idx = int(boundary["time"])
            if 2 <= sec_idx < len(energy_curve) - 2:
                e_before = float(np.mean(energy_curve[max(0, sec_idx - 2) : sec_idx]))
                e_after = float(np.mean(energy_curve[sec_idx : min(len(energy_curve), sec_idx + 2)]))
                if e_before > e_after:
                    # Energy is declining — good for outgoing track
                    score += 0.4 * (e_before - e_after)

        return score

    best = max(candidates, key=_score)
    return best["time"]
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/controllers/streams/smart_fades/test_crossfade_helpers.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/crossfade_helpers.py tests/controllers/streams/smart_fades/
git commit -m "feat: add phrase boundary scoring for crossfade alignment"
```

---

### Task 2: Add energy cross-validation and curve selection helpers

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/crossfade_helpers.py`
- Modify: `tests/controllers/streams/smart_fades/test_crossfade_helpers.py`

- [ ] **Step 1: Write the failing tests**

Add to `tests/controllers/streams/smart_fades/test_crossfade_helpers.py`:

```python
from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    find_best_phrase_boundary,
    validate_energy_crossover,
    select_crossfade_curve_type,
    compute_gain_compensation_db,
)


def test_validate_energy_crossover_good() -> None:
    """Should validate when outgoing declining, incoming rising."""
    outgoing_energy = np.array([0.8, 0.7, 0.5, 0.3, 0.2], dtype=np.float32)
    incoming_energy = np.array([0.1, 0.2, 0.4, 0.6, 0.8], dtype=np.float32)

    is_valid, crossing_point = validate_energy_crossover(outgoing_energy, incoming_energy)

    assert is_valid is True
    assert crossing_point is not None
    assert 1 <= crossing_point <= 3  # Curves cross somewhere in the middle


def test_validate_energy_crossover_both_high() -> None:
    """Should fail validation when both tracks are at peak energy."""
    outgoing_energy = np.array([0.9, 0.9, 0.9, 0.9, 0.9], dtype=np.float32)
    incoming_energy = np.array([0.9, 0.9, 0.9, 0.9, 0.9], dtype=np.float32)

    is_valid, crossing_point = validate_energy_crossover(outgoing_energy, incoming_energy)

    assert is_valid is False


def test_select_crossfade_curve_type_similar_slopes() -> None:
    """Similar energy slopes should select equal-power (qsin)."""
    outgoing = np.linspace(0.8, 0.2, 10, dtype=np.float32)
    incoming = np.linspace(0.2, 0.8, 10, dtype=np.float32)

    curve = select_crossfade_curve_type(outgoing, incoming)

    assert curve == "qsin"  # Equal-power for similar slopes


def test_select_crossfade_curve_type_divergent_slopes() -> None:
    """Divergent slopes should select equal-gain (linear)."""
    outgoing = np.linspace(0.8, 0.2, 10, dtype=np.float32)
    incoming = np.ones(10, dtype=np.float32) * 0.5  # Flat — divergent from declining

    curve = select_crossfade_curve_type(outgoing, incoming)

    assert curve == "tri"  # Equal-gain (linear) for divergent slopes


def test_compute_gain_compensation_db() -> None:
    """Should compute dB difference to normalize quieter track."""
    loud = np.array([0.8, 0.9, 0.7], dtype=np.float32)
    quiet = np.array([0.2, 0.3, 0.25], dtype=np.float32)

    db = compute_gain_compensation_db(loud, quiet)

    # Quiet track needs positive dB boost
    assert db > 0
    assert db < 20  # Sanity check
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/controllers/streams/smart_fades/test_crossfade_helpers.py::test_validate_energy_crossover_good -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement the three functions**

Add to `music_assistant/controllers/streams/smart_fades/crossfade_helpers.py`:

```python
def validate_energy_crossover(
    outgoing_energy: npt.NDArray[np.float32],
    incoming_energy: npt.NDArray[np.float32],
) -> tuple[bool, int | None]:
    """Validate that outgoing energy is declining and incoming is rising.

    If both tracks are at peak energy, returns False — the crossfade should
    be shifted to where energy curves cross, even if it breaks phrase alignment.

    :param outgoing_energy: Per-second energy for the outgoing track's crossfade region.
    :param incoming_energy: Per-second energy for the incoming track's crossfade region.
    :return: Tuple of (is_valid, crossing_point_index). crossing_point is the
             index where energy curves cross (outgoing < incoming), or None.
    """
    if len(outgoing_energy) < 2 or len(incoming_energy) < 2:
        return True, None  # Not enough data to validate

    min_len = min(len(outgoing_energy), len(incoming_energy))
    out = outgoing_energy[:min_len]
    inc = incoming_energy[:min_len]

    # Check if both are consistently high (both > 0.7 average)
    if float(np.mean(out)) > 0.7 and float(np.mean(inc)) > 0.7:
        return False, None

    # Check slopes
    out_slope = float(np.polyfit(np.arange(len(out)), out, 1)[0])
    inc_slope = float(np.polyfit(np.arange(len(inc)), inc, 1)[0])

    # Valid: outgoing declining (negative slope), incoming rising (positive slope)
    is_valid = out_slope < 0 or inc_slope > 0

    # Find crossing point
    diff = out - inc
    crossing_indices = np.where(np.diff(np.sign(diff)))[0]
    crossing_point = int(crossing_indices[0]) if len(crossing_indices) > 0 else None

    return is_valid, crossing_point


def select_crossfade_curve_type(
    outgoing_energy: npt.NDArray[np.float32],
    incoming_energy: npt.NDArray[np.float32],
) -> str:
    """Select crossfade curve type based on energy slope comparison.

    Similar slopes = equal-power (qsin). Divergent slopes = equal-gain (tri/linear).

    :param outgoing_energy: Per-second energy for outgoing track's crossfade region.
    :param incoming_energy: Per-second energy for incoming track's crossfade region.
    :return: FFmpeg acrossfade curve name ('qsin' for equal-power, 'tri' for equal-gain).
    """
    if len(outgoing_energy) < 2 or len(incoming_energy) < 2:
        return "tri"

    out_slope = float(np.polyfit(np.arange(len(outgoing_energy)), outgoing_energy, 1)[0])
    inc_slope = float(np.polyfit(np.arange(len(incoming_energy)), incoming_energy, 1)[0])

    # "Similar" = both negative, both positive, or both near zero
    # "Divergent" = one clearly declining while other flat/rising
    slope_diff = abs(out_slope - inc_slope)

    if slope_diff < 0.05:
        return "qsin"  # Equal-power
    return "tri"  # Equal-gain (linear)


def compute_gain_compensation_db(
    outgoing_energy: npt.NDArray[np.float32],
    incoming_energy: npt.NDArray[np.float32],
) -> float:
    """Compute dB gain needed to compensate for loudness difference.

    Returns positive dB if the incoming track is quieter (needs boost),
    negative if louder (needs attenuation), or 0 if similar.

    :param outgoing_energy: Per-second energy for outgoing track's crossfade region.
    :param incoming_energy: Per-second energy for incoming track's crossfade region.
    :return: Gain in dB to apply to incoming track. Clamped to [-6, 6] dB.
    """
    out_rms = float(np.mean(outgoing_energy)) if len(outgoing_energy) > 0 else 0.0
    inc_rms = float(np.mean(incoming_energy)) if len(incoming_energy) > 0 else 0.0

    if inc_rms < 1e-10 or out_rms < 1e-10:
        return 0.0

    ratio = out_rms / inc_rms
    db = 20.0 * np.log10(ratio)

    return float(np.clip(db, -6.0, 6.0))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/controllers/streams/smart_fades/test_crossfade_helpers.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/crossfade_helpers.py tests/controllers/streams/smart_fades/test_crossfade_helpers.py
git commit -m "feat: add energy cross-validation, curve selection, and gain compensation"
```

---

### Task 3: Add S-curve tempo ramp computation

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/crossfade_helpers.py`
- Modify: `tests/controllers/streams/smart_fades/test_crossfade_helpers.py`

- [ ] **Step 1: Write the failing test**

Add to `tests/controllers/streams/smart_fades/test_crossfade_helpers.py`:

```python
from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    compute_gradual_tempo_steps,
    find_best_phrase_boundary,
    validate_energy_crossover,
    select_crossfade_curve_type,
    compute_gain_compensation_db,
)


def test_compute_gradual_tempo_steps_5_percent() -> None:
    """5% tempo change over 10 downbeats should produce S-curve steps."""
    # Downbeats every 2 seconds (120 BPM, 4/4)
    downbeats = np.arange(0, 20, 2.0)

    steps = compute_gradual_tempo_steps(
        start_ratio=1.0,
        end_ratio=1.05,
        downbeats=downbeats,
    )

    assert len(steps) > 0
    # Each step is (timestamp, tempo_ratio)
    timestamps = [s[0] for s in steps]
    ratios = [s[1] for s in steps]

    # First ratio should be close to 1.0, last close to 1.05
    assert abs(ratios[0] - 1.0) < 0.01
    assert abs(ratios[-1] - 1.05) < 0.001

    # S-curve: middle steps should change faster than edges
    if len(ratios) > 4:
        early_delta = abs(ratios[1] - ratios[0])
        mid_idx = len(ratios) // 2
        mid_delta = abs(ratios[mid_idx] - ratios[mid_idx - 1])
        assert mid_delta > early_delta, "S-curve should have larger steps in the middle"

    # Max step size should be <= 0.5%
    for i in range(1, len(ratios)):
        assert abs(ratios[i] - ratios[i - 1]) <= 0.006, (
            f"Step {i}: delta {abs(ratios[i] - ratios[i-1]):.4f} exceeds 0.5%"
        )


def test_compute_gradual_tempo_steps_small_change() -> None:
    """Less than 0.5% change should produce a single step."""
    downbeats = np.arange(0, 20, 2.0)

    steps = compute_gradual_tempo_steps(
        start_ratio=1.0,
        end_ratio=1.003,
        downbeats=downbeats,
    )

    # Small change = 1 step directly to target
    assert len(steps) >= 1
    assert abs(steps[-1][1] - 1.003) < 0.001
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/controllers/streams/smart_fades/test_crossfade_helpers.py::test_compute_gradual_tempo_steps_5_percent -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement compute_gradual_tempo_steps**

Add to `music_assistant/controllers/streams/smart_fades/crossfade_helpers.py`:

```python
def compute_gradual_tempo_steps(
    start_ratio: float,
    end_ratio: float,
    downbeats: npt.NDArray[np.float64],
    max_step_pct: float = 0.005,
) -> list[tuple[float, float]]:
    """Compute S-curve tempo steps aligned to downbeats.

    Returns a list of (timestamp, tempo_ratio) pairs for use with FFmpeg's
    asendcmd + rubberband. Steps follow a sigmoid curve: small changes at
    start/end, larger in the middle.

    :param start_ratio: Starting tempo ratio (e.g., 1.0).
    :param end_ratio: Target tempo ratio (e.g., 1.05).
    :param downbeats: Array of downbeat timestamps to align steps to.
    :param max_step_pct: Maximum tempo change per step as a fraction (default 0.005 = 0.5%).
    :return: List of (timestamp_seconds, tempo_ratio) tuples.
    """
    total_change = abs(end_ratio - start_ratio)

    if total_change < 1e-6:
        return []

    # Minimum number of steps to stay within max_step_pct
    min_steps = max(1, int(np.ceil(total_change / max_step_pct)))

    # Use available downbeats, but don't exceed what we need
    n_steps = min(min_steps, len(downbeats))
    if n_steps < 1:
        return [(0.0, end_ratio)]

    # S-curve (sigmoid) distribution
    # Generate normalized positions [0, 1] for each step
    if n_steps == 1:
        sigmoid_values = np.array([1.0])
    else:
        k = 10.0  # Steepness of sigmoid
        x = np.linspace(-1, 1, n_steps)
        sigmoid_values = 1.0 / (1.0 + np.exp(-k * x))
        # Normalize to [0, 1]
        sigmoid_values = (sigmoid_values - sigmoid_values[0]) / (sigmoid_values[-1] - sigmoid_values[0])

    steps: list[tuple[float, float]] = []
    for i in range(n_steps):
        timestamp = float(downbeats[i]) if i < len(downbeats) else float(downbeats[-1])
        ratio = start_ratio + (end_ratio - start_ratio) * float(sigmoid_values[i])
        steps.append((timestamp, round(ratio, 6)))

    return steps
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/controllers/streams/smart_fades/test_crossfade_helpers.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/crossfade_helpers.py tests/controllers/streams/smart_fades/test_crossfade_helpers.py
git commit -m "feat: add S-curve gradual tempo step computation"
```

---

### Task 4: Add GradualTimeStretchFilter to filters.py

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/filters.py`

- [ ] **Step 1: Add GradualTimeStretchFilter class**

Add to `music_assistant/controllers/streams/smart_fades/filters.py` after the `TimeStretchFilter` class (after line 47):

```python
class GradualTimeStretchFilter(Filter):
    """Apply gradual tempo change using asendcmd + rubberband with S-curve steps.

    Uses FFmpeg's timeline-editable rubberband filter to schedule tempo
    changes at downbeat timestamps within a single FFmpeg invocation.
    Falls back to the segmented approach if asendcmd is unavailable.
    """

    output_fadeout_label: str = "fadeout_gradstretch"
    output_fadein_label: str = "fadein_unchanged"

    def __init__(
        self,
        logger: logging.Logger,
        tempo_steps: list[tuple[float, float]],
    ) -> None:
        """Initialize GradualTimeStretchFilter.

        :param logger: Logger for debug output.
        :param tempo_steps: List of (timestamp_seconds, tempo_ratio) from compute_gradual_tempo_steps.
        """
        super().__init__(logger)
        self.tempo_steps = tempo_steps
        self.logger.debug(
            "GradualTimeStretch: %d steps from %.4f to %.4f",
            len(tempo_steps),
            tempo_steps[0][1] if tempo_steps else 1.0,
            tempo_steps[-1][1] if tempo_steps else 1.0,
        )

    def apply(self, input_fadein_label: str, input_fadeout_label: str) -> list[str]:
        """Build FFmpeg filter string for gradual time stretching."""
        if not self.tempo_steps:
            self.output_fadeout_label = input_fadeout_label.strip("[]")
            self.output_fadein_label = input_fadein_label.strip("[]")
            return []

        # Build asendcmd command string for tempo changes at each timestamp
        cmd_parts = []
        for timestamp, ratio in self.tempo_steps:
            cmd_parts.append(f"{timestamp:.3f} [rb] tempo {ratio:.6f}")
        cmd_string = "; ".join(cmd_parts)

        initial_ratio = self.tempo_steps[0][1]

        filters = [
            # Apply gradual stretch to fadeout (outgoing) track
            f"{input_fadeout_label} asendcmd=c='{cmd_string}',"
            f"rubberband@rb=tempo={initial_ratio:.6f}:transients=smooth:detector=soft:pitchq=speed"
            f" [{self.output_fadeout_label}]",
            # Fadein passes through unchanged
            f"{input_fadein_label} acopy [{self.output_fadein_label}]",
        ]

        return filters

    def __repr__(self) -> str:
        """Return string representation."""
        n = len(self.tempo_steps)
        start = self.tempo_steps[0][1] if self.tempo_steps else 1.0
        end = self.tempo_steps[-1][1] if self.tempo_steps else 1.0
        return f"GradualTimeStretchFilter(steps={n}, {start:.4f}->{end:.4f})"
```

- [ ] **Step 2: Add GainCompensationFilter class**

Add after GradualTimeStretchFilter:

```python
class GainCompensationFilter(Filter):
    """Apply gain compensation to balance loudness between tracks."""

    output_fadeout_label: str = "fadeout_gain"
    output_fadein_label: str = "fadein_gain"

    def __init__(
        self,
        logger: logging.Logger,
        fadein_gain_db: float,
    ) -> None:
        """Initialize GainCompensationFilter.

        :param logger: Logger for debug output.
        :param fadein_gain_db: Gain in dB to apply to incoming track. Positive = boost.
        """
        super().__init__(logger)
        self.fadein_gain_db = fadein_gain_db
        self.logger.debug("GainCompensation: %.1f dB on incoming track", fadein_gain_db)

    def apply(self, input_fadein_label: str, input_fadeout_label: str) -> list[str]:
        """Build FFmpeg filter string for gain compensation."""
        if abs(self.fadein_gain_db) < 0.5:
            # Less than 0.5 dB difference — skip
            self.output_fadeout_label = input_fadeout_label.strip("[]")
            self.output_fadein_label = input_fadein_label.strip("[]")
            return []

        filters = [
            f"{input_fadeout_label} acopy [{self.output_fadeout_label}]",
            f"{input_fadein_label} volume={self.fadein_gain_db:.1f}dB [{self.output_fadein_label}]",
        ]

        return filters

    def __repr__(self) -> str:
        """Return string representation."""
        return f"GainCompensationFilter(fadein={self.fadein_gain_db:.1f}dB)"
```

- [ ] **Step 3: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 4: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/filters.py
git commit -m "feat: add GradualTimeStretchFilter and GainCompensationFilter"
```

---

### Task 5: Integrate phrase alignment into SmartCrossFade._build()

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/fades.py:155-190` (SmartCrossFade.__init__)
- Modify: `music_assistant/controllers/streams/smart_fades/fades.py:192-310` (SmartCrossFade._build)

- [ ] **Step 1: Extend SmartCrossFade.__init__ to accept extended analysis data**

In `fades.py`, update `SmartCrossFade.__init__` (line 161) to extract the new fields from `AudioAnalysisData`:

```python
    def __init__(
        self,
        logger: logging.Logger,
        fade_out_analysis: AudioAnalysisData,
        fade_in_analysis: AudioAnalysisData,
    ) -> None:
        """Initialize SmartFades with analysis data.

        :param logger: Logger for debug output.
        :param fade_out_analysis: Analysis data for the outgoing track.
        :param fade_in_analysis: Analysis data for the incoming track.
        """
        if (
            fade_out_analysis.bpm is None
            or fade_in_analysis.bpm is None
            or fade_out_analysis.beats is None
            or fade_in_analysis.beats is None
        ):
            raise ValueError("AudioAnalysisData must have bpm and beats set for smart crossfade")
        self.fade_out_bpm: float = fade_out_analysis.bpm
        self.fade_in_bpm: float = fade_in_analysis.bpm
        self.fade_out_beats: npt.NDArray[np.float64] = fade_out_analysis.beats
        self.fade_in_beats: npt.NDArray[np.float64] = fade_in_analysis.beats
        self.fade_out_downbeats: npt.NDArray[np.float64] = (
            fade_out_analysis.downbeats if fade_out_analysis.downbeats is not None else np.array([])
        )
        self.fade_in_downbeats: npt.NDArray[np.float64] = (
            fade_in_analysis.downbeats if fade_in_analysis.downbeats is not None else np.array([])
        )
        # Extended analysis data (may be None — all logic must handle absence gracefully)
        self.fade_out_energy: npt.NDArray[np.float32] | None = fade_out_analysis.energy_curve
        self.fade_in_energy: npt.NDArray[np.float32] | None = fade_in_analysis.energy_curve
        self.fade_out_centroid: npt.NDArray[np.float32] | None = fade_out_analysis.spectral_centroid_curve
        self.fade_in_centroid: npt.NDArray[np.float32] | None = fade_in_analysis.spectral_centroid_curve
        self.fade_out_phrases: list | None = fade_out_analysis.phrase_boundaries
        self.fade_in_phrases: list | None = fade_in_analysis.phrase_boundaries
        super().__init__(logger)
```

- [ ] **Step 2: Update _build() to use phrase alignment with energy cross-validation**

This is the largest change. Update `_build()` to add phrase-aware timing before the existing filter chain construction. The key insertion point is where `fadein_start_pos` is computed (currently `_calculate_optimal_fade_timing`). Add phrase-aligned logic as the preferred path, falling back to current behavior.

Add imports at the top of `fades.py`:

```python
from .crossfade_helpers import (
    compute_gain_compensation_db,
    compute_gradual_tempo_steps,
    find_best_phrase_boundary,
    select_crossfade_curve_type,
    validate_energy_crossover,
)
from .filters import (
    CrossfadeFilter,
    FrequencySweepFilter,
    GainCompensationFilter,
    GradualTimeStretchFilter,
    TimeStretchFilter,
    TrimFilter,
)
```

In `_build()`, after the existing BPM/stretch logic and before the filter chain construction, add the phrase alignment logic. The existing method body should be refactored to:

1. Try phrase-aligned timing first (if phrase_boundaries available)
2. Validate with energy cross-validation
3. Fall back to current downbeat-based timing
4. Use energy-aware curve selection instead of fixed curves
5. Use gradual stretch instead of instant stretch (if BPM diff <= 5%)
6. Add gain compensation

The exact code depends heavily on the existing `_build()` structure — the implementing engineer should read the full method (lines 192-310) and integrate the new logic while preserving the fallback hierarchy.

Key integration points in `_build()`:
- Replace `TimeStretchFilter` with `GradualTimeStretchFilter` when BPM diff is 1-5%
- Add `GainCompensationFilter` before `CrossfadeFilter` when energy data is available
- Use `select_crossfade_curve_type()` result to set `c1`/`c2` params on `CrossfadeFilter`
- Use `find_best_phrase_boundary()` to override `fadein_start_pos` when phrase data is available
- Use `validate_energy_crossover()` to confirm or shift the chosen crossfade position

- [ ] **Step 3: Add decision logging**

Add a summary log line at the end of `_build()`:

```python
        self.logger.info(
            "Smart crossfade: %s, phrase_aligned=%s, curve=%s, "
            "gradual_stretch=%s, gain_comp=%.1fdB",
            f"{self.fade_out_bpm:.0f}->{self.fade_in_bpm:.0f} BPM",
            phrase_aligned,
            curve_type,
            gradual_stretch_used,
            gain_db,
        )
```

- [ ] **Step 4: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 5: Run all tests**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 6: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/fades.py
git commit -m "feat: integrate phrase alignment, energy-aware curves, and gradual stretching into SmartCrossFade"
```

---

### Task 6: Verify end-to-end with pre-commit and full test suite

**Files:** None (verification only)

- [ ] **Step 1: Run full pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 2: Run full test suite**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/ -v`
Expected: All tests PASS

- [ ] **Step 3: Verify the filter chain logs**

Run the server locally or check that the test produces the expected log line pattern:
```
Smart crossfade: 120->125 BPM, phrase_aligned=True, curve=qsin, gradual_stretch=True, gain_comp=1.5dB
```

- [ ] **Step 4: Final commit if any fixups needed**

```bash
git add -A
git commit -m "fix: address pre-commit and test issues from crossfade enhancements"
```
