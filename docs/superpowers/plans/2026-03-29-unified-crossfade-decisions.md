# Unified Crossfade Decision Framework — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace inline crossover/curve/fade-length heuristics in `_build_filters()` with a unified decision pipeline driven by key compatibility, spectral centroid, and energy contours.

**Architecture:** Two new files (`models.py` for all dataclasses, `crossfade_params.py` for resolver logic) in the smart_fades controller module. Existing dataclasses move into `models.py`. The resolver follows the same pattern as `resolve_alignment()` and `resolve_time_stretch()`.

**Tech Stack:** Python 3.12+, dataclasses, numpy (minimal — slopes from energy curves), math (sqrt, log2). No new dependencies.

**Spec:** `docs/superpowers/specs/2026-03-29-unified-crossfade-decisions-design.md`

---

### Task 1: Create `models.py` with `MusicalKey` and Camelot wheel

**Files:**
- Create: `music_assistant/controllers/streams/smart_fades/models.py`
- Test: `tests/controllers/streams/smart_fades/test_models.py`

- [ ] **Step 1: Write failing tests for `MusicalKey.camelot_code`**

Create `tests/controllers/streams/smart_fades/test_models.py`:

```python
"""Tests for smart fades models — MusicalKey and Camelot wheel."""

from music_assistant.controllers.streams.smart_fades.models import MusicalKey


class TestMusicalKeyCamelotCode:
    """Tests for MusicalKey.camelot_code property."""

    def test_c_major_is_8b(self) -> None:
        key = MusicalKey(root="C", mode="major", confidence=0.9)
        assert key.camelot_code == "8B"

    def test_a_minor_is_8a(self) -> None:
        key = MusicalKey(root="A", mode="minor", confidence=0.9)
        assert key.camelot_code == "8A"

    def test_d_major_is_10b(self) -> None:
        key = MusicalKey(root="D", mode="major", confidence=0.9)
        assert key.camelot_code == "10B"

    def test_f_sharp_minor_is_11a(self) -> None:
        key = MusicalKey(root="F#", mode="minor", confidence=0.9)
        assert key.camelot_code == "11A"

    def test_b_flat_major_is_6b(self) -> None:
        key = MusicalKey(root="Bb", mode="major", confidence=0.9)
        assert key.camelot_code == "6B"

    def test_e_flat_minor_is_2a(self) -> None:
        key = MusicalKey(root="Eb", mode="minor", confidence=0.9)
        assert key.camelot_code == "2A"

    def test_unknown_root_returns_none(self) -> None:
        key = MusicalKey(root="X", mode="major", confidence=0.9)
        assert key.camelot_code is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/controllers/streams/smart_fades/test_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'MusicalKey' from 'music_assistant.controllers.streams.smart_fades.models'`

- [ ] **Step 3: Implement `MusicalKey` with Camelot lookup in `models.py`**

Create `music_assistant/controllers/streams/smart_fades/models.py`:

```python
"""Data models for the smart fades controller module.

All dataclasses used across the smart fades module live here.
Logic stays in its own files (alignment.py, time_stretch.py, crossfade_params.py).
"""

from __future__ import annotations

from dataclasses import dataclass


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


# Camelot wheel: maps (root_lowercase, mode_lowercase) -> Camelot code.
# Supports both sharp (C#) and flat (Db) notation.
_CAMELOT_WHEEL: dict[tuple[str, str], str] = {
    # Major keys (B ring)
    ("b", "major"): "1B",
    ("f#", "major"): "2B", ("gb", "major"): "2B",
    ("db", "major"): "3B", ("c#", "major"): "3B",
    ("ab", "major"): "4B", ("g#", "major"): "4B",
    ("eb", "major"): "5B", ("d#", "major"): "5B",
    ("bb", "major"): "6B", ("a#", "major"): "6B",
    ("f", "major"): "7B",
    ("c", "major"): "8B",
    ("g", "major"): "9B",
    ("d", "major"): "10B",
    ("a", "major"): "11B",
    ("e", "major"): "12B",
    # Minor keys (A ring)
    ("ab", "minor"): "1A", ("g#", "minor"): "1A",
    ("eb", "minor"): "2A", ("d#", "minor"): "2A",
    ("bb", "minor"): "3A", ("a#", "minor"): "3A",
    ("f", "minor"): "4A",
    ("c", "minor"): "5A",
    ("g", "minor"): "6A",
    ("d", "minor"): "7A",
    ("a", "minor"): "8A",
    ("e", "minor"): "9A",
    ("b", "minor"): "10A",
    ("f#", "minor"): "11A", ("gb", "minor"): "11A",
    ("c#", "minor"): "12A", ("db", "minor"): "12A",
}
```

Note: The lookup uses `.lower()` on both root and mode so that `"C"`, `"c"`, `"Bb"`, `"bb"` all work. Supports both sharp and flat enharmonic equivalents (e.g., `C#` and `Db` both map to `3B`).

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/controllers/streams/smart_fades/test_models.py -v`
Expected: All PASS

- [ ] **Step 5: Run pre-commit**

Run: `pre-commit run --all-files`

- [ ] **Step 6: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/models.py tests/controllers/streams/smart_fades/test_models.py
git commit -m "feat: add MusicalKey with Camelot wheel lookup"
```

---

### Task 2: Add `compatibility_score()` to `MusicalKey`

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/models.py`
- Modify: `tests/controllers/streams/smart_fades/test_models.py`

- [ ] **Step 1: Write failing tests for `compatibility_score()`**

Add to `tests/controllers/streams/smart_fades/test_models.py`:

```python
class TestMusicalKeyCompatibility:
    """Tests for MusicalKey.compatibility_score()."""

    def test_same_key_returns_1(self) -> None:
        a = MusicalKey(root="C", mode="major", confidence=0.9)
        b = MusicalKey(root="C", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == 1.0

    def test_adjacent_position_returns_0_9(self) -> None:
        # 8B (C major) -> 9B (G major) = adjacent, same letter
        a = MusicalKey(root="C", mode="major", confidence=0.9)
        b = MusicalKey(root="G", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == 0.9

    def test_adjacent_wraps_around(self) -> None:
        # 1B (B major) -> 12B (E major) = distance 1, wraps
        a = MusicalKey(root="B", mode="major", confidence=0.9)
        b = MusicalKey(root="E", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == 0.9

    def test_relative_major_minor_returns_0_85(self) -> None:
        # 8A (A minor) -> 8B (C major) = same number, different letter
        a = MusicalKey(root="A", mode="minor", confidence=0.9)
        b = MusicalKey(root="C", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == 0.85

    def test_adjacent_plus_relative_returns_0_8(self) -> None:
        # 8A (A minor) -> 7B (F major) = distance 1, different letter
        a = MusicalKey(root="A", mode="minor", confidence=0.9)
        b = MusicalKey(root="F", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == 0.8

    def test_two_positions_returns_0_5(self) -> None:
        # 8B (C major) -> 10B (D major) = distance 2, same letter
        a = MusicalKey(root="C", mode="major", confidence=0.9)
        b = MusicalKey(root="D", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == 0.5

    def test_three_positions_returns_0_2(self) -> None:
        # 8B (C major) -> 11B (A major) = distance 3, same letter
        a = MusicalKey(root="C", mode="major", confidence=0.9)
        b = MusicalKey(root="A", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == 0.2

    def test_distant_key_returns_0_1(self) -> None:
        # 8B (C major) -> 2B (F# major) = distance 6, same letter
        a = MusicalKey(root="C", mode="major", confidence=0.9)
        b = MusicalKey(root="F#", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == 0.1

    def test_unknown_key_returns_0_1(self) -> None:
        a = MusicalKey(root="X", mode="major", confidence=0.9)
        b = MusicalKey(root="C", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == 0.1

    def test_symmetry(self) -> None:
        a = MusicalKey(root="C", mode="major", confidence=0.9)
        b = MusicalKey(root="G", mode="major", confidence=0.9)
        assert a.compatibility_score(b) == b.compatibility_score(a)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/controllers/streams/smart_fades/test_models.py::TestMusicalKeyCompatibility -v`
Expected: FAIL — `AttributeError: 'MusicalKey' object has no attribute 'compatibility_score'`

- [ ] **Step 3: Implement `compatibility_score()`**

Add to `MusicalKey` in `models.py`:

```python
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
```

Add the helper function (module-level in `models.py`):

```python
def _parse_camelot(code: str) -> tuple[int, str]:
    """Parse a Camelot code like '8B' into (number, letter).

    :param code: Camelot code string (e.g., '8B', '11A').
    """
    letter = code[-1]
    number = int(code[:-1])
    return number, letter
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/controllers/streams/smart_fades/test_models.py -v`
Expected: All PASS

- [ ] **Step 5: Run pre-commit**

Run: `pre-commit run --all-files`

- [ ] **Step 6: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/models.py tests/controllers/streams/smart_fades/test_models.py
git commit -m "feat: add Camelot wheel compatibility scoring to MusicalKey"
```

---

### Task 3: Add `CrossfadeConfig` and `CrossfadeParams` to `models.py`

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/models.py`

- [ ] **Step 1: Add `CrossfadeConfig` and `CrossfadeParams` dataclasses**

Append to `models.py`:

```python
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
    long_fade_linear_threshold: int = 12

    # Key detection confidence gate
    key_confidence_threshold: float = 0.4
    key_compat_neutral: float = 0.6


@dataclass
class CrossfadeParams:
    """Resolved crossfade parameters for filter construction."""

    crossover_freq: int
    fade_bars: int
    fade_seconds: float
    curve_type: str
    use_bar_alignment: bool
```

- [ ] **Step 2: Run pre-commit**

Run: `pre-commit run --all-files`

- [ ] **Step 3: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/models.py
git commit -m "feat: add CrossfadeConfig and CrossfadeParams dataclasses"
```

---

### Task 4: Move `AlignmentResult` and `TimeStretchDecision` into `models.py`

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/models.py`
- Modify: `music_assistant/controllers/streams/smart_fades/alignment.py`
- Modify: `music_assistant/controllers/streams/smart_fades/time_stretch.py`
- Modify: `music_assistant/controllers/streams/smart_fades/fades.py`

- [ ] **Step 1: Copy `AlignmentResult` into `models.py`**

Add numpy imports to `models.py`:

```python
import numpy as np
import numpy.typing as npt
```

Copy the `AlignmentResult` dataclass from `alignment.py` into `models.py`:

```python
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
    curve_type: str | None
    fadeout_downbeats_rel: npt.NDArray[np.float64]
```

- [ ] **Step 2: Copy `TimeStretchDecision` into `models.py`**

```python
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
```

- [ ] **Step 3: Update `alignment.py` to import from `models.py`**

In `alignment.py`, remove the `AlignmentResult` dataclass definition and the `@dataclass` import (if no longer needed). Replace with:

```python
from music_assistant.controllers.streams.smart_fades.models import AlignmentResult
```

Keep the re-export so existing callers aren't broken — add after the import:

```python
__all__ = ["AlignmentResult", "resolve_alignment"]
```

- [ ] **Step 4: Update `time_stretch.py` to import from `models.py`**

In `time_stretch.py`, remove the `TimeStretchDecision` dataclass definition. Replace with:

```python
from music_assistant.controllers.streams.smart_fades.models import (
    AlignmentResult,
    TimeStretchDecision,
)
```

Remove the old import of `AlignmentResult` from `alignment.py` (it was `from music_assistant.controllers.streams.smart_fades.alignment import AlignmentResult`).

- [ ] **Step 5: Update `fades.py` imports**

In `fades.py`, update the `AlignmentResult` import to come from `models.py`:

```python
from music_assistant.controllers.streams.smart_fades.models import AlignmentResult
```

Remove `AlignmentResult` from the `alignment` import (keep `resolve_alignment`).

Similarly, update the `TimeStretchDecision` import:

```python
from music_assistant.controllers.streams.smart_fades.models import TimeStretchDecision
```

Remove `TimeStretchDecision` from the `time_stretch` import (keep `compensate_for_stretch`, `resolve_time_stretch`).

- [ ] **Step 6: Run all existing tests to verify nothing broke**

Run: `pytest tests/controllers/streams/smart_fades/ -v`
Expected: All existing tests PASS

- [ ] **Step 7: Run pre-commit**

Run: `pre-commit run --all-files`

- [ ] **Step 8: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/models.py \
        music_assistant/controllers/streams/smart_fades/alignment.py \
        music_assistant/controllers/streams/smart_fades/time_stretch.py \
        music_assistant/controllers/streams/smart_fades/fades.py
git commit -m "refactor: consolidate smart_fades dataclasses into models.py"
```

---

### Task 5: Implement `crossfade_params.py` — helpers and `snap_to_musical_bars`

**Files:**
- Create: `music_assistant/controllers/streams/smart_fades/crossfade_params.py`
- Modify: `tests/controllers/streams/smart_fades/test_models.py`

- [ ] **Step 1: Write failing tests for `snap_to_musical_bars` and helpers**

Add to `tests/controllers/streams/smart_fades/test_models.py`:

```python
from music_assistant.controllers.streams.smart_fades.crossfade_params import (
    snap_to_musical_bars,
    _compute_spectral_overlap,
    _extract_key,
)
from music_assistant.controllers.streams.smart_fades.models import MusicalKey


class TestSnapToMusicalBars:
    """Tests for power-of-2 bar snapping with downward bias."""

    def test_below_1_5_snaps_to_1(self) -> None:
        assert snap_to_musical_bars(0.5) == 1
        assert snap_to_musical_bars(1.0) == 1
        assert snap_to_musical_bars(1.5) == 1

    def test_between_1_5_and_3_snaps_to_2(self) -> None:
        assert snap_to_musical_bars(1.6) == 2
        assert snap_to_musical_bars(2.5) == 2
        assert snap_to_musical_bars(3.0) == 2

    def test_between_3_and_6_snaps_to_4(self) -> None:
        assert snap_to_musical_bars(3.1) == 4
        assert snap_to_musical_bars(5.0) == 4
        assert snap_to_musical_bars(6.0) == 4

    def test_between_6_and_12_snaps_to_8(self) -> None:
        assert snap_to_musical_bars(6.1) == 8
        assert snap_to_musical_bars(10.0) == 8
        assert snap_to_musical_bars(12.0) == 8

    def test_above_12_snaps_to_16(self) -> None:
        assert snap_to_musical_bars(12.1) == 16
        assert snap_to_musical_bars(20.0) == 16


class TestComputeSpectralOverlap:
    """Tests for spectral overlap calculation."""

    def test_identical_centroids_returns_1(self) -> None:
        assert _compute_spectral_overlap(2000.0, 2000.0) == 1.0

    def test_one_octave_apart_returns_0(self) -> None:
        # 1000 Hz vs 2000 Hz = one octave, log2(2) = 1.0, 1 - 1 = 0
        result = _compute_spectral_overlap(1000.0, 2000.0)
        assert abs(result - 0.0) < 0.01

    def test_two_octaves_returns_0(self) -> None:
        result = _compute_spectral_overlap(500.0, 2000.0)
        assert result == 0.0

    def test_similar_centroids_returns_high(self) -> None:
        result = _compute_spectral_overlap(1800.0, 2200.0)
        assert result > 0.5


class TestExtractKey:
    """Tests for _extract_key from raw dict."""

    def test_valid_dict_returns_musical_key(self) -> None:
        raw = {"root": "D#", "mode": "major", "confidence": 0.8}
        key = _extract_key(raw)
        assert key is not None
        assert key.root == "D#"
        assert key.mode == "major"
        assert key.confidence == 0.8

    def test_none_returns_none(self) -> None:
        assert _extract_key(None) is None

    def test_missing_fields_returns_none(self) -> None:
        assert _extract_key({"root": "C"}) is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/controllers/streams/smart_fades/test_models.py::TestSnapToMusicalBars -v`
Expected: FAIL — `ImportError`

- [ ] **Step 3: Implement the helpers in `crossfade_params.py`**

Create `music_assistant/controllers/streams/smart_fades/crossfade_params.py`:

```python
"""Unified crossfade parameter resolution.

Combines key compatibility, spectral centroid, and energy contours
into crossover frequency, fade length, and curve type decisions.
"""

from __future__ import annotations

import math
from typing import Any

from music_assistant.controllers.streams.smart_fades.models import MusicalKey


def snap_to_musical_bars(bars: float) -> int:
    """Snap fade length to musically coherent bar counts (powers of 2).

    Uses downward bias: 5 bars snaps to 4, not 8. A slightly short
    clean transition beats a slightly long problematic one.

    :param bars: Raw bar count to snap.
    """
    if bars <= 1.5:
        return 1
    if bars <= 3.0:
        return 2
    if bars <= 6.0:
        return 4
    if bars <= 12.0:
        return 8
    return 16


def _compute_spectral_overlap(centroid_a: float, centroid_b: float) -> float:
    """Compute 0-1 spectral similarity from two centroids.

    Uses log-frequency ratio since pitch perception is logarithmic.
    1.0 = identical, 0.0 = two or more octaves apart.

    :param centroid_a: Spectral centroid of track A in Hz.
    :param centroid_b: Spectral centroid of track B in Hz.
    """
    hi = max(centroid_a, centroid_b, 1.0)
    lo = max(min(centroid_a, centroid_b), 1.0)
    return max(0.0, min(1.0, 1.0 - math.log2(hi / lo)))


def _extract_key(raw: dict[str, Any] | None) -> MusicalKey | None:
    """Construct a MusicalKey from the raw musical_key dict in AudioAnalysisData.

    :param raw: Raw dict with 'root', 'mode', 'confidence' keys, or None.
    """
    if raw is None:
        return None
    try:
        return MusicalKey(
            root=raw["root"],
            mode=raw["mode"],
            confidence=raw["confidence"],
        )
    except (KeyError, TypeError):
        return None
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/controllers/streams/smart_fades/test_models.py -v`
Expected: All PASS

- [ ] **Step 5: Run pre-commit**

Run: `pre-commit run --all-files`

- [ ] **Step 6: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/crossfade_params.py \
        tests/controllers/streams/smart_fades/test_models.py
git commit -m "feat: add snap_to_musical_bars, spectral overlap, and key extraction helpers"
```

---

### Task 6: Implement `resolve_crossfade_params()` — Path A

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/crossfade_params.py`
- Create: `tests/controllers/streams/smart_fades/test_crossfade_params.py`

- [ ] **Step 1: Write failing tests for Path A**

Create `tests/controllers/streams/smart_fades/test_crossfade_params.py`:

```python
"""Tests for resolve_crossfade_params — unified crossfade decision pipeline."""

import numpy as np

from music_assistant.controllers.streams.smart_fades.crossfade_params import (
    resolve_crossfade_params,
)
from music_assistant.controllers.streams.smart_fades.models import (
    CrossfadeConfig,
    CrossfadeParams,
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
        apply=False,
        bpm_ratio=1.0 + bpm_diff_percent / 100.0,
        bpm_diff_percent=bpm_diff_percent,
        tempo_steps=None,
    )


class TestPathA:
    """Path A: BPM diff > stretch_threshold_pct (unstretched, beats drift)."""

    def test_path_a_uses_exponential_curve(self) -> None:
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

    def test_path_a_fade_between_1_5_and_2_5_seconds(self) -> None:
        fade_out = _make_analysis(bpm=120.0)
        fade_in = _make_analysis(bpm=130.0)
        stretch = _make_stretch(bpm_diff_percent=8.3)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out,
            fade_in_analysis=fade_in,
            stretch=stretch,
        )
        assert 1.5 <= result.fade_seconds <= 2.5

    def test_path_a_crossover_between_2200_and_3000(self) -> None:
        fade_out = _make_analysis(bpm=120.0)
        fade_in = _make_analysis(bpm=130.0)
        stretch = _make_stretch(bpm_diff_percent=8.3)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out,
            fade_in_analysis=fade_in,
            stretch=stretch,
        )
        assert 2200 <= result.crossover_freq <= 3000

    def test_path_a_incompatible_key_higher_crossover(self) -> None:
        fade_out = _make_analysis(
            bpm=120.0, musical_key={"root": "C", "mode": "major", "confidence": 0.9}
        )
        fade_in = _make_analysis(
            bpm=130.0, musical_key={"root": "F#", "mode": "major", "confidence": 0.9}
        )
        stretch = _make_stretch(bpm_diff_percent=8.3)
        result_bad = resolve_crossfade_params(
            fade_out_analysis=fade_out,
            fade_in_analysis=fade_in,
            stretch=stretch,
        )

        fade_in_good = _make_analysis(
            bpm=130.0, musical_key={"root": "G", "mode": "major", "confidence": 0.9}
        )
        result_good = resolve_crossfade_params(
            fade_out_analysis=fade_out,
            fade_in_analysis=fade_in_good,
            stretch=stretch,
        )
        assert result_bad.crossover_freq >= result_good.crossover_freq
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/controllers/streams/smart_fades/test_crossfade_params.py -v`
Expected: FAIL — `ImportError: cannot import name 'resolve_crossfade_params'`

- [ ] **Step 3: Implement `resolve_crossfade_params()` with Path A logic**

Add to `crossfade_params.py`:

```python
import logging

from music_assistant.controllers.streams.smart_fades.helpers import get_bpm_diff_percentage
from music_assistant.controllers.streams.smart_fades.models import (
    CrossfadeConfig,
    CrossfadeParams,
    MusicalKey,
    TimeStretchDecision,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


def resolve_crossfade_params(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    stretch: TimeStretchDecision,
    config: CrossfadeConfig | None = None,
    logger: logging.Logger | None = None,
) -> CrossfadeParams:
    """Compute unified crossfade parameters from all available signals.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param stretch: Time-stretch decision (tells us if BPM is matched).
    :param config: Tunable parameters. Uses defaults if None.
    :param logger: Optional logger for decision logging.
    """
    if config is None:
        config = CrossfadeConfig()

    key_out = _extract_key(fade_out_analysis.musical_key)
    key_in = _extract_key(fade_in_analysis.musical_key)
    key_compat = _resolve_key_compat(key_out, key_in, config)

    # Path A: unstretched (BPM diff > threshold)
    if stretch.bpm_diff_percent > config.stretch_threshold_pct:
        return _resolve_path_a(key_compat, config, logger)

    # Path B: stretched (beats aligned)
    return _resolve_path_b(
        fade_out_analysis=fade_out_analysis,
        fade_in_analysis=fade_in_analysis,
        key_compat=key_compat,
        config=config,
        logger=logger,
    )


def _resolve_key_compat(
    key_out: MusicalKey | None,
    key_in: MusicalKey | None,
    config: CrossfadeConfig,
) -> float:
    """Resolve key compatibility with confidence gate.

    :param key_out: Outgoing track's key, or None.
    :param key_in: Incoming track's key, or None.
    :param config: Crossfade configuration.
    """
    if key_out is None or key_in is None:
        return config.key_compat_neutral
    if (
        key_out.confidence < config.key_confidence_threshold
        or key_in.confidence < config.key_confidence_threshold
    ):
        return config.key_compat_neutral
    return key_out.compatibility_score(key_in)


def _resolve_path_a(
    key_compat: float,
    config: CrossfadeConfig,
    logger: logging.Logger | None,
) -> CrossfadeParams:
    """Resolve Path A: unstretched, quick timed fade.

    :param key_compat: Key compatibility score 0-1.
    :param config: Crossfade configuration.
    :param logger: Optional logger.
    """
    incompat = 1.0 - key_compat
    crossover = int(
        config.path_a_crossover_low
        + incompat * (config.path_a_crossover_high - config.path_a_crossover_low)
    )
    fade_seconds = (
        config.path_a_max_fade_sec
        + incompat * (config.path_a_min_fade_sec - config.path_a_max_fade_sec)
    )
    fade_seconds = round(fade_seconds, 2)

    if logger:
        logger.debug(
            "Crossfade params Path A: key_compat=%.2f → crossover=%dHz, fade=%.2fs, curve=exponential",
            key_compat,
            crossover,
            fade_seconds,
        )

    return CrossfadeParams(
        crossover_freq=crossover,
        fade_bars=0,
        fade_seconds=fade_seconds,
        curve_type="exponential",
        use_bar_alignment=False,
    )


def _resolve_path_b(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    key_compat: float,
    config: CrossfadeConfig,
    logger: logging.Logger | None,
) -> CrossfadeParams:
    """Resolve Path B: stretched, beats aligned. Placeholder — implemented in Task 7."""
    # Temporary: return safe defaults until Task 7 fills this in
    return CrossfadeParams(
        crossover_freq=1500,
        fade_bars=8,
        fade_seconds=16.0,
        curve_type="qsin",
        use_bar_alignment=True,
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/controllers/streams/smart_fades/test_crossfade_params.py -v`
Expected: All PASS

- [ ] **Step 5: Run pre-commit**

Run: `pre-commit run --all-files`

- [ ] **Step 6: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/crossfade_params.py \
        tests/controllers/streams/smart_fades/test_crossfade_params.py
git commit -m "feat: implement resolve_crossfade_params with Path A logic"
```

---

### Task 7: Implement `resolve_crossfade_params()` — Path B

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/crossfade_params.py`
- Modify: `tests/controllers/streams/smart_fades/test_crossfade_params.py`

- [ ] **Step 1: Write failing tests for Path B**

Add to `tests/controllers/streams/smart_fades/test_crossfade_params.py`:

```python
class TestPathBCrossover:
    """Path B crossover frequency: key urgency blend with spectral."""

    def test_compatible_key_spectral_drives_crossover(self) -> None:
        """With high key compat, spectral centroid drives crossover low."""
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
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        assert result.crossover_freq < 1500

    def test_incompatible_key_pushes_crossover_high(self) -> None:
        """With low key compat, crossover is pushed high regardless of spectral."""
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
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        assert result.crossover_freq > 2000


class TestPathBFadeLength:
    """Path B fade length: key tier -> energy -> spectral -> snap."""

    def test_compatible_key_allows_long_fade(self) -> None:
        fade_out = _make_analysis(
            musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        )
        fade_in = _make_analysis(
            musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        )
        stretch = _make_stretch(bpm_diff_percent=2.0)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        assert result.fade_bars >= 8

    def test_incompatible_key_limits_fade(self) -> None:
        fade_out = _make_analysis(
            musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        )
        fade_in = _make_analysis(
            musical_key={"root": "F#", "mode": "major", "confidence": 0.9},
        )
        stretch = _make_stretch(bpm_diff_percent=2.0)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        assert result.fade_bars <= 4

    def test_fade_bars_is_power_of_2(self) -> None:
        fade_out = _make_analysis(
            musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        )
        fade_in = _make_analysis(
            musical_key={"root": "D", "mode": "major", "confidence": 0.9},
        )
        stretch = _make_stretch(bpm_diff_percent=2.0)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        assert result.fade_bars in (1, 2, 4, 8, 16)

    def test_bar_alignment_enabled(self) -> None:
        fade_out = _make_analysis()
        fade_in = _make_analysis()
        stretch = _make_stretch(bpm_diff_percent=2.0)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        assert result.use_bar_alignment is True


class TestPathBCurveType:
    """Path B curve type: priority chain."""

    def test_incompatible_key_selects_exponential(self) -> None:
        fade_out = _make_analysis(
            musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        )
        fade_in = _make_analysis(
            musical_key={"root": "F#", "mode": "major", "confidence": 0.9},
        )
        stretch = _make_stretch(bpm_diff_percent=2.0)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        assert result.curve_type == "exponential"

    def test_default_is_qsin(self) -> None:
        fade_out = _make_analysis(
            musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        )
        fade_in = _make_analysis(
            musical_key={"root": "G", "mode": "major", "confidence": 0.9},
        )
        stretch = _make_stretch(bpm_diff_percent=2.0)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        assert result.curve_type == "qsin"


class TestGracefulDegradation:
    """Missing signals should produce safe defaults."""

    def test_no_key_uses_neutral(self) -> None:
        fade_out = _make_analysis()
        fade_in = _make_analysis()
        stretch = _make_stretch(bpm_diff_percent=2.0)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        # With neutral key compat (0.6), should get moderate fade
        assert result.fade_bars in (4, 8)
        assert result.curve_type == "qsin"

    def test_no_spectral_uses_key_only_crossover(self) -> None:
        fade_out = _make_analysis(
            musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        )
        fade_in = _make_analysis(
            musical_key={"root": "C", "mode": "major", "confidence": 0.9},
        )
        stretch = _make_stretch(bpm_diff_percent=2.0)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        assert 600 <= result.crossover_freq <= 3000

    def test_low_key_confidence_uses_neutral(self) -> None:
        fade_out = _make_analysis(
            musical_key={"root": "C", "mode": "major", "confidence": 0.2},
        )
        fade_in = _make_analysis(
            musical_key={"root": "F#", "mode": "major", "confidence": 0.2},
        )
        stretch = _make_stretch(bpm_diff_percent=2.0)
        result = resolve_crossfade_params(
            fade_out_analysis=fade_out, fade_in_analysis=fade_in, stretch=stretch,
        )
        # Low confidence → neutral compat (0.6), NOT incompatible
        assert result.fade_bars >= 4
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `pytest tests/controllers/streams/smart_fades/test_crossfade_params.py::TestPathBCrossover -v`
Expected: FAIL — placeholder `_resolve_path_b` returns fixed values

- [ ] **Step 3: Implement full `_resolve_path_b`**

Replace the placeholder `_resolve_path_b` in `crossfade_params.py` with the full implementation:

```python
def _resolve_path_b(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    key_compat: float,
    config: CrossfadeConfig,
    logger: logging.Logger | None,
) -> CrossfadeParams:
    """Resolve Path B: stretched, beats aligned.

    Three-stage pipeline: crossover -> fade length -> curve type.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param key_compat: Resolved key compatibility 0-1.
    :param config: Crossfade configuration.
    :param logger: Optional logger.
    """
    fade_in_bpm = fade_in_analysis.bpm or 120.0

    # Extract spectral centroids (average over last/first 45s)
    centroid_out = _avg_centroid(fade_out_analysis.spectral_centroid_curve, tail=True)
    centroid_in = _avg_centroid(fade_in_analysis.spectral_centroid_curve, tail=False)
    spectral_olap = _compute_spectral_overlap(centroid_out, centroid_in)

    # Extract energy slopes
    slope_out = _compute_energy_slope(fade_out_analysis.energy_curve, tail=True)
    slope_in = _compute_energy_slope(fade_in_analysis.energy_curve, tail=False)

    # Stage 1: crossover frequency
    crossover_freq = _resolve_crossover_freq(
        key_compat, centroid_out, centroid_in, config
    )

    # Stage 2: fade length (bars)
    fade_bars = _resolve_fade_bars(
        key_compat, slope_out, slope_in, spectral_olap, config
    )

    # Stage 3: curve type
    curve_type = _resolve_curve_type(
        key_compat, spectral_olap, slope_out, slope_in, fade_bars, config
    )

    bar_duration = 4.0 * 60.0 / fade_in_bpm
    fade_seconds = round(fade_bars * bar_duration, 2)

    if logger:
        logger.debug(
            "Crossfade params Path B: key_compat=%.2f, centroids=%.0f/%.0fHz, "
            "slopes=%.2f/%.2f → crossover=%dHz, fade=%dbars (%.1fs), curve=%s "
            "(spectral_overlap=%.2f)",
            key_compat,
            centroid_out,
            centroid_in,
            slope_out,
            slope_in,
            crossover_freq,
            fade_bars,
            fade_seconds,
            curve_type,
            spectral_olap,
        )

    return CrossfadeParams(
        crossover_freq=crossover_freq,
        fade_bars=fade_bars,
        fade_seconds=fade_seconds,
        curve_type=curve_type,
        use_bar_alignment=True,
    )


def _avg_centroid(
    curve: npt.NDArray[np.float32] | None, tail: bool, window: int = 45
) -> float:
    """Average spectral centroid over the tail (last N seconds) or head (first N seconds).

    :param curve: Per-second spectral centroid array, or None.
    :param tail: If True, average the last `window` seconds. Otherwise the first.
    :param window: Number of seconds to average.
    """
    if curve is None or len(curve) == 0:
        return 0.0
    segment = curve[-window:] if tail else curve[:window]
    return float(np.mean(segment))


def _compute_energy_slope(
    curve: npt.NDArray[np.float32] | None, tail: bool, window: int = 45
) -> float:
    """Compute energy gradient in the crossfade region.

    Positive = energy rising, negative = energy falling.

    :param curve: Per-second RMS energy array (normalized 0-1), or None.
    :param tail: If True, use the last `window` seconds. Otherwise the first.
    :param window: Number of seconds to analyze.
    """
    if curve is None or len(curve) < 2:
        return 0.0
    segment = curve[-window:] if tail else curve[:window]
    if len(segment) < 2:
        return 0.0
    x = np.arange(len(segment), dtype=np.float64)
    slope = float(np.polyfit(x, segment.astype(np.float64), 1)[0])
    return slope


def _resolve_crossover_freq(
    key_compat: float,
    centroid_out: float,
    centroid_in: float,
    config: CrossfadeConfig,
) -> int:
    """Blend key-driven and spectral-driven crossover frequencies.

    :param key_compat: Key compatibility 0-1.
    :param centroid_out: Average spectral centroid of outgoing track tail (Hz).
    :param centroid_in: Average spectral centroid of incoming track head (Hz).
    :param config: Crossfade configuration.
    """
    crossover_key = config.crossover_key_base + (1.0 - key_compat) * config.crossover_key_range

    if centroid_out > 0 and centroid_in > 0:
        spectral_mid = math.sqrt(centroid_out * centroid_in)
        crossover_spectral = max(
            config.crossover_min,
            min(config.crossover_max, spectral_mid * config.crossover_spectral_scale),
        )
    else:
        crossover_spectral = crossover_key

    key_urgency = max(0.0, min(1.0, (1.0 - key_compat) * config.key_urgency_steepness))
    crossover = key_urgency * crossover_key + (1.0 - key_urgency) * crossover_spectral
    return int(max(config.crossover_min, min(config.crossover_max, crossover)))


def _resolve_fade_bars(
    key_compat: float,
    slope_out: float,
    slope_in: float,
    spectral_olap: float,
    config: CrossfadeConfig,
) -> int:
    """Determine fade length in bars: key tier -> energy -> spectral -> snap.

    :param key_compat: Key compatibility 0-1.
    :param slope_out: Outgoing energy slope.
    :param slope_in: Incoming energy slope.
    :param spectral_olap: Spectral overlap 0-1.
    :param config: Crossfade configuration.
    """
    # Step 1: key tier
    if key_compat >= config.key_threshold_compatible:
        tier_min, tier_max = config.key_tier_compatible
    elif key_compat >= config.key_threshold_moderate:
        tier_min, tier_max = config.key_tier_moderate
    elif key_compat >= config.key_threshold_clashing:
        tier_min, tier_max = config.key_tier_incompatible
    else:
        tier_min, tier_max = config.key_tier_clashing

    # Step 2: energy flow picks position within tier
    energy_flow = max(-2.0, min(2.0, slope_in - slope_out))
    energy_score = (energy_flow + 2.0) / 4.0
    fade_bars_raw = tier_min + (tier_max - tier_min) * energy_score

    # Step 3: spectral overlap multiplier
    spectral_mult = (
        config.spectral_fade_mult_min
        + spectral_olap * (config.spectral_fade_mult_max - config.spectral_fade_mult_min)
    )
    fade_bars_raw *= spectral_mult

    # Step 4: snap to power-of-2
    return snap_to_musical_bars(fade_bars_raw)


def _resolve_curve_type(
    key_compat: float,
    spectral_olap: float,
    slope_out: float,
    slope_in: float,
    fade_bars: int,
    config: CrossfadeConfig,
) -> str:
    """Select curve type using priority chain.

    :param key_compat: Key compatibility 0-1.
    :param spectral_olap: Spectral overlap 0-1.
    :param slope_out: Outgoing energy slope.
    :param slope_in: Incoming energy slope.
    :param fade_bars: Resolved fade length in bars.
    :param config: Crossfade configuration.
    """
    if key_compat < config.key_compat_exp_threshold:
        return "exponential"
    if (
        spectral_olap > config.spectral_overlap_linear_threshold
        and key_compat > config.key_compat_linear_threshold
    ):
        return "linear"
    if fade_bars >= config.long_fade_linear_threshold:
        return "linear"
    if slope_out < config.energy_slope_natural_fade:
        return "logarithmic"
    if slope_in > config.energy_slope_building:
        return "exponential"
    return "qsin"
```

Add numpy imports at the top of `crossfade_params.py`:

```python
import numpy as np
import numpy.typing as npt
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/controllers/streams/smart_fades/test_crossfade_params.py -v`
Expected: All PASS

- [ ] **Step 5: Run all smart_fades tests to verify no regressions**

Run: `pytest tests/controllers/streams/smart_fades/ -v`
Expected: All PASS

- [ ] **Step 6: Run pre-commit**

Run: `pre-commit run --all-files`

- [ ] **Step 7: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/crossfade_params.py \
        tests/controllers/streams/smart_fades/test_crossfade_params.py
git commit -m "feat: implement Path B crossfade parameter resolution"
```

---

### Task 8: Integrate into `_build_filters()` in `fades.py`

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/fades.py`

- [ ] **Step 1: Replace inline heuristics in `_build_filters`**

In `fades.py`, add import:

```python
from music_assistant.controllers.streams.smart_fades.crossfade_params import (
    resolve_crossfade_params,
)
```

Replace the body of `_build_filters` from line 236 onwards. The method currently receives `alignment` and `stretch`. Add a call to `resolve_crossfade_params` and use its output:

```python
    def _build_filters(self, alignment: AlignmentResult, stretch: TimeStretchDecision) -> None:
        """Construct the filter chain from alignment and stretch decisions.

        :param alignment: Resolved and compensated alignment result.
        :param stretch: Time-stretch decision.
        """
        params = resolve_crossfade_params(
            fade_out_analysis=self.fade_out_analysis,
            fade_in_analysis=self.fade_in_analysis,
            stretch=stretch,
            logger=self.logger,
        )

        energy_aligned = alignment.strategy in ("energy", "spectral")
        crossover_freq = params.crossover_freq
        fadein_curve = params.curve_type
        fadeout_curve = params.curve_type

        # Time stretch filter
        if stretch.apply:
            if stretch.tempo_steps:
                self.filters.append(GradualTimeStretchFilter(self.logger, stretch.tempo_steps))
            else:
                self.filters.append(
                    TimeStretchFilter(logger=self.logger, stretch_ratio=stretch.bpm_ratio)
                )

        # Beat alignment trim
        if (
            alignment.fadein_start_pos is not None
            and alignment.fadein_start_pos + alignment.crossfade_duration
            <= SMART_CROSSFADE_DURATION
        ):
            self.filters.append(
                TrimFilter(logger=self.logger, fadein_start_pos=alignment.fadein_start_pos)
            )
        elif alignment.fadein_start_pos is not None:
            self.logger.log(
                VERBOSE_LOG_LEVEL,
                "Skipping beat alignment: not enough audio after trim (%.1fs + %.1fs > %.1fs)",
                alignment.fadein_start_pos,
                alignment.crossfade_duration,
                SMART_CROSSFADE_DURATION,
            )

        # Fadeout end position (energy-aligned path)
        fadeout_end_pos: float | None = None
        if energy_aligned and alignment.fadeout_start_pos is not None:
            fadeout_end_pos = alignment.fadeout_start_pos + alignment.crossfade_duration
            fadeout_end_pos = min(fadeout_end_pos, SMART_CROSSFADE_DURATION)

        # Lowpass on outgoing track
        if fadeout_end_pos is not None:
            fadeout_eq_duration = min(max(alignment.crossfade_duration * 2.5, 8.0), fadeout_end_pos)
            fadeout_eq_start = max(0, fadeout_end_pos - fadeout_eq_duration)
        else:
            fadeout_eq_duration = min(
                max(alignment.crossfade_duration * 2.5, 8.0), SMART_CROSSFADE_DURATION
            )
            fadeout_eq_start = max(0, SMART_CROSSFADE_DURATION - fadeout_eq_duration)

        self.filters.append(
            FrequencySweepFilter(
                logger=self.logger,
                sweep_type="lowpass",
                target_freq=crossover_freq,
                duration=fadeout_eq_duration,
                start_time=fadeout_eq_start,
                sweep_direction="fade_in",
                poles=1,
                curve_type=fadeout_curve,
                stream_type="fadeout",
            )
        )

        # Highpass on incoming track
        fadein_eq_duration = alignment.crossfade_duration / 1.5
        self.filters.append(
            FrequencySweepFilter(
                logger=self.logger,
                sweep_type="highpass",
                target_freq=crossover_freq,
                duration=fadein_eq_duration,
                start_time=0,
                sweep_direction="fade_out",
                poles=1,
                curve_type=fadein_curve,
                stream_type="fadein",
            )
        )

        # Trim Song A to energy knee
        if fadeout_end_pos is not None and fadeout_end_pos < SMART_CROSSFADE_DURATION:
            self.filters.append(
                FadeoutTrimFilter(logger=self.logger, fadeout_end_pos=fadeout_end_pos)
            )

        # Final crossfade
        self.filters.append(
            CrossfadeFilter(
                logger=self.logger,
                crossfade_duration=alignment.crossfade_duration,
                curve_type=alignment.curve_type,
            )
        )
```

The key changes from the original:
- Removed the inline `crossover_freq = int(np.clip(1500 + (avg_bpm - 90) * 20, 1500, 2500))` heuristic
- Removed the inline `crossfade_bars < 8` curve selection
- Replaced with `params.crossover_freq` and `params.curve_type` from `resolve_crossfade_params()`

- [ ] **Step 2: Remove unused `np` import if no longer needed**

Check if `numpy` is still used elsewhere in `fades.py`. If `np.clip` was the only usage, the import can be removed.

- [ ] **Step 3: Run all smart_fades tests**

Run: `pytest tests/controllers/streams/smart_fades/ -v`
Expected: All PASS

- [ ] **Step 4: Run pre-commit**

Run: `pre-commit run --all-files`

- [ ] **Step 5: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/fades.py
git commit -m "feat: wire resolve_crossfade_params into _build_filters"
```

---

### Task 9: Final verification and cleanup

**Files:**
- All modified files

- [ ] **Step 1: Run the full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All PASS (or only pre-existing failures unrelated to this change)

- [ ] **Step 2: Run pre-commit on all files**

Run: `pre-commit run --all-files`
Expected: All PASS

- [ ] **Step 3: Verify the module structure**

Run: `ls -la music_assistant/controllers/streams/smart_fades/`

Confirm these files exist:
- `models.py` (new — all dataclasses)
- `crossfade_params.py` (new — resolver logic)
- `alignment.py` (modified — imports from models.py)
- `time_stretch.py` (modified — imports from models.py)
- `fades.py` (modified — uses resolve_crossfade_params)
- `filters.py` (unchanged)
- `mixer.py` (unchanged)
- `helpers.py` (unchanged)

- [ ] **Step 4: Verify imports are clean**

Run: `python -c "from music_assistant.controllers.streams.smart_fades.models import MusicalKey, AlignmentResult, TimeStretchDecision, CrossfadeConfig, CrossfadeParams; print('All imports OK')"`

Run: `python -c "from music_assistant.controllers.streams.smart_fades.crossfade_params import resolve_crossfade_params, snap_to_musical_bars; print('All imports OK')"`

Expected: Both print "All imports OK"

- [ ] **Step 5: Final commit if any cleanup was needed**

```bash
git add -u
git commit -m "chore: final cleanup for unified crossfade decisions"
```
