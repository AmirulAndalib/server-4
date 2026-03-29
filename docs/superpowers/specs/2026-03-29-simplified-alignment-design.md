# Simplified Crossfade Alignment — Design Specification

**Date:** 2026-03-29
**Branch:** `smart-crossfade-improvements`
**Status:** Draft
**Replaces:** Energy/spectral/bar-count cascade in `alignment.py`
**Contributors:** DJ/Producer expert, DSP/Audio engineer

## Overview

The alignment module (`alignment.py`) has grown to ~1150 lines with 12+ functions and three cascaded strategies that mix temporal and tonal concerns. This spec simplifies it by enforcing a clean separation:

- **Temporal decisions** (when to fade, how long): BPM + key set the max, energy knees + levels determine the actual
- **Tonal decisions** (EQ crossover, curve type): key + spectral centroid + energy slope — already implemented correctly in `crossfade_params.py`

## Problems with Current Approach

1. **Duration computed from bar-count math** — `_calculate_energy_crossfade_duration` computes duration from energy crossover points with an 8-bar minimum, ignoring that the outgoing track's natural decline (knee) already tells you how long the fade should be.

2. **All-or-nothing cascade** — energy alignment requires BOTH a fadeout knee AND a quiet fadein region. If the incoming track has no quiet intro (common in EDM), the entire energy alignment fails and falls to bar-count, losing the good fadeout position.

3. **`_find_fadein_entry` solves the wrong problem** — 140 lines of quiet-region detection when the incoming track should simply enter at its first downbeat. The energy knee on the outgoing side is the primary signal.

4. **Three duplicated strategies** — energy, spectral, and bar-count are three implementations of the same concept (find a position, compute a duration) with different signals. They should be one strategy with fallbacks within it.

5. **Resolver mixes temporal and tonal** — `_resolve_fade_bars` uses energy slopes and spectral overlap for duration, which is the alignment module's job.

## Design

### Metric-to-Decision Mapping

**Temporal decisions (resolver provides max, alignment provides actual):**

| Metric | Role |
|---|---|
| BPM diff | Hard cap: >stretch_threshold = short timed fade (Path A) |
| Key compat | Max duration: compatible=16, moderate=8, incompatible=4, clashing=2 bars |
| Energy knee (outgoing) | Where to start fade. Constrains duration: knee_to_end |
| Energy level at overlap | Further shortens if both sides are loud |

**Tonal decisions (resolver only, already correct):**

| Metric | Role |
|---|---|
| Key compat | Crossover freq: clash pushes higher (more harmonic separation) |
| Spectral centroid | Crossover freq: placed below geometric mean of centroids |
| Spectral overlap | Curve type: high overlap = linear (avoids +3dB power bump) |
| Energy slope | Curve type: natural fade = log, building = exp, default = qsin |

### Algorithm

```
Step 1: Resolver computes max_fade_seconds from key + BPM
        (compatible=16bars, moderate=8, incompatible=4, clashing=2)
        Already implemented in _resolve_fade_bars — simplify to key tiers only.

Step 2: Resolver computes crossover_freq + curve_type from key + spectral + slopes
        Already implemented and correct. No changes.

Step 3: Alignment finds outgoing energy knee
        Use _find_fadeout_start (modified to also return knee_idx).
        If no energy curve, try spectral brightness knee as fallback.

Step 4: Alignment determines actual_duration based on energy context:

        Context A — Knee found:
          fadeout_start = knee position (or 1-4 bars before, proportional)
          actual_duration = buffer_end - fadeout_start, snapped to bars
          Min 2 bars.

        Context B — Both tracks quiet (peak < 0.20):
          No knee detection needed. Tracks won't clash.
          actual_duration = max_fade_seconds (full key-based duration)
          fadeout_start = buffer_end - actual_duration, snapped to phrase

        Context C — No knee, energy present (flat/loud outgoing):
          Energy ratio determines: if incoming >> outgoing, short fade.
          actual_duration = scale max_fade_seconds by energy ratio
          fadeout_start = buffer_end - actual_duration

Step 5: final_duration = min(actual_duration, max_fade_seconds)

Step 6: Incoming track enters at first downbeat (always).
```

### Incoming Track: Always First Downbeat

The incoming track always enters at its first downbeat. No fadein entry detection. The rationale:

- **Quiet intro:** The long max_fade_seconds from key compat gives room for the intro to build under the outgoing track. The natural overlap is long and smooth.
- **Loud start:** The energy knee or energy ratio shortens the actual_duration. The overlap is brief and clean. The EQ crossover (from key+spectral) manages the spectral collision.
- **No analysis data:** Bar-count fallback enters at first downbeat anyway.

`_find_fadein_entry` and `_find_quiet_region_entry` are removed entirely.

---

## Changes by File

### `crossfade_params.py` — simplify `_resolve_fade_bars`

Remove energy slope and spectral overlap from fade length calculation. The function becomes a simple key-tier lookup + BPM routing:

```python
def _resolve_fade_bars(key_compat: float, config: CrossfadeConfig) -> int:
    """Max fade length from key compatibility tiers."""
    if key_compat >= config.key_threshold_compatible:
        return config.key_tier_compatible[1]   # 16
    elif key_compat >= config.key_threshold_moderate:
        return config.key_tier_moderate[1]      # 8
    elif key_compat >= config.key_threshold_clashing:
        return config.key_tier_incompatible[1]  # 4
    else:
        return config.key_tier_clashing[1]      # 2
```

No snapping needed — these are already powers of 2. The energy slopes and spectral overlap stay as inputs to `_resolve_curve_type` (tonal decision), just removed from `_resolve_fade_bars` (temporal decision).

Also rename `fade_bars` / `fade_seconds` on `CrossfadeParams` to `max_fade_bars` / `max_fade_seconds` to clarify intent.

### `alignment.py` — major simplification

**Remove (~510 lines):**
- `_find_fadein_entry` — replaced by "enter at first downbeat"
- `_find_quiet_region_entry` — helper for removed function
- `_calculate_energy_crossfade_duration` — duration from knee position instead
- `_try_spectral_alignment` — fold spectral knee into single strategy
- `_find_spectral_fadein_entry` — removed with fadein detection
- `_find_spectral_fadeout_start` — fold into `_find_knee` helper
- `_calculate_optimal_crossfade_bars` — not needed
- `_calculate_optimal_fade_timing` — not needed
- `_adjust_crossfade_to_downbeats` — snapping stays in simplified form

**Keep (modified):**
- `resolve_alignment` — rewrite to single strategy with three energy contexts
- `_find_fadeout_start` → `_find_knee` — generalized knee finder, returns `(start_pos, knee_idx)`, works on energy or spectral
- `_bar_count_alignment` — gut to ~20 lines, used only when no energy/spectral data exists
- `_clamp_duration_by_bpm` — keep as safety valve
- `_smooth`, `_snap_to_downbeat`, `_snap_to_phrase_boundary`, `_normalize_spectral` — utility functions, keep

**New:**
- `_find_knee(curve, downbeats, bpm, ...)` — unified knee finder that works on both energy and spectral curves. Replaces `_find_fadeout_start` + `_find_spectral_fadeout_start`.

**Rewritten `resolve_alignment`:**

```python
def resolve_alignment(*, fade_out_analysis, fade_in_analysis, logger) -> AlignmentResult:
    # Extract curves and downbeats (existing logic)
    ...

    # Step 1: Try to find outgoing knee (energy first, spectral fallback)
    knee = _find_knee(energy_out, fadeout_downbeats_rel, bpm=fade_out_bpm)
    if knee is None and spectral_out is not None:
        knee = _find_knee(
            _normalize_spectral(spectral_out), fadeout_downbeats_rel, bpm=fade_out_bpm
        )

    # Step 2: Determine fadein entry (always first downbeat)
    fadein_entry = float(fadein_downbeats_rel[0]) if len(fadein_downbeats_rel) > 0 else 0.0

    # Step 3: Compute duration based on energy context
    if knee is not None:
        # Context A: knee found — duration from knee to buffer end
        fadeout_start, knee_idx = knee
        crossfade_duration = float(len(energy_out)) - fadeout_start
        crossfade_duration = _snap_to_bars(crossfade_duration, fade_in_bpm, min_bars=2)
        strategy = "energy"

    elif _is_both_quiet(energy_out, energy_in):
        # Context B: both quiet — use max duration from resolver
        crossfade_duration = SMART_CROSSFADE_DURATION  # will be capped by resolver's max
        fadeout_start = max(0, SMART_CROSSFADE_DURATION - crossfade_duration)
        strategy = "quiet"

    elif energy_out is not None and energy_in is not None:
        # Context C: no knee, energy present — ratio-based
        energy_ratio = _compute_energy_ratio(energy_out, energy_in)
        crossfade_duration = _ratio_to_duration(energy_ratio, fade_in_bpm)
        fadeout_start = max(0, SMART_CROSSFADE_DURATION - crossfade_duration)
        strategy = "energy_ratio"

    else:
        # No energy data at all — bar-count fallback
        return _bar_count_alignment(...)

    return AlignmentResult(
        strategy=strategy,
        fadeout_start_pos=fadeout_start,
        fadein_start_pos=fadein_entry,
        crossfade_duration=crossfade_duration,
        fadeout_downbeats_rel=fadeout_downbeats_rel,
    )
```

### `fades.py` — minimal change

Already uses `min(alignment.crossfade_duration, params.fade_seconds)`. Rename to `params.max_fade_seconds` when the field is renamed.

### `models.py` — rename field

Rename `CrossfadeParams.fade_bars` → `max_fade_bars`, `fade_seconds` → `max_fade_seconds`.

---

## Constants

### Removed
```python
_RISE_GRADIENT          # was used by removed _find_fadein_entry
_RISE_SUSTAINED         # was used by removed _find_fadein_entry
_LOW_ENERGY_GUARD       # replaced by _QUIET_THRESHOLD
_LOW_ENERGY_ABSOLUTE    # replaced by _QUIET_THRESHOLD
_MIN_QUIET_SUSTAIN      # was used by removed _find_quiet_region_entry
_POST_QUIET_WINDOW      # was used by removed _find_quiet_region_entry
_POST_QUIET_RISE_THRESHOLD  # was used by removed _find_quiet_region_entry
_DROP_CHECK_WINDOW      # was used by removed _find_quiet_region_entry
_MAX_RISE_GRADIENT      # was used by removed _find_quiet_region_entry
```

### Kept
```python
_SMOOTH_WINDOW = 3
_DECLINE_THRESHOLD = 0.85
_SPECTRAL_SMOOTH_WINDOW = 5
_SPECTRAL_DECLINE_THRESHOLD = 0.75
_SPECTRAL_REMAINING_AVG_GUARD = 0.85
```

### New
```python
_QUIET_THRESHOLD = 0.20          # Both tracks below this = Context B (both quiet)
_ENERGY_RATIO_SHORT_FADE = 3.0   # If incoming/outgoing energy ratio > this, short fade
```

---

## Files NOT Modified

- `crossfade_params.py`: `_resolve_crossover_freq` — correct, key+spectral drives crossover
- `crossfade_params.py`: `_resolve_curve_type` — correct, key+spectral+slopes drives curve
- `time_stretch.py` — unchanged
- `filters.py` — unchanged
- `mixer.py` — unchanged
- `helpers.py` — unchanged
- `providers/smart_fades/` — unchanged

---

## Test Impact

**Remove tests for removed functions:**
- `test_find_fadein_entry_*` (7 tests)
- `test_find_spectral_fadein_entry_*` (3 tests)
- `test_select_crossfade_curve_type_*` (already removed)
- `test_calculate_energy_crossfade_duration` (1 test)

**Update tests:**
- `test_resolve_alignment_energy_path` — update to verify knee-based duration
- `test_resolve_alignment_spectral_fallback` — update for folded spectral knee
- `test_resolve_alignment_bar_count_fallback` — simplify

**Add tests:**
- Context A: outgoing knee found, incoming loud → short fade at knee
- Context A: outgoing knee found, incoming quiet → longer fade
- Context B: both quiet, compatible keys → long fade (16 bars)
- Context B: both quiet, clashing keys → short fade (2 bars)
- Context C: no knee, incoming much louder → short fade
- Context C: no knee, similar energy → moderate fade
- No data → bar-count fallback

---

## CPU Impact

None. Removes computation (fewer functions, simpler logic).

---

## Expected Results for the MGMT → EDM Case

- Outgoing (MGMT Kids): energy knee at sec 39 of 45s buffer
- Incoming (EDM): starts at 0.27 energy, no quiet intro
- **Context A fires:** knee found at sec 39
- fadeout_start = snapped to downbeat near sec 39
- crossfade_duration = 45 - 39 = 6s → snapped to ~2-4 bars
- fadein_entry = first downbeat (~0.1s)
- Resolver max: key compat 0.9 → 16 bars. min(4 bars, 16 bars) = 4 bars
- Crossover: 1567 Hz (from key+spectral, unchanged)
- Result: 4-bar fade starting at the energy knee. Short, clean handoff.
