# Fades Refactor Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Extract alignment and time-stretch logic from `SmartCrossFade._build()` into reusable modules so future fade modes (DJ Mix) can share the infrastructure.

**Architecture:** Three-phase refactor. Task 1 moves generic helpers to `crossfade_helpers.py`. Task 2 creates `alignment.py` with `AlignmentResult` dataclass and `resolve_alignment()`. Task 3 creates `time_stretch.py` with `TimeStretchDecision` and `resolve_time_stretch()`. Task 4 rewires `SmartCrossFade` to use the new modules. Task 5 is a final verification pass.

**Tech Stack:** Python 3.12+, numpy, pytest

**Design spec:** `docs/superpowers/specs/2026-03-28-fades-refactor-design.md`

---

### Task 1: Move generic helpers from fades.py to crossfade_helpers.py

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/fades.py`
- Modify: `music_assistant/controllers/streams/smart_fades/crossfade_helpers.py`
- Test: `tests/controllers/streams/smart_fades/test_crossfade_helpers.py`

- [ ] **Step 1: Add `get_bpm_diff_percentage()` to crossfade_helpers.py**

Add at the end of `crossfade_helpers.py`:

```python
def get_bpm_diff_percentage(bpm1: float, bpm2: float) -> float:
    """Calculate BPM difference percentage between two BPM values.

    :param bpm1: First BPM value.
    :param bpm2: Second BPM value.
    """
    return abs(1.0 - bpm1 / bpm2) * 100
```

- [ ] **Step 2: Add `extrapolate_downbeats()` to crossfade_helpers.py**

Move the entire `extrapolate_downbeats()` function (currently at `fades.py:897-992`) to the end of `crossfade_helpers.py`. It needs the `SMART_CROSSFADE_DURATION` import:

Add to the imports at the top of `crossfade_helpers.py`:

```python
from music_assistant.controllers.streams.smart_fades.fades import SMART_CROSSFADE_DURATION
```

Wait — that creates a circular import since `fades.py` imports from `crossfade_helpers.py`. Instead, define the constant in `crossfade_helpers.py` directly:

```python
# Buffer size in seconds for crossfade analysis
SMART_CROSSFADE_DURATION = 45
```

And update the import in `fades.py` to use it from there:

```python
from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    SMART_CROSSFADE_DURATION,
    ...
)
```

Remove the `SMART_CROSSFADE_DURATION = 45` line from `fades.py`.

Then copy the full `extrapolate_downbeats()` function (lines 897-992 of `fades.py`) to `crossfade_helpers.py`. The function signature is:

```python
def extrapolate_downbeats(
    downbeats: npt.NDArray[np.float64],
    tempo_factor: float,
    buffer_size: float = SMART_CROSSFADE_DURATION,
    bpm: float | None = None,
) -> npt.NDArray[np.float64]:
    """Extrapolate downbeats based on actual intervals when detection is incomplete.

    This is needed when we want to perform beat alignment in an 'atmospheric' outro
    that does not have any detected downbeats.

    :param downbeats: Array of detected downbeat positions in seconds.
    :param tempo_factor: Tempo adjustment factor for time stretching.
    :param buffer_size: Maximum buffer size in seconds.
    :param bpm: Optional BPM for validation when extrapolating with only 2 downbeats.
    """
```

Keep the full body exactly as-is.

- [ ] **Step 3: Write tests for moved helpers**

Add to `tests/controllers/streams/smart_fades/test_crossfade_helpers.py`:

```python
from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    extrapolate_downbeats,
    get_bpm_diff_percentage,
)


def test_get_bpm_diff_percentage_same_bpm() -> None:
    """Same BPM should give 0% diff."""
    assert get_bpm_diff_percentage(120.0, 120.0) == 0.0


def test_get_bpm_diff_percentage_5_percent() -> None:
    """5% BPM difference should return ~5."""
    result = get_bpm_diff_percentage(120.0, 126.0)
    assert 4.9 <= result <= 5.1


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
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/controllers/streams/smart_fades/test_crossfade_helpers.py -v`
Expected: ALL PASS (existing + new)

- [ ] **Step 5: Remove moved functions from fades.py**

In `fades.py`:
- Remove the `SMART_CROSSFADE_DURATION = 45` line (line 40)
- Remove the `get_bpm_diff_percentage()` function (lines 892-894)
- Remove the `extrapolate_downbeats()` function (lines 897-992)
- Add imports from `crossfade_helpers`:

```python
from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    SMART_CROSSFADE_DURATION,
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
```

- [ ] **Step 6: Run all tests to verify nothing broke**

Run: `pytest tests/controllers/streams/smart_fades/ -v`
Expected: ALL PASS

- [ ] **Step 7: Run pre-commit**

Run: `pre-commit run --all-files`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/fades.py \
       music_assistant/controllers/streams/smart_fades/crossfade_helpers.py \
       tests/controllers/streams/smart_fades/test_crossfade_helpers.py
git commit -m "refactor: move extrapolate_downbeats and get_bpm_diff_percentage to crossfade_helpers"
```

---

### Task 2: Create alignment.py with AlignmentResult and resolve_alignment()

**Files:**
- Create: `music_assistant/controllers/streams/smart_fades/alignment.py`
- Create: `tests/controllers/streams/smart_fades/test_alignment.py`
- Reference (read-only): `music_assistant/controllers/streams/smart_fades/fades.py` — the code to extract from

This task extracts `_try_energy_alignment()`, `_try_spectral_alignment()`, the bar-counting methods, and `_clamp_duration_by_bpm()` into a new `alignment.py` module. All logic comes from the current `SmartCrossFade` class in `fades.py`.

- [ ] **Step 1: Write test file for alignment module**

Create `tests/controllers/streams/smart_fades/test_alignment.py`:

```python
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
    """Helper to create AudioAnalysisData with sensible defaults."""
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
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/controllers/streams/smart_fades/test_alignment.py -v`
Expected: FAIL (module does not exist yet)

- [ ] **Step 3: Create alignment.py with AlignmentResult dataclass**

Create `music_assistant/controllers/streams/smart_fades/alignment.py`:

```python
"""Crossfade alignment resolution.

Runs the energy -> spectral -> bar-count alignment cascade and returns
an AlignmentResult with positions in source-audio time (unstretched).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    SMART_CROSSFADE_DURATION,
    calculate_energy_crossfade_duration,
    extrapolate_downbeats,
    find_fadein_entry,
    find_fadeout_start,
    find_spectral_fadein_entry,
    find_spectral_fadeout_start,
    get_bpm_diff_percentage,
    select_crossfade_curve_type,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


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

- [ ] **Step 4: Add `clamp_duration_by_bpm()` to alignment.py**

```python
def clamp_duration_by_bpm(
    duration: float,
    bpm: float,
    bpm_diff_percent: float,
    logger: logging.Logger | None = None,
) -> float:
    """Clamp crossfade duration to a BPM-aware maximum bar count.

    :param duration: Crossfade duration in seconds.
    :param bpm: BPM of the incoming track (used for bar duration).
    :param bpm_diff_percent: BPM difference percentage between tracks.
    :param logger: Optional logger for debug output.
    """
    if duration <= 0:
        return duration
    bar_duration = 4 * (60.0 / bpm)
    if bpm_diff_percent <= 5.0:
        max_bars = 16
    elif bpm_diff_percent <= 10.0:
        max_bars = 12
    else:
        max_bars = 4
    max_duration = max_bars * bar_duration
    if duration > max_duration:
        if logger:
            logger.debug(
                "Clamping duration from %.1fs to %.1fs (max %d bars at %.1f%% BPM diff)",
                duration,
                max_duration,
                max_bars,
                bpm_diff_percent,
            )
        return max_duration
    return duration
```

- [ ] **Step 5: Add `_extract_buffer_and_downbeats()` private helper**

This helper extracts the buffer-relative energy/spectral slices and downbeats that both energy and spectral alignment need. Add to `alignment.py`:

```python
def _extract_buffer_and_downbeats(
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
) -> tuple[
    float,
    npt.NDArray[np.float64],
    npt.NDArray[np.float64],
]:
    """Extract buffer-relative downbeats for both tracks.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :return: Tuple of (outro_start_offset, fadeout_downbeats_rel, fadein_downbeats_rel).
    """
    fade_out_duration = fade_out_analysis.duration or 0.0
    fade_out_downbeats = (
        fade_out_analysis.downbeats if fade_out_analysis.downbeats is not None else np.array([])
    )
    fade_in_downbeats = (
        fade_in_analysis.downbeats if fade_in_analysis.downbeats is not None else np.array([])
    )

    outro_start = max(0.0, fade_out_duration - SMART_CROSSFADE_DURATION)
    out_db_mask = fade_out_downbeats >= outro_start
    fadeout_downbeats_rel = fade_out_downbeats[out_db_mask] - outro_start

    fadein_downbeats_rel = fade_in_downbeats[fade_in_downbeats < SMART_CROSSFADE_DURATION]

    return outro_start, fadeout_downbeats_rel, fadein_downbeats_rel
```

- [ ] **Step 6: Add `_try_energy_alignment()` private function**

Extract from `SmartCrossFade._try_energy_alignment()` (fades.py:216-295). Convert from method to standalone function:

```python
def _try_energy_alignment(
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    fadeout_downbeats_rel: npt.NDArray[np.float64],
    fadein_downbeats_rel: npt.NDArray[np.float64],
    outro_start: float,
    logger: logging.Logger | None = None,
) -> AlignmentResult | None:
    """Attempt energy-contour alignment for crossfade parameters.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param fadeout_downbeats_rel: Buffer-relative downbeats for Song A.
    :param fadein_downbeats_rel: Buffer-relative downbeats for Song B.
    :param outro_start: Absolute time offset where the buffer starts in Song A.
    :param logger: Optional logger for debug output.
    """
    fade_out_energy = fade_out_analysis.energy_curve
    fade_in_energy = fade_in_analysis.energy_curve
    if fade_out_energy is None or fade_in_energy is None:
        if logger:
            logger.debug(
                "Energy alignment skipped: fade_out_energy=%s, fade_in_energy=%s",
                "present" if fade_out_energy is not None else "None",
                "present" if fade_in_energy is not None else "None",
            )
        return None

    fade_out_duration = fade_out_analysis.duration or 0.0
    buffer_secs = min(SMART_CROSSFADE_DURATION, int(fade_out_duration))
    energy_out = fade_out_energy[-buffer_secs:] if buffer_secs > 0 else fade_out_energy
    energy_in = fade_in_energy[:SMART_CROSSFADE_DURATION]

    if logger:
        logger.debug(
            "Energy alignment attempt: energy_out=%d values (range %.2f-%.2f), "
            "energy_in=%d values (range %.2f-%.2f), "
            "fadeout_downbeats=%d, fadein_downbeats=%d",
            len(energy_out),
            float(energy_out.min()) if len(energy_out) > 0 else 0,
            float(energy_out.max()) if len(energy_out) > 0 else 0,
            len(energy_in),
            float(energy_in.min()) if len(energy_in) > 0 else 0,
            float(energy_in.max()) if len(energy_in) > 0 else 0,
            len(fadeout_downbeats_rel),
            len(fadein_downbeats_rel),
        )

    fade_out_bpm = fade_out_analysis.bpm or 120.0
    fade_in_bpm = fade_in_analysis.bpm or 120.0

    fadeout_start = find_fadeout_start(energy_out, fadeout_downbeats_rel, bpm=fade_out_bpm)
    fadein_entry = find_fadein_entry(energy_in, fadein_downbeats_rel)

    if fadeout_start is None or fadein_entry is None:
        if logger:
            logger.debug(
                "Energy alignment failed: fadeout_start=%s, fadein_entry=%s",
                f"{fadeout_start:.1f}s" if fadeout_start is not None else "None (no clear decline)",
                f"{fadein_entry:.1f}s" if fadein_entry is not None else "None (no clear build)",
            )
        return None

    crossfade_duration = calculate_energy_crossfade_duration(
        energy_out=energy_out,
        fadeout_start=int(fadeout_start),
        energy_in=energy_in,
        fadein_entry=int(fadein_entry),
        bpm=fade_in_bpm,
    )

    # Select curve type based on energy slopes in overlap region
    curve_type: str | None = None
    overlap_out = energy_out[int(fadeout_start) : int(fadeout_start) + int(crossfade_duration)]
    overlap_in = energy_in[int(fadein_entry) : int(fadein_entry) + int(crossfade_duration)]
    if len(overlap_out) > 1 and len(overlap_in) > 1:
        curve_type = select_crossfade_curve_type(overlap_out, overlap_in)

    bpm_diff_percent = get_bpm_diff_percentage(fade_out_bpm, fade_in_bpm)
    crossfade_duration = clamp_duration_by_bpm(
        crossfade_duration, fade_in_bpm, bpm_diff_percent, logger
    )

    return AlignmentResult(
        strategy="energy",
        fadeout_start_pos=fadeout_start,
        fadein_start_pos=fadein_entry,
        crossfade_duration=crossfade_duration,
        curve_type=curve_type,
        fadeout_downbeats_rel=fadeout_downbeats_rel,
    )
```

- [ ] **Step 7: Add `_try_spectral_alignment()` private function**

Extract from `SmartCrossFade._try_spectral_alignment()` (fades.py:297-389):

```python
def _try_spectral_alignment(
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    fadeout_downbeats_rel: npt.NDArray[np.float64],
    fadein_downbeats_rel: npt.NDArray[np.float64],
    outro_start: float,
    logger: logging.Logger | None = None,
) -> AlignmentResult | None:
    """Attempt spectral-centroid alignment as fallback when energy alignment fails.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param fadeout_downbeats_rel: Buffer-relative downbeats for Song A.
    :param fadein_downbeats_rel: Buffer-relative downbeats for Song B.
    :param outro_start: Absolute time offset where the buffer starts in Song A.
    :param logger: Optional logger for debug output.
    """
    fade_out_spectral = fade_out_analysis.spectral_centroid_curve
    fade_in_spectral = fade_in_analysis.spectral_centroid_curve
    if fade_out_spectral is None or fade_in_spectral is None:
        if logger:
            logger.debug(
                "Spectral alignment skipped: fade_out_spectral=%s, fade_in_spectral=%s",
                "present" if fade_out_spectral is not None else "None",
                "present" if fade_in_spectral is not None else "None",
            )
        return None

    fade_out_duration = fade_out_analysis.duration or 0.0
    fade_out_bpm = fade_out_analysis.bpm or 120.0
    fade_in_bpm = fade_in_analysis.bpm or 120.0

    buffer_secs = min(SMART_CROSSFADE_DURATION, int(fade_out_duration))
    spectral_out = fade_out_spectral[-buffer_secs:] if buffer_secs > 0 else fade_out_spectral
    spectral_in = fade_in_spectral[:SMART_CROSSFADE_DURATION]

    if logger:
        logger.debug(
            "Spectral alignment attempt: spectral_out=%d values, spectral_in=%d values",
            len(spectral_out),
            len(spectral_in),
        )

    fadeout_start = find_spectral_fadeout_start(
        spectral_out, fadeout_downbeats_rel, bpm=fade_out_bpm
    )
    fadein_entry = find_spectral_fadein_entry(spectral_in, fadein_downbeats_rel)

    if fadeout_start is None or fadein_entry is None:
        if logger:
            logger.debug(
                "Spectral alignment failed: fadeout_start=%s, fadein_entry=%s",
                f"{fadeout_start:.1f}s" if fadeout_start is not None else "None",
                f"{fadein_entry:.1f}s" if fadein_entry is not None else "None",
            )
        return None

    # Use energy-based duration if energy curves available, else BPM-scaled bars
    fade_out_energy = fade_out_analysis.energy_curve
    fade_in_energy = fade_in_analysis.energy_curve
    if fade_out_energy is not None and fade_in_energy is not None:
        energy_out = fade_out_energy[-buffer_secs:] if buffer_secs > 0 else fade_out_energy
        energy_in = fade_in_energy[:SMART_CROSSFADE_DURATION]
        crossfade_duration = calculate_energy_crossfade_duration(
            energy_out=energy_out,
            fadeout_start=int(fadeout_start),
            energy_in=energy_in,
            fadein_entry=int(fadein_entry),
            bpm=fade_in_bpm,
        )
    else:
        bar_duration = 4.0 * (60.0 / fade_in_bpm)
        if fade_in_bpm < 100:
            bars = 8
        elif fade_in_bpm < 140:
            bars = 12
        else:
            bars = 16
        crossfade_duration = bars * bar_duration

    bpm_diff_percent = get_bpm_diff_percentage(fade_out_bpm, fade_in_bpm)
    crossfade_duration = clamp_duration_by_bpm(
        crossfade_duration, fade_in_bpm, bpm_diff_percent, logger
    )

    if logger:
        logger.debug(
            "Spectral alignment successful: fadeout_start=%.1fs, fadein_start=%.1fs, "
            "duration=%.1fs",
            fadeout_start,
            fadein_entry,
            crossfade_duration,
        )

    return AlignmentResult(
        strategy="spectral",
        fadeout_start_pos=fadeout_start,
        fadein_start_pos=fadein_entry,
        crossfade_duration=crossfade_duration,
        curve_type="qsin",
        fadeout_downbeats_rel=fadeout_downbeats_rel,
    )
```

- [ ] **Step 8: Add `_bar_count_alignment()` private function**

This absorbs `_calculate_optimal_crossfade_bars()`, `_calculate_optimal_fade_timing()`, `_calculate_crossfade_duration()`, and `_adjust_crossfade_to_downbeats()` from `SmartCrossFade`. Extract from fades.py:698-847:

```python
def _bar_count_alignment(
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    extrapolated_fadeout_downbeats: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> AlignmentResult:
    """Fall back to bar-counting alignment when energy/spectral both fail.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param extrapolated_fadeout_downbeats: Extrapolated downbeats for Song A.
    :param logger: Optional logger for debug output.
    """
    fade_in_bpm = fade_in_analysis.bpm or 120.0
    fade_out_bpm = fade_out_analysis.bpm or 120.0
    fade_out_beats = fade_out_analysis.beats if fade_out_analysis.beats is not None else np.array([])
    fade_in_beats = fade_in_analysis.beats if fade_in_analysis.beats is not None else np.array([])
    fade_in_downbeats = (
        fade_in_analysis.downbeats if fade_in_analysis.downbeats is not None else np.array([])
    )
    fade_out_downbeats = (
        fade_out_analysis.downbeats if fade_out_analysis.downbeats is not None else np.array([])
    )

    bpm_diff_percent = get_bpm_diff_percentage(fade_in_bpm, fade_out_bpm)

    if logger:
        logger.debug(
            "Bar-count alignment fallback (BPM diff=%.1f%%, bpm_ratio=%.3f)",
            bpm_diff_percent,
            fade_in_bpm / fade_out_bpm,
        )

    crossfade_bars = _calculate_optimal_crossfade_bars(
        fade_in_bpm=fade_in_bpm,
        fade_out_bpm=fade_out_bpm,
        extrapolated_fadeout_downbeats=extrapolated_fadeout_downbeats,
        fade_in_downbeats=fade_in_downbeats,
        fade_out_beats=fade_out_beats,
        fade_in_beats=fade_in_beats,
        logger=logger,
    )
    fadein_start_pos = _calculate_optimal_fade_timing(
        crossfade_bars=crossfade_bars,
        extrapolated_fadeout_downbeats=extrapolated_fadeout_downbeats,
        fade_in_downbeats=fade_in_downbeats,
        fade_out_beats=fade_out_beats,
        fade_in_beats=fade_in_beats,
        logger=logger,
    )
    crossfade_duration = _calculate_crossfade_duration(
        crossfade_bars=crossfade_bars,
        fade_in_bpm=fade_in_bpm,
        logger=logger,
    )

    crossfade_duration = _adjust_crossfade_to_downbeats(
        crossfade_duration=crossfade_duration,
        fadein_start_pos=fadein_start_pos,
        extrapolated_fadeout_downbeats=extrapolated_fadeout_downbeats,
        logger=logger,
    )

    # Buffer-relative downbeats for Song A
    fade_out_duration = fade_out_analysis.duration or 0.0
    outro_start = max(0.0, fade_out_duration - SMART_CROSSFADE_DURATION)
    out_db_mask = fade_out_downbeats >= outro_start
    fadeout_downbeats_rel = fade_out_downbeats[out_db_mask] - outro_start

    return AlignmentResult(
        strategy="bar_count",
        fadeout_start_pos=None,
        fadein_start_pos=fadein_start_pos,
        crossfade_duration=crossfade_duration,
        curve_type=None,
        fadeout_downbeats_rel=fadeout_downbeats_rel,
    )
```

- [ ] **Step 9: Add the bar-count helper functions**

These are direct extractions from `SmartCrossFade` (fades.py:677-847), converted from methods to standalone functions. Add to `alignment.py`:

```python
def _calculate_optimal_crossfade_bars(
    *,
    fade_in_bpm: float,
    fade_out_bpm: float,
    extrapolated_fadeout_downbeats: npt.NDArray[np.float64],
    fade_in_downbeats: npt.NDArray[np.float64],
    fade_out_beats: npt.NDArray[np.float64],
    fade_in_beats: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> int:
    """Calculate optimal crossfade bars that fit in available buffer."""
    bpm_diff_percent = get_bpm_diff_percentage(fade_in_bpm, fade_out_bpm)

    if bpm_diff_percent <= 5.0:
        ideal_bars = 10
    elif bpm_diff_percent <= 10.0:
        ideal_bars = 6
    else:
        ideal_bars = 3

    for bars in [ideal_bars, 8, 6, 4, 2, 1]:
        if bars > ideal_bars:
            continue

        fadein_start_pos = _calculate_optimal_fade_timing(
            crossfade_bars=bars,
            extrapolated_fadeout_downbeats=extrapolated_fadeout_downbeats,
            fade_in_downbeats=fade_in_downbeats,
            fade_out_beats=fade_out_beats,
            fade_in_beats=fade_in_beats,
            logger=logger,
        )
        if fadein_start_pos is None:
            continue

        test_duration = _calculate_crossfade_duration(
            crossfade_bars=bars, fade_in_bpm=fade_in_bpm, logger=logger
        )
        fadein_buffer = SMART_CROSSFADE_DURATION - fadein_start_pos
        if test_duration <= fadein_buffer:
            if bars < ideal_bars and logger:
                from music_assistant.constants import VERBOSE_LOG_LEVEL

                logger.log(
                    VERBOSE_LOG_LEVEL,
                    "Reduced crossfade from %d to %d bars (fadein buffer=%.1fs, needed=%.1fs)",
                    ideal_bars,
                    bars,
                    fadein_buffer,
                    test_duration,
                )
            return bars

    return 1


def _calculate_optimal_fade_timing(
    *,
    crossfade_bars: int,
    extrapolated_fadeout_downbeats: npt.NDArray[np.float64],
    fade_in_downbeats: npt.NDArray[np.float64],
    fade_out_beats: npt.NDArray[np.float64],
    fade_in_beats: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> float | None:
    """Calculate beat positions for alignment."""
    beats_per_bar = 4

    def _calc_beat_positions(
        out_beats: npt.NDArray[np.float64],
        in_beats: npt.NDArray[np.float64],
        num_beats: int,
    ) -> float | None:
        if len(out_beats) < num_beats or len(in_beats) < num_beats:
            return None
        return float(in_beats[:num_beats][0])

    # Try downbeats first
    result = _calc_beat_positions(
        extrapolated_fadeout_downbeats, fade_in_downbeats, crossfade_bars
    )
    if result:
        return result

    # Fall back to regular beats
    required_beats = crossfade_bars * beats_per_bar
    result = _calc_beat_positions(fade_out_beats, fade_in_beats, required_beats)
    if result:
        return result

    if logger:
        from music_assistant.constants import VERBOSE_LOG_LEVEL

        logger.log(VERBOSE_LOG_LEVEL, "No beat alignment possible (insufficient beats)")
    return None


def _calculate_crossfade_duration(
    *,
    crossfade_bars: int,
    fade_in_bpm: float,
    logger: logging.Logger | None = None,
) -> float:
    """Calculate final crossfade duration based on musical bars and BPM."""
    beats_per_bar = 4
    seconds_per_beat = 60.0 / fade_in_bpm
    musical_duration = crossfade_bars * beats_per_bar * seconds_per_beat
    actual_duration = min(musical_duration, SMART_CROSSFADE_DURATION)

    if musical_duration > SMART_CROSSFADE_DURATION and logger:
        from music_assistant.constants import VERBOSE_LOG_LEVEL

        logger.log(
            VERBOSE_LOG_LEVEL,
            "Constraining crossfade duration from %.1fs to %.1fs (buffer limit)",
            musical_duration,
            actual_duration,
        )
    return actual_duration


def _adjust_crossfade_to_downbeats(
    *,
    crossfade_duration: float,
    fadein_start_pos: float | None,
    extrapolated_fadeout_downbeats: npt.NDArray[np.float64],
    logger: logging.Logger | None = None,
) -> float:
    """Adjust crossfade duration to align with outgoing track's downbeats."""
    if len(extrapolated_fadeout_downbeats) == 0 or fadein_start_pos is None:
        return crossfade_duration

    ideal_start_pos = SMART_CROSSFADE_DURATION - crossfade_duration

    if logger:
        from music_assistant.constants import VERBOSE_LOG_LEVEL

        logger.log(
            VERBOSE_LOG_LEVEL,
            "Downbeat adjustment - ideal_start=%.2fs (buffer=%.1fs - crossfade=%.2fs), "
            "fadein_start=%.2fs",
            ideal_start_pos,
            SMART_CROSSFADE_DURATION,
            crossfade_duration,
            fadein_start_pos,
        )

    earlier_downbeat = None
    later_downbeat = None
    for downbeat in extrapolated_fadeout_downbeats:
        if downbeat <= ideal_start_pos:
            earlier_downbeat = downbeat
        elif downbeat > ideal_start_pos and later_downbeat is None:
            later_downbeat = downbeat
            break

    if earlier_downbeat is not None:
        adjusted_duration = float(SMART_CROSSFADE_DURATION - earlier_downbeat)
        if fadein_start_pos + adjusted_duration <= SMART_CROSSFADE_DURATION:
            if abs(adjusted_duration - crossfade_duration) > 0.1 and logger:
                from music_assistant.constants import VERBOSE_LOG_LEVEL

                logger.log(
                    VERBOSE_LOG_LEVEL,
                    "Adjusted crossfade duration from %.2fs to %.2fs (earlier downbeat at %.2fs)",
                    crossfade_duration,
                    adjusted_duration,
                    earlier_downbeat,
                )
            return adjusted_duration

    if later_downbeat is not None:
        adjusted_duration = float(SMART_CROSSFADE_DURATION - later_downbeat)
        if fadein_start_pos + adjusted_duration <= SMART_CROSSFADE_DURATION:
            if abs(adjusted_duration - crossfade_duration) > 0.1 and logger:
                from music_assistant.constants import VERBOSE_LOG_LEVEL

                logger.log(
                    VERBOSE_LOG_LEVEL,
                    "Adjusted crossfade duration from %.2fs to %.2fs (later downbeat at %.2fs)",
                    crossfade_duration,
                    adjusted_duration,
                    later_downbeat,
                )
            return adjusted_duration

    if logger:
        from music_assistant.constants import VERBOSE_LOG_LEVEL

        logger.log(
            VERBOSE_LOG_LEVEL,
            "Could not adjust crossfade duration to downbeats, using original %.2fs",
            crossfade_duration,
        )
    return crossfade_duration
```

- [ ] **Step 10: Add `resolve_alignment()` public function**

```python
def resolve_alignment(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    logger: logging.Logger | None = None,
) -> AlignmentResult:
    """Resolve crossfade alignment using energy -> spectral -> bar-count cascade.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param logger: Optional logger for debug output.
    :return: AlignmentResult with positions in source-audio time.
    """
    outro_start, fadeout_downbeats_rel, fadein_downbeats_rel = _extract_buffer_and_downbeats(
        fade_out_analysis, fade_in_analysis
    )

    # 1. Try energy-contour alignment (preferred)
    result = _try_energy_alignment(
        fade_out_analysis,
        fade_in_analysis,
        fadeout_downbeats_rel,
        fadein_downbeats_rel,
        outro_start,
        logger,
    )
    if result is not None:
        return result

    # 2. Try spectral-centroid alignment (fallback)
    if logger:
        logger.debug("Energy alignment failed, trying spectral-centroid alignment")
    result = _try_spectral_alignment(
        fade_out_analysis,
        fade_in_analysis,
        fadeout_downbeats_rel,
        fadein_downbeats_rel,
        outro_start,
        logger,
    )
    if result is not None:
        return result

    # 3. Bar-counting fallback (always succeeds)
    if logger:
        logger.debug("Energy and spectral alignment failed, falling back to bar-count alignment")
    extrapolated = extrapolate_downbeats(
        fade_out_analysis.downbeats if fade_out_analysis.downbeats is not None else np.array([]),
        tempo_factor=1.0,
        bpm=fade_out_analysis.bpm,
    )
    return _bar_count_alignment(
        fade_out_analysis,
        fade_in_analysis,
        extrapolated,
        logger,
    )
```

- [ ] **Step 11: Run tests**

Run: `pytest tests/controllers/streams/smart_fades/test_alignment.py -v`
Expected: ALL PASS

- [ ] **Step 12: Run pre-commit**

Run: `pre-commit run --all-files`
Expected: PASS

- [ ] **Step 13: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/alignment.py \
       tests/controllers/streams/smart_fades/test_alignment.py
git commit -m "feat: add alignment.py with AlignmentResult and resolve_alignment()"
```

---

### Task 3: Create time_stretch.py with TimeStretchDecision and resolve_time_stretch()

**Files:**
- Create: `music_assistant/controllers/streams/smart_fades/time_stretch.py`
- Create: `tests/controllers/streams/smart_fades/test_time_stretch.py`
- Reference (read-only): `music_assistant/controllers/streams/smart_fades/fades.py:476-532` — the code to extract from

- [ ] **Step 1: Write test file**

Create `tests/controllers/streams/smart_fades/test_time_stretch.py`:

```python
"""Tests for time-stretch decision and alignment compensation."""

import numpy as np

from music_assistant.controllers.streams.smart_fades.alignment import AlignmentResult
from music_assistant.controllers.streams.smart_fades.time_stretch import (
    TimeStretchDecision,
    compensate_for_stretch,
    resolve_time_stretch,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


def _make_analysis(bpm: float = 120.0, duration: float = 180.0) -> AudioAnalysisData:
    """Helper to create AudioAnalysisData with sensible defaults."""
    beats = np.arange(0, duration, 60.0 / bpm)
    downbeats = beats[::4]
    return AudioAnalysisData(
        bpm=bpm,
        beats=beats,
        downbeats=downbeats,
        duration=duration,
    )


def _make_alignment(
    fadeout_start_pos: float | None = 20.0,
    fadein_start_pos: float | None = 5.0,
    crossfade_duration: float = 16.0,
) -> AlignmentResult:
    """Helper to create AlignmentResult with sensible defaults."""
    return AlignmentResult(
        strategy="energy",
        fadeout_start_pos=fadeout_start_pos,
        fadein_start_pos=fadein_start_pos,
        crossfade_duration=crossfade_duration,
        curve_type="qsin",
        fadeout_downbeats_rel=np.arange(0, 45, 2.0),
    )


def test_resolve_time_stretch_within_threshold() -> None:
    """BPM diff within threshold should produce stretch decision with steps."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=124.0)  # ~3.3% diff
    alignment = _make_alignment()

    result = resolve_time_stretch(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        alignment=alignment,
    )

    assert result.apply is True
    assert abs(result.bpm_ratio - 124.0 / 120.0) < 0.001
    assert result.tempo_steps is not None
    assert len(result.tempo_steps) > 0


def test_resolve_time_stretch_above_threshold() -> None:
    """BPM diff above threshold should not apply stretch."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=140.0)  # ~16.7% diff, well above 5%
    alignment = _make_alignment()

    result = resolve_time_stretch(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        alignment=alignment,
    )

    assert result.apply is False


def test_resolve_time_stretch_negligible_diff() -> None:
    """Negligible BPM diff should not apply stretch."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=120.05)  # ~0.04% diff
    alignment = _make_alignment()

    result = resolve_time_stretch(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        alignment=alignment,
    )

    assert result.apply is False


def test_resolve_time_stretch_custom_threshold() -> None:
    """Custom threshold should be respected."""
    fade_out = _make_analysis(bpm=120.0)
    fade_in = _make_analysis(bpm=130.0)  # ~8.3% diff
    alignment = _make_alignment()

    result = resolve_time_stretch(
        fade_out_analysis=fade_out,
        fade_in_analysis=fade_in,
        alignment=alignment,
        threshold_percent=10.0,  # Higher threshold for DJ mix
    )

    assert result.apply is True


def test_compensate_for_stretch_adjusts_fadeout() -> None:
    """Compensation should divide fadeout_start_pos by bpm_ratio."""
    alignment = _make_alignment(fadeout_start_pos=20.0)
    stretch = TimeStretchDecision(
        apply=True,
        bpm_ratio=0.95,  # Slowing down
        bpm_diff_percent=5.0,
        tempo_steps=[(0.0, 1.0), (10.0, 0.95)],
    )

    result = compensate_for_stretch(alignment, stretch)

    assert result.fadeout_start_pos is not None
    assert abs(result.fadeout_start_pos - 20.0 / 0.95) < 0.01
    # fadein should be unchanged
    assert result.fadein_start_pos == alignment.fadein_start_pos
    # crossfade_duration should be unchanged
    assert result.crossfade_duration == alignment.crossfade_duration


def test_compensate_for_stretch_preserves_fadein() -> None:
    """Compensation should not touch fadein_start_pos."""
    alignment = _make_alignment(fadein_start_pos=5.0)
    stretch = TimeStretchDecision(
        apply=True, bpm_ratio=1.05, bpm_diff_percent=5.0, tempo_steps=[]
    )

    result = compensate_for_stretch(alignment, stretch)

    assert result.fadein_start_pos == 5.0


def test_compensate_no_stretch() -> None:
    """No stretch should return alignment unchanged."""
    alignment = _make_alignment(fadeout_start_pos=20.0)
    stretch = TimeStretchDecision(
        apply=False, bpm_ratio=1.0, bpm_diff_percent=0.0, tempo_steps=None
    )

    result = compensate_for_stretch(alignment, stretch)

    assert result.fadeout_start_pos == 20.0
```

- [ ] **Step 2: Run tests to confirm they fail**

Run: `pytest tests/controllers/streams/smart_fades/test_time_stretch.py -v`
Expected: FAIL (module does not exist yet)

- [ ] **Step 3: Create time_stretch.py**

Create `music_assistant/controllers/streams/smart_fades/time_stretch.py`:

```python
"""Time-stretch decision and alignment compensation.

Decides whether to apply time stretching based on BPM difference,
and compensates alignment positions for the stretch ratio.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace

import numpy as np
import numpy.typing as npt

from music_assistant.controllers.streams.smart_fades.alignment import AlignmentResult
from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    SMART_CROSSFADE_DURATION,
    compute_gradual_tempo_steps,
    get_bpm_diff_percentage,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


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


def resolve_time_stretch(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    alignment: AlignmentResult,
    threshold_percent: float = 5.0,
    stretch_duration: float = 10.0,
    logger: logging.Logger | None = None,
) -> TimeStretchDecision:
    """Decide whether and how to apply time stretching.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param alignment: Resolved alignment result.
    :param threshold_percent: Max BPM diff (%) for time stretching. Default 5%.
    :param stretch_duration: How long (seconds) the gradual tempo ramp takes.
    :param logger: Optional logger for debug output.
    """
    fade_out_bpm = fade_out_analysis.bpm or 120.0
    fade_in_bpm = fade_in_analysis.bpm or 120.0
    bpm_ratio = fade_in_bpm / fade_out_bpm
    bpm_diff_percent = get_bpm_diff_percentage(fade_out_bpm, fade_in_bpm)

    no_stretch = TimeStretchDecision(
        apply=False,
        bpm_ratio=bpm_ratio,
        bpm_diff_percent=bpm_diff_percent,
        tempo_steps=None,
    )

    # Only stretch if diff is meaningful but within threshold
    energy_aligned = alignment.strategy in ("energy", "spectral")
    if not (0.1 < bpm_diff_percent <= threshold_percent):
        return no_stretch

    # For bar-count alignment, only stretch if we have enough bars
    if not energy_aligned:
        # Need crossfade_bars > 4 for stretch to make sense in bar-count mode
        bar_duration = 4 * (60.0 / fade_in_bpm)
        crossfade_bars = int(alignment.crossfade_duration / bar_duration) if bar_duration > 0 else 0
        if crossfade_bars <= 4:
            return no_stretch

    fade_out_beats = (
        fade_out_analysis.beats if fade_out_analysis.beats is not None else np.array([])
    )
    fade_out_duration = fade_out_analysis.duration or 0.0

    # Select timestamps for S-curve steps:
    # >3% BPM diff: use beat-level stepping (more steps = smoother)
    # <=3%: use downbeat-level stepping (fewer steps sufficient)
    if bpm_diff_percent > 3.0:
        if energy_aligned:
            buffer_start = max(0, fade_out_duration - SMART_CROSSFADE_DURATION)
            stretch_timestamps = (
                fade_out_beats[fade_out_beats >= buffer_start] - buffer_start
            )
        else:
            stretch_timestamps = fade_out_beats[fade_out_beats < SMART_CROSSFADE_DURATION]
    else:
        stretch_timestamps = alignment.fadeout_downbeats_rel

    # Limit timestamps to stretch_duration window
    stretch_timestamps = stretch_timestamps[stretch_timestamps <= stretch_duration]

    if bpm_diff_percent > 0.5 and len(stretch_timestamps) >= 4:
        tempo_steps = compute_gradual_tempo_steps(
            start_ratio=1.0,
            end_ratio=bpm_ratio,
            downbeats=stretch_timestamps,
        )
        if tempo_steps:
            return TimeStretchDecision(
                apply=True,
                bpm_ratio=bpm_ratio,
                bpm_diff_percent=bpm_diff_percent,
                tempo_steps=tempo_steps,
            )

    # Fallback: instant stretch (no gradual steps possible)
    return TimeStretchDecision(
        apply=True,
        bpm_ratio=bpm_ratio,
        bpm_diff_percent=bpm_diff_percent,
        tempo_steps=None,
    )


def compensate_for_stretch(
    alignment: AlignmentResult,
    stretch: TimeStretchDecision,
) -> AlignmentResult:
    """Adjust alignment positions for time-stretching.

    Divides fadeout_start_pos by bpm_ratio when stretching is applied.
    fadein_start_pos and crossfade_duration are left in Song B's time domain.

    :param alignment: Alignment result with positions in source-audio time.
    :param stretch: Time-stretch decision.
    :return: New AlignmentResult with compensated positions.
    """
    if not stretch.apply or alignment.fadeout_start_pos is None:
        return alignment

    return replace(
        alignment,
        fadeout_start_pos=alignment.fadeout_start_pos / stretch.bpm_ratio,
    )
```

- [ ] **Step 4: Run tests**

Run: `pytest tests/controllers/streams/smart_fades/test_time_stretch.py -v`
Expected: ALL PASS

- [ ] **Step 5: Run pre-commit**

Run: `pre-commit run --all-files`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/time_stretch.py \
       tests/controllers/streams/smart_fades/test_time_stretch.py
git commit -m "feat: add time_stretch.py with TimeStretchDecision and resolve_time_stretch()"
```

---

### Task 4: Rewire SmartCrossFade to use new modules

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/fades.py`
- Test: `tests/controllers/streams/smart_fades/test_crossfade_helpers.py` (existing, verify still passes)
- Test: `tests/controllers/streams/smart_fades/test_alignment.py` (existing, verify still passes)
- Test: `tests/controllers/streams/smart_fades/test_time_stretch.py` (existing, verify still passes)

- [ ] **Step 1: Update SmartCrossFade constructor to store analysis objects**

In `fades.py`, replace the `SmartCrossFade.__init__()` method (lines 173-214) with:

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
    self.fade_out_analysis = fade_out_analysis
    self.fade_in_analysis = fade_in_analysis
    super().__init__(logger)
```

- [ ] **Step 2: Replace _build() with orchestration using new modules**

Replace the entire `_build()` method and all the private methods that moved to `alignment.py`. The new `_build()`:

```python
def _build(self) -> None:
    """Build the smart fades filter chain."""
    alignment = resolve_alignment(
        fade_out_analysis=self.fade_out_analysis,
        fade_in_analysis=self.fade_in_analysis,
        logger=self.logger,
    )

    stretch = resolve_time_stretch(
        fade_out_analysis=self.fade_out_analysis,
        fade_in_analysis=self.fade_in_analysis,
        alignment=alignment,
        logger=self.logger,
    )

    alignment = compensate_for_stretch(alignment, stretch)

    if stretch.apply:
        self.logger.debug(
            "Adjusted energy fadeout_start for time stretch: %.1fs (ratio=%.4f)",
            alignment.fadeout_start_pos or -1,
            stretch.bpm_ratio,
        )

    self._build_filters(alignment, stretch)

    self.logger.info(
        "Smart crossfade: %s BPM, strategy=%s, fadeout_start=%.1fs, "
        "fadein_entry=%.1fs, duration=%.1fs, curve=%s",
        f"{self.fade_out_analysis.bpm:.0f}->{self.fade_in_analysis.bpm:.0f}",
        alignment.strategy,
        alignment.fadeout_start_pos if alignment.fadeout_start_pos is not None else -1,
        alignment.fadein_start_pos or -1,
        alignment.crossfade_duration or -1,
        alignment.curve_type or "default",
    )
```

- [ ] **Step 3: Add _build_filters() method**

This is the filter chain construction extracted from the second half of the old `_build()`. Add to `SmartCrossFade`:

```python
def _build_filters(
    self, alignment: AlignmentResult, stretch: TimeStretchDecision
) -> None:
    """Construct the filter chain from alignment and stretch decisions.

    :param alignment: Resolved and compensated alignment result.
    :param stretch: Time-stretch decision.
    """
    energy_aligned = alignment.strategy in ("energy", "spectral")
    fade_out_bpm = self.fade_out_analysis.bpm or 120.0
    fade_in_bpm = self.fade_in_analysis.bpm or 120.0

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

    # Downbeat adjustment (bar-count path only)
    # Already handled inside resolve_alignment for bar_count strategy

    # EQ crossover frequency
    avg_bpm = (fade_out_bpm + fade_in_bpm) / 2
    crossover_freq = int(np.clip(1500 + (avg_bpm - 90) * 20, 1500, 2500))
    if abs(stretch.bpm_ratio - 1.0) > 0.3:
        crossover_freq = int(crossover_freq * 0.85)

    # Determine crossfade_bars for curve selection
    bar_duration = 4 * (60.0 / fade_in_bpm)
    crossfade_bars = int(alignment.crossfade_duration / bar_duration) if bar_duration > 0 else 0

    if crossfade_bars < 8:
        fadeout_curve = "exponential"
        fadein_curve = "exponential"
    else:
        fadeout_curve = "logarithmic"
        fadein_curve = "linear"

    # Fadeout end position (energy-aligned path)
    fadeout_end_pos: float | None = None
    if energy_aligned and alignment.fadeout_start_pos is not None:
        fadeout_end_pos = alignment.fadeout_start_pos + alignment.crossfade_duration
        fadeout_end_pos = min(fadeout_end_pos, SMART_CROSSFADE_DURATION)

    # Lowpass on outgoing track
    if fadeout_end_pos is not None:
        fadeout_eq_duration = min(
            max(alignment.crossfade_duration * 2.5, 8.0), fadeout_end_pos
        )
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

- [ ] **Step 4: Remove all moved methods from SmartCrossFade**

Delete these methods from `SmartCrossFade` (they now live in `alignment.py`):
- `_try_energy_alignment()`
- `_try_spectral_alignment()`
- `_clamp_duration_by_bpm()`
- `_calculate_crossfade_duration()`
- `_calculate_optimal_crossfade_bars()`
- `_calculate_optimal_fade_timing()`
- `_adjust_crossfade_to_downbeats()`

Also remove the now-unused `extrapolate_downbeats()` and `get_bpm_diff_percentage()` functions (already moved in Task 1).

- [ ] **Step 5: Update imports in fades.py**

The new imports should be:

```python
from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import TYPE_CHECKING

import aiofiles
import numpy as np
import shortuuid

from music_assistant.constants import VERBOSE_LOG_LEVEL
from music_assistant.controllers.streams.smart_fades.alignment import (
    AlignmentResult,
    resolve_alignment,
)
from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    SMART_CROSSFADE_DURATION,
)
from music_assistant.controllers.streams.smart_fades.filters import (
    CrossfadeFilter,
    FadeoutTrimFilter,
    Filter,
    FrequencySweepFilter,
    GradualTimeStretchFilter,
    TimeStretchFilter,
    TrimFilter,
)
from music_assistant.controllers.streams.smart_fades.time_stretch import (
    TimeStretchDecision,
    compensate_for_stretch,
    resolve_time_stretch,
)
from music_assistant.helpers.process import communicate
from music_assistant.helpers.util import remove_file
from music_assistant.models.audio_analysis import AudioAnalysisData

if TYPE_CHECKING:
    from music_assistant_models.media_items import AudioFormat
```

Remove unused imports: `numpy.typing`, `npt`, the old crossfade_helpers imports.

- [ ] **Step 6: Run ALL tests**

Run: `pytest tests/controllers/streams/smart_fades/ -v`
Expected: ALL PASS

- [ ] **Step 7: Run pre-commit**

Run: `pre-commit run --all-files`
Expected: PASS

- [ ] **Step 8: Commit**

```bash
git add music_assistant/controllers/streams/smart_fades/fades.py
git commit -m "refactor: rewire SmartCrossFade to use alignment.py and time_stretch.py"
```

---

### Task 5: Final verification and cleanup

**Files:**
- All files in `music_assistant/controllers/streams/smart_fades/`
- All test files

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 2: Run pre-commit on all files**

Run: `pre-commit run --all-files`
Expected: PASS

- [ ] **Step 3: Verify fades.py line count**

Run: `wc -l music_assistant/controllers/streams/smart_fades/fades.py`
Expected: ~250 lines (down from ~990)

- [ ] **Step 4: Verify design spec compliance**

Read `docs/superpowers/specs/2026-03-28-fades-refactor-design.md` and check:
- `AlignmentResult` dataclass matches spec (fields: strategy, fadeout_start_pos, fadein_start_pos, crossfade_duration, curve_type, fadeout_downbeats_rel)
- `resolve_alignment()` signature matches spec (fade_out_analysis, fade_in_analysis, logger)
- `TimeStretchDecision` dataclass matches spec (fields: apply, bpm_ratio, bpm_diff_percent, tempo_steps)
- `resolve_time_stretch()` signature matches spec (fade_out_analysis, fade_in_analysis, alignment, threshold_percent=5.0, stretch_duration=10.0, logger)
- `compensate_for_stretch()` signature matches spec (alignment, stretch)
- `SmartCrossFade._build()` is orchestration-only (~15 lines)
- `SmartCrossFade._build_filters()` receives AlignmentResult and TimeStretchDecision
- No changes to `filters.py`, `mixer.py`, `__init__.py`
- `extrapolate_downbeats()` and `get_bpm_diff_percentage()` live in `crossfade_helpers.py`
- `SMART_CROSSFADE_DURATION` constant lives in `crossfade_helpers.py`

- [ ] **Step 5: Verify no circular imports**

Run: `python -c "from music_assistant.controllers.streams.smart_fades import SmartFadesMixer; print('OK')"`
Expected: `OK`

- [ ] **Step 6: Commit any cleanup**

If any cleanup was needed:
```bash
git add -A
git commit -m "chore: final cleanup for fades refactor"
```
