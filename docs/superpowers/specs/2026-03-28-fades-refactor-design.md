# Smart Fades Refactor: Extract Alignment and Time-Stretch Logic

**Date:** 2026-03-28
**Branch:** `smart-crossfade-improvements`
**Goal:** Refactor `fades.py` to extract reusable alignment and time-stretch logic so that future fade modes (DJ Mix) can share the infrastructure without duplicating code.

## Problem

`SmartCrossFade._build()` is a ~250-line method that mixes three concerns:

1. **Alignment strategy selection** (energy -> spectral -> bar-counting fallback cascade)
2. **Time-stretch decision and position compensation**
3. **Filter chain construction** (EQ sweeps, trims, crossfade)

The alignment methods (`_try_energy_alignment`, `_try_spectral_alignment`) share structural overlap (buffer extraction, downbeat coordinate transforms, same return shape). The bar-counting fallback is spread across four private methods. All of this is coupled to `SmartCrossFade` via `self`, making it impossible for a future `DJMixFade(SmartFade)` to reuse the alignment or stretching logic.

## Design

### New file: `alignment.py`

A new module in `music_assistant/controllers/streams/smart_fades/alignment.py` containing:

#### `AlignmentResult` dataclass

Replaces the 6-tuple currently returned by `_try_energy_alignment` and `_try_spectral_alignment`.

```python
@dataclass
class AlignmentResult:
    strategy: str                           # "energy", "spectral", "bar_count"
    fadeout_start_pos: float | None         # Song A position (source-audio time, unstretched)
    fadein_start_pos: float | None          # Song B position
    crossfade_duration: float               # Duration in seconds
    curve_type: str | None                  # "qsin", "tri", or None (default)
    fadeout_downbeats_rel: npt.NDArray[np.float64]  # Buffer-relative downbeats for Song A
```

All positions are in **source-audio time** (unstretched). Compensation for time-stretching happens separately.

#### `resolve_alignment()` function

Top-level function that runs the full alignment cascade. Takes explicit parameters instead of reaching into `self`:

```python
def resolve_alignment(
    *,
    fade_out_energy: npt.NDArray[np.float32] | None,
    fade_in_energy: npt.NDArray[np.float32] | None,
    fade_out_spectral: npt.NDArray[np.float32] | None,
    fade_in_spectral: npt.NDArray[np.float32] | None,
    fade_out_downbeats: npt.NDArray[np.float64],
    fade_in_downbeats: npt.NDArray[np.float64],
    fade_out_beats: npt.NDArray[np.float64],
    fade_in_beats: npt.NDArray[np.float64],
    fade_out_bpm: float,
    fade_in_bpm: float,
    fade_out_duration: float,
    fade_in_duration: float,
    extrapolated_fadeout_downbeats: npt.NDArray[np.float64],
    buffer_duration: float = SMART_CROSSFADE_DURATION,
    logger: logging.Logger | None = None,
) -> AlignmentResult:
```

Internally calls three private functions in order:
1. `_try_energy_alignment(...)` -> `AlignmentResult | None`
2. `_try_spectral_alignment(...)` -> `AlignmentResult | None`
3. `_bar_count_alignment(...)` -> `AlignmentResult` (always succeeds)

Each returns an `AlignmentResult` or `None`. The cascade stops at the first success.

The bar-counting fallback absorbs the current `_calculate_optimal_crossfade_bars()`, `_calculate_optimal_fade_timing()`, `_calculate_crossfade_duration()`, and `_adjust_crossfade_to_downbeats()` logic from `SmartCrossFade`.

#### `clamp_duration_by_bpm()` utility function

Extracted from `SmartCrossFade._clamp_duration_by_bpm()`. Pure function:

```python
def clamp_duration_by_bpm(
    duration: float,
    bpm: float,
    bpm_diff_percent: float,
    logger: logging.Logger | None = None,
) -> float:
```

Called by `resolve_alignment()` after energy/spectral alignment succeeds, and available to future fade modes that may want different thresholds.

### New file: `time_stretch.py`

A new module in `music_assistant/controllers/streams/smart_fades/time_stretch.py` containing:

#### `TimeStretchDecision` dataclass

```python
@dataclass
class TimeStretchDecision:
    apply: bool                                     # Whether to apply time stretching
    bpm_ratio: float                                # fade_in_bpm / fade_out_bpm
    bpm_diff_percent: float                         # abs(1 - ratio) * 100
    tempo_steps: list[tuple[float, float]] | None   # S-curve steps for gradual stretch, or None for instant
```

#### `resolve_time_stretch()` function

Extracts the time-stretch decision logic from `_build()`:

```python
def resolve_time_stretch(
    *,
    fade_out_bpm: float,
    fade_in_bpm: float,
    fade_out_beats: npt.NDArray[np.float64],
    fade_out_downbeats_rel: npt.NDArray[np.float64],
    fade_out_duration: float,
    alignment: AlignmentResult,
    threshold_percent: float = 8.0,
    buffer_duration: float = SMART_CROSSFADE_DURATION,
    logger: logging.Logger | None = None,
) -> TimeStretchDecision:
```

Handles:
- BPM ratio and diff calculation
- Threshold check (0.1% < diff <= threshold)
- Beat-level vs downbeat-level timestamp selection (>3% vs <=3%)
- Calling `compute_gradual_tempo_steps()` when appropriate
- Returns `TimeStretchDecision` with `apply=False` when stretching is not needed

#### `compensate_for_stretch()` function

Pure function that adjusts alignment positions for time-stretching:

```python
def compensate_for_stretch(
    alignment: AlignmentResult,
    stretch: TimeStretchDecision,
) -> AlignmentResult:
```

If `stretch.apply` is True and `alignment.fadeout_start_pos` is not None, divides `fadeout_start_pos` by `stretch.bpm_ratio`. Returns a new `AlignmentResult` (immutable pattern). Does not touch `fadein_start_pos` or `crossfade_duration` (Song B's time domain, per existing design).

### Changes to `fades.py`

#### `SmartCrossFade._build()` becomes orchestration-only

```python
def _build(self) -> None:
    self.extrapolated_fadeout_downbeats = extrapolate_downbeats(...)

    alignment = resolve_alignment(
        fade_out_energy=self.fade_out_energy,
        fade_in_energy=self.fade_in_energy,
        # ... all explicit parameters ...
    )

    stretch = resolve_time_stretch(
        fade_out_bpm=self.fade_out_bpm,
        fade_in_bpm=self.fade_in_bpm,
        alignment=alignment,
        # ... explicit parameters ...
    )

    alignment = compensate_for_stretch(alignment, stretch)

    self._build_filters(alignment, stretch)
```

~15-20 lines of orchestration instead of ~250.

#### `SmartCrossFade._build_filters()` new method

Receives `AlignmentResult` and `TimeStretchDecision`, constructs the filter chain. This is the part that remains SmartCrossFade-specific (DJ mix will override it for different EQ behavior, more aggressive curves, etc.):

```python
def _build_filters(
    self, alignment: AlignmentResult, stretch: TimeStretchDecision
) -> None:
```

Contains:
- Time-stretch filter instantiation (from `TimeStretchDecision`)
- TrimFilter (from `alignment.fadein_start_pos`)
- EQ crossover frequency calculation and FrequencySweepFilter creation
- FadeoutTrimFilter (from `alignment.fadeout_start_pos` + `alignment.crossfade_duration`)
- CrossfadeFilter (from `alignment.crossfade_duration` + `alignment.curve_type`)
- Downbeat re-extrapolation after stretch

This method is ~100 lines (the current filter chain section of `_build()`), which is reasonable for a single method that builds a 4-6 filter chain with conditional logic.

#### Other changes to `fades.py`

- Remove `_try_energy_alignment()`, `_try_spectral_alignment()` (moved to `alignment.py`)
- Remove `_clamp_duration_by_bpm()` (moved to `alignment.py`)
- Remove `_calculate_optimal_crossfade_bars()`, `_calculate_optimal_fade_timing()`, `_calculate_crossfade_duration()`, `_adjust_crossfade_to_downbeats()` (absorbed into bar-count alignment in `alignment.py`)
- Keep `SmartFade` base class, `StandardCrossFade`, `extrapolate_downbeats()`, `get_bpm_diff_percentage()`

### No changes to these files

- **`filters.py`** -- already clean, no changes needed
- **`crossfade_helpers.py`** -- functions stay where they are; `alignment.py` calls into them
- **`mixer.py`** -- public API unchanged; still instantiates `SmartCrossFade(logger, analysis_out, analysis_in)`
- **`__init__.py`** -- only exports `SmartFadesMixer`, unchanged
- **`models/audio_analysis.py`** -- data model unchanged

### File structure after refactor

```
music_assistant/controllers/streams/smart_fades/
    __init__.py            (unchanged)
    mixer.py               (unchanged)
    fades.py               (~350 lines, down from ~990)
    filters.py             (unchanged, ~307 lines)
    crossfade_helpers.py   (unchanged, ~619 lines)
    alignment.py           (NEW, ~400 lines)
    time_stretch.py        (NEW, ~120 lines)
```

## How future DJMixFade reuses this

```python
class DJMixFade(SmartFade):
    time_stretch_bpm_percentage_threshold: float = 12.0  # more aggressive

    def _build(self) -> None:
        alignment = resolve_alignment(...)          # same cascade
        stretch = resolve_time_stretch(
            ...,
            threshold_percent=self.time_stretch_bpm_percentage_threshold,
        )
        alignment = compensate_for_stretch(alignment, stretch)
        self._build_filters(alignment, stretch)     # different filter chain

    def _build_filters(self, alignment, stretch) -> None:
        # More aggressive EQ, different curves, harder cuts, etc.
        ...
```

DJMixFade gets alignment and stretching for free. Only the filter chain differs.

## What moves where

| Current location | What | Destination |
|---|---|---|
| `fades.py` `_try_energy_alignment()` | Energy alignment orchestration | `alignment.py` `_try_energy_alignment()` |
| `fades.py` `_try_spectral_alignment()` | Spectral alignment orchestration | `alignment.py` `_try_spectral_alignment()` |
| `fades.py` `_calculate_optimal_crossfade_bars()` | Bar-count ideal bars | `alignment.py` `_bar_count_alignment()` |
| `fades.py` `_calculate_optimal_fade_timing()` | Beat position calculation | `alignment.py` (internal to bar-count) |
| `fades.py` `_calculate_crossfade_duration()` | Bars-to-seconds conversion | `alignment.py` (internal to bar-count) |
| `fades.py` `_adjust_crossfade_to_downbeats()` | Downbeat snapping (fallback) | `alignment.py` (internal to bar-count) |
| `fades.py` `_clamp_duration_by_bpm()` | Duration clamping | `alignment.py` `clamp_duration_by_bpm()` |
| `fades.py` `_build()` lines 476-532 | Time-stretch decision + compensation | `time_stretch.py` `resolve_time_stretch()` + `compensate_for_stretch()` |
| `fades.py` `_build()` lines 534-633 | Filter chain construction | `fades.py` `_build_filters()` (new method) |

## Backward compatibility

- `SmartFadesMixer` public API: unchanged
- `SmartCrossFade` constructor signature: unchanged
- `StandardCrossFade`: unchanged
- `SmartFade` ABC: unchanged (still has `_build()` and `apply()`)
- All existing tests for `crossfade_helpers.py` remain valid and unchanged
- FFmpeg output format: unchanged

## Test plan

- Existing `test_crossfade_helpers.py` tests pass without changes (functions stay in place)
- New unit tests for `alignment.py`:
  - `test_resolve_alignment_energy_path`: energy curves present and valid -> strategy="energy"
  - `test_resolve_alignment_spectral_fallback`: energy fails, spectral succeeds -> strategy="spectral"
  - `test_resolve_alignment_bar_count_fallback`: both fail -> strategy="bar_count"
  - `test_clamp_duration_by_bpm`: various BPM diffs produce correct max bars
  - `test_alignment_result_positions_in_source_time`: positions are not pre-compensated
- New unit tests for `time_stretch.py`:
  - `test_resolve_time_stretch_within_threshold`: BPM diff < 8% -> apply=True with steps
  - `test_resolve_time_stretch_above_threshold`: BPM diff > 8% -> apply=False
  - `test_resolve_time_stretch_negligible_diff`: BPM diff < 0.1% -> apply=False
  - `test_compensate_for_stretch_adjusts_fadeout`: fadeout_start_pos divided by ratio
  - `test_compensate_for_stretch_preserves_fadein`: fadein_start_pos unchanged
  - `test_compensate_no_stretch`: apply=False returns alignment unchanged
