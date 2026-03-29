# Unified Crossfade Decision Framework — Design Specification

**Date:** 2026-03-29
**Branch:** `smart-crossfade-improvements`
**Status:** Draft
**Depends on:** `2026-03-26-smart-crossfade-improvements-design.md` (priorities 5 & 6)
**Contributors:** DJ/Producer expert, DSP/Audio engineer, Psychoacoustics/Mastering engineer

## Overview

Priorities 5 (Harmonic Awareness) and 6 (Spectral-Aware Crossover Frequency) from the smart crossfade improvements design both influence overlapping outputs: crossover frequency, fade length, and curve type. This spec defines a unified decision framework that combines key compatibility, spectral centroid, energy contours, and BPM stretchability into coherent crossfade parameters.

## Goals

- Combine harmonic, spectral, and energy signals into a single decision pipeline
- Replace the current BPM-only crossover heuristic (`1500 + (avg_bpm - 90) * 20`)
- Replace the current bar-count-only curve selection (bars < 8 -> exponential)
- Snap fade lengths to musically coherent bar counts (powers of 2)
- Make all thresholds and tuning values easily configurable for iterative listening tests
- Maintain graceful degradation when analysis data is missing

## Non-Goals

- DJ-style mixing or aggressive transitions (reserved for future mode)
- Time signature detection (assume 4/4)
- Phrase boundary detection beyond downbeat alignment

## Current System (Being Replaced)

These specific heuristics in `_build_filters()` (`fades.py:273-289`) are replaced:

```python
# Current crossover: BPM-only heuristic
avg_bpm = (fade_out_bpm + fade_in_bpm) / 2
crossover_freq = int(np.clip(1500 + (avg_bpm - 90) * 20, 1500, 2500))

# Current curve: bar-count-only
if crossfade_bars < 8:
    fadeout_curve = "exponential"
    fadein_curve = "exponential"
else:
    fadeout_curve = "logarithmic"
    fadein_curve = "linear"
```

The energy-contour alignment in `alignment.py` (fade positioning and duration) remains unchanged. This framework sits downstream: it takes the alignment result's `crossfade_duration` as input context and outputs refined parameters.

---

## Architecture

### Module Structure

The smart fades controller module follows a consistent pattern: resolver functions that take analysis data and return dataclass results. All dataclasses are consolidated into a single `models.py` within the module, while logic stays in its own file.

#### New File: `models.py`

Location: `music_assistant/controllers/streams/smart_fades/models.py`

Consolidates **all** dataclasses used across the smart fades controller module into one place. This includes existing dataclasses that are currently defined inline in their logic files, plus the new ones from this spec.

**Moved from existing files:**
- `AlignmentResult` (currently in `alignment.py`)
- `TimeStretchDecision` (currently in `time_stretch.py`)

**New dataclasses:**
- `MusicalKey` — key representation with Camelot wheel logic (see below)
- `CrossfadeConfig` — all tunable crossfade parameters
- `CrossfadeParams` — resolved crossfade parameters for filter construction

The existing files (`alignment.py`, `time_stretch.py`) import their dataclasses from `models.py` instead of defining them inline. This eliminates circular import risk and makes the type landscape visible in one place.

#### `MusicalKey` in `models.py`

**Deliberately decoupled from `AudioAnalysisData`** — this is the smart fades module's own domain concept, not a model-layer type. The smart fades module constructs `MusicalKey` from the raw `musical_key: dict` in `AudioAnalysisData`.

```python
@dataclass
class MusicalKey:
    """Musical key with Camelot wheel compatibility scoring.

    This is the smart fades module's own representation, constructed from
    the raw key data in AudioAnalysisData. It is not coupled to the
    audio analysis model.
    """

    root: str           # "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"
    mode: str           # "major" or "minor"
    confidence: float   # 0.0-1.0 from key detection

    @property
    def camelot_code(self) -> str | None:
        """Return Camelot wheel code (e.g. '8B', '5A'), or None if root is unrecognized."""
        return _CAMELOT_WHEEL.get((self.root, self.mode))

    def compatibility_score(self, other: MusicalKey) -> float:
        """Return 0.0-1.0 harmonic compatibility using Camelot wheel distance.

        :param other: The other musical key to compare against.
        """
        ...
```

**Camelot wheel lookup table:** Module-level constant in `models.py`. Maps `(root, mode)` to Camelot code. 24 entries covering all keys.

```python
_CAMELOT_WHEEL: dict[tuple[str, str], str] = {
    ("A♭", "major"): "4B",  ("B",  "major"): "1B",  ("F#", "major"): "2B",
    ("D♭", "major"): "3B",  ("G#", "major"): "4B",  ("E♭", "major"): "5B",
    ("B♭", "major"): "6B",  ("F",  "major"): "7B",  ("C",  "major"): "8B",
    ("G",  "major"): "9B",  ("D",  "major"): "10B", ("A",  "major"): "11B",
    ("E",  "major"): "12B",
    ("A♭", "minor"): "1A",  ("E♭", "minor"): "2A",  ("B♭", "minor"): "3A",
    ("F",  "minor"): "4A",  ("C",  "minor"): "5A",  ("G",  "minor"): "6A",
    ("D",  "minor"): "7A",  ("A",  "minor"): "8A",  ("E",  "minor"): "9A",
    ("B",  "minor"): "10A", ("F#", "minor"): "11A", ("C#", "minor"): "12A",
}
```

**Compatibility scoring algorithm:** Parses Camelot codes into number (1-12) and letter (A/B), then scores by distance:

| Relationship | Distance | Score |
|---|---|---|
| Same key | number=0, same letter | 1.0 |
| Adjacent position | number=1, same letter | 0.9 |
| Relative major/minor | number=0, different letter | 0.85 |
| Adjacent + relative | number=1, different letter | 0.8 |
| 2 positions away | number=2, same letter | 0.5 |
| 3 positions away | number=3, same letter | 0.2 |
| Everything else | >3 | 0.1 |

Number distance wraps: `min(abs(n1 - n2), 12 - abs(n1 - n2))`.

Returns `0.1` if either key has no valid Camelot code (unrecognized root/mode).

#### New File: `crossfade_params.py`

Location: `music_assistant/controllers/streams/smart_fades/crossfade_params.py`

Contains the unified decision logic. Follows the resolver pattern: `resolve_crossfade_params()` returns a `CrossfadeParams` dataclass (defined in `models.py`).

Contains:
- `resolve_crossfade_params()` — main resolver function
- `snap_to_musical_bars()` — power-of-2 bar snapping (public, useful for tests)
- `_extract_key()` — constructs `MusicalKey` from raw `AudioAnalysisData.musical_key` dict
- `_compute_energy_slopes()` — extracts gradient from energy_curve in the crossfade region
- `_compute_spectral_overlap()` — measures spectral similarity from centroids
- `_resolve_crossover_freq()` — key-urgency blended crossover calculation
- `_resolve_fade_bars()` — key tier → energy → spectral → snap pipeline
- `_resolve_curve_type()` — priority chain for curve selection

### Integration Point

`SmartCrossFade._build_filters()` in `fades.py` calls `resolve_crossfade_params()` to get `CrossfadeParams`, then uses those values for filter construction instead of the inline heuristics.

### Data Flow

```
AudioAnalysisData (fade_out)
  │  .musical_key: dict ──→ _extract_key() ──→ MusicalKey("D#", "major", 0.8)
  │  .energy_curve: ndarray                              │
  │  .spectral_centroid_curve: ndarray                   │
  │                                                      ↓
  ├──→ resolve_crossfade_params() ←── MusicalKey.compatibility_score()
  │         │
AudioAnalysisData (fade_in)                              │
  │  .musical_key: dict ──→ _extract_key() ──→ MusicalKey   │
  │                                                      │
TimeStretchDecision ────────────────────────────────────→│
CrossfadeConfig (defaults or custom) ──────────────────→ │
                                                         ↓
                                                   CrossfadeParams
                                                         │
                                                         ↓
                                               _build_filters() uses:
                                                 - crossover_freq → FrequencySweepFilter
                                                 - fade_bars → crossfade duration
                                                 - curve_type → CrossfadeFilter curves
```

### Coupling Boundaries

- `models.py` has minimal imports — only `numpy` for type hints on `AlignmentResult.fadeout_downbeats_rel`. No imports from other smart fades modules.
- `crossfade_params.py` imports types from `models.py` and reads raw dicts from `AudioAnalysisData`. It does **not** add methods or helpers to `AudioAnalysisData`.
- `AudioAnalysisData` in `models/audio_analysis.py` is **unchanged**. It stores `musical_key: dict[str, Any] | None` as before. The smart fades module owns the translation to its domain type.
- `alignment.py` and `time_stretch.py` import their dataclasses from `models.py` instead of defining them inline.
- `fades.py` imports `resolve_crossfade_params` from `crossfade_params.py` and types from `models.py`.

---

## Configuration

All thresholds and tuning values live in `CrossfadeConfig`. Different fade modes (smart crossfade, future modes) can instantiate their own config with different values.

```python
@dataclass
class CrossfadeConfig:
    """All tunable parameters for crossfade decisions.

    Every threshold, range, and multiplier is configurable for iterative
    listening tests. Change one number, re-run, listen.
    """

    # ── Path routing ─────────────────────────────────────────────
    stretch_threshold_pct: float = 6.0  # above this → Path A (no stretch possible)

    # ── Path A: unstretched (>stretch_threshold_pct BPM diff) ────
    path_a_min_fade_sec: float = 1.5
    path_a_max_fade_sec: float = 2.5
    path_a_crossover_low: int = 2200
    path_a_crossover_high: int = 3000

    # ── Path B: key compatibility → fade length tiers ────────────
    # Each tier is (min_bars, max_bars). Values must be powers of 2.
    # Energy score picks position within range before snapping.
    key_tier_compatible: tuple[int, int] = (8, 16)     # key_compat >= key_threshold_compatible
    key_tier_moderate: tuple[int, int] = (4, 8)        # key_compat >= key_threshold_moderate
    key_tier_incompatible: tuple[int, int] = (2, 4)    # key_compat >= key_threshold_clashing
    key_tier_clashing: tuple[int, int] = (2, 2)        # key_compat < key_threshold_clashing

    # Key compatibility thresholds for tier selection
    key_threshold_compatible: float = 0.7
    key_threshold_moderate: float = 0.3
    key_threshold_clashing: float = 0.15

    # ── Path B: crossover frequency ──────────────────────────────
    crossover_key_base: float = 1000.0     # crossover at key_compat=1.0
    crossover_key_range: float = 2000.0    # added as key_compat drops toward 0
    crossover_spectral_scale: float = 0.6  # multiplied by geometric mean of centroids
    key_urgency_steepness: float = 1.5     # how fast key takes over from spectral
    crossover_min: int = 600
    crossover_max: int = 3000

    # ── Path B: spectral overlap modifier on fade length ─────────
    spectral_fade_mult_min: float = 0.8    # low overlap → shorter
    spectral_fade_mult_max: float = 1.2    # high overlap → longer

    # ── Path B: curve selection thresholds ────────────────────────
    key_compat_exp_threshold: float = 0.3           # below → exponential
    energy_slope_natural_fade: float = -0.3         # outgoing fading → logarithmic
    energy_slope_building: float = 0.3              # incoming building → exponential
    spectral_overlap_linear_threshold: float = 0.7  # above + decent key → linear
    key_compat_linear_threshold: float = 0.5        # minimum key for linear selection
    long_fade_linear_threshold: int = 12            # bars, above → consider linear

    # ── Key detection confidence gate ────────────────────────────
    key_confidence_threshold: float = 0.4  # below → use neutral key_compat
    key_compat_neutral: float = 0.6        # default when confidence is low
```

---

## Decision Algorithms

### Camelot Wheel Key Compatibility

Each musical key maps to a Camelot wheel position: a number (1-12) and a letter (A for minor, B for major). Compatibility is scored by distance on the wheel.

| Relationship | Distance | Example | Score |
|---|---|---|---|
| Same key | 0 | 8A → 8A | 1.0 |
| Adjacent position | 1 | 8A → 7A, 8A → 9A | 0.9 |
| Relative major/minor | A↔B same number | 8A → 8B | 0.85 |
| Adjacent + relative | 1 + A↔B | 8A → 7B | 0.8 |
| 2 positions away | 2 | 8A → 6A | 0.5 |
| 3 positions away | 3 | 8A → 5A | 0.2 |
| Everything else | >3 | | 0.1 |

**Confidence gate:** If either track's key detection confidence is below `key_confidence_threshold`, return `key_compat_neutral` instead of the computed score. This prevents aggressive decisions based on unreliable key data.

**Implementation:** Computed from Camelot number/letter distance. The Camelot number wraps (12 → 1), so distance = `min(abs(n1 - n2), 12 - abs(n1 - n2))`.

### Spectral Overlap

Measures how similar two tracks' spectral profiles are, using centroids as a proxy:

```python
def spectral_overlap(centroid_a: float, centroid_b: float) -> float:
    """0-1 measure of spectral similarity. Uses log-frequency ratio."""
    hi = max(centroid_a, centroid_b, 1.0)
    lo = max(min(centroid_a, centroid_b), 1.0)
    return clamp(1.0 - log2(hi / lo), 0.0, 1.0)
```

- 1.0 = identical spectra
- 0.5 = roughly one octave apart
- 0.0 = two or more octaves apart

---

### Path A: Unstretched (BPM diff > stretch_threshold_pct)

When the BPM difference exceeds the stretch threshold, the system cannot time-stretch to match tempos. Beats drift during the crossfade. At >6%, drift exceeds perceptual thresholds within 1-2 bars. This path is a damage-limitation strategy: a quick, clean handoff with minimal spectral overlap.

**Rationale:** At 6% drift and 128 BPM, beats slip by one full beat every ~7.8 seconds. Listeners detect rhythmic misalignment at 30-50ms, which arrives in under 2 seconds. Bar-aligned crossfading is meaningless when beats don't align.

**Algorithm:**

```
crossover = lerp(path_a_crossover_low, path_a_crossover_high, 1 - key_compat)
fade_seconds = lerp(path_a_max_fade_sec, path_a_min_fade_sec, 1 - key_compat)
curve = exponential
use_bar_alignment = False
```

Key compatibility has minor influence: it nudges crossover (2200→3000 Hz) and fade length (2.5→1.5s). But rhythmic incoherence dominates. This path always uses exponential curves and never attempts bar alignment.

---

### Path B: Stretched (BPM diff <= stretch_threshold_pct)

The GradualTimeStretch completes before the crossfade begins. Both tracks are at identical BPM with beat-aligned transients throughout the overlap. BPM is not a constraint on fade length.

Three-stage pipeline: crossover → fade length → curve type.

#### Stage 1: Crossover Frequency

Two signals blended by key urgency:

```python
# Key-driven: pushes crossover up to isolate harmonics when keys clash
crossover_key = key_base + (1.0 - key_compat) * key_range

# Spectral-driven: places crossover below geometric mean of centroids
crossover_spectral = clamp(sqrt(centroid_a * centroid_b) * spectral_scale, min, max)

# Blend: key takes priority as compatibility drops
key_urgency = clamp((1.0 - key_compat) * steepness, 0.0, 1.0)
crossover = key_urgency * crossover_key + (1.0 - key_urgency) * crossover_spectral
crossover = clamp(crossover, crossover_min, crossover_max)
```

**Behavior:**
- key_compat >= 0.85: key_urgency ~0.22, spectral mostly drives → 800-1400 Hz
- key_compat ~0.5: key_urgency ~0.75, key mostly drives → 1800-2200 Hz
- key_compat <= 0.33: key_urgency saturates to 1.0, key fully drives → 2300-3000 Hz

**Why this blend works:** When keys are compatible, the crossover should optimize for spectral separation (place it where the two tracks' energy is best separated). When keys clash, harmonic isolation is non-negotiable and overrides spectral optimization.

#### Stage 2: Fade Length (bars)

Three factors applied as successive constraints, snapped to musically coherent bar counts.

**Step 1 — Key compatibility selects the tier:**

```
if key_compat >= key_threshold_compatible:   tier = key_tier_compatible    # (8, 16)
elif key_compat >= key_threshold_moderate:   tier = key_tier_moderate      # (4, 8)
elif key_compat >= key_threshold_clashing:   tier = key_tier_incompatible  # (2, 4)
else:                                        tier = key_tier_clashing      # (2, 2)
```

**Step 2 — Energy flow picks position within the tier:**

```python
# energy_score: how good the energy handoff is for crossfading
# Ideal: outgoing fading out (negative slope), incoming building (positive slope)
energy_flow = clamp(slope_in - slope_out, -2.0, 2.0)
energy_score = (energy_flow + 2.0) / 4.0  # normalize to 0-1

# Lerp between min and max bars of the tier
fade_bars_raw = tier.min + (tier.max - tier.min) * energy_score
```

**Step 3 — Spectral overlap as a minor multiplier:**

```python
spectral_mult = spectral_fade_mult_min + spectral_overlap * (spectral_fade_mult_max - spectral_fade_mult_min)
fade_bars_raw *= spectral_mult
```

High spectral overlap (similar tracks) → small boost (masking helps). Low spectral overlap (different tracks) → small reduction (each track is distinctly audible).

**Step 4 — Snap to power-of-2 bars with downward bias:**

```python
def snap_to_musical_bars(bars: float) -> int:
    if bars <= 1.5:  return 1
    if bars <= 3.0:  return 2
    if bars <= 6.0:  return 4
    if bars <= 12.0: return 8
    return 16
```

Downward bias is intentional: if the continuous calculation says "5 bars," the inputs indicate the situation isn't comfortable enough for 8 but is fine for 4. A slightly short clean transition beats a slightly long problematic one.

#### Stage 3: Curve Type

Priority chain (first match wins):

| Priority | Condition | Curve | Rationale |
|---|---|---|---|
| 1 | `key_compat < key_compat_exp_threshold` | exponential | Minimize time both harmonic contents are loud |
| 2 | `spectral_overlap > spectral_overlap_linear_threshold and key_compat > key_compat_linear_threshold` | linear | Avoid +3dB power bump from correlated signals |
| 3 | `fade_bars >= long_fade_linear_threshold and both_tracks_high_energy` | linear | Prevent loudness buildup in long dense overlaps |
| 4 | `energy_slope_out < energy_slope_natural_fade` | logarithmic | Match the outgoing track's natural fade contour |
| 5 | `energy_slope_in > energy_slope_building` | exponential | Clear space for the incoming track's build |
| 6 | Default | qsin (equal-power) | Constant perceived loudness |

---

## Decision Logging

Every computation logs inputs, intermediate values, and outputs:

```
"Path B: key_compat=0.85 (8A→9A), centroids=2100/1800Hz, slopes=-0.4/0.2
 → crossover=1050Hz (urgency=0.22, spectral_xover=1180)
 → fade=8bars (tier=8-16, energy=0.65, spectral_mult=1.08, raw=9.3, snapped=8)
 → curve=log (matched: energy_slope_out=-0.4 < -0.3)"
```

This extends the existing verbose logging pattern in the smart fades code and makes debugging transitions straightforward.

---

## Graceful Degradation

| Missing Signal | Behavior |
|---|---|
| No key data or low confidence | Use `key_compat_neutral` (0.6), let energy/spectral drive |
| No spectral centroid | Use `crossover_key` only (key drives 100% of crossover) |
| No energy curves | Use midpoint of key tier `(min + max) / 2`, default qsin curve |
| No analysis at all | Fall through to existing bar-count alignment (current behavior unchanged) |

---

## Example Scenarios

### Best case: two house tracks, same key, natural energy handoff
- key_compat=1.0, centroids=2000/2200 Hz, slopes=-0.3/0.4
- **Crossover:** key_urgency=0, spectral drives → sqrt(2000*2200)*0.6 = ~1260 Hz
- **Fade:** tier (8,16), energy_score ~0.85 → raw ~14.8 → snaps to **16 bars**
- **Curve:** energy_slope_out < -0.3 → **logarithmic**
- **Result:** 16-bar blend at 1260 Hz, log curve. Invisible transition.

### Moderate: EDM into chill, adjacent keys, bass-heavy into bright
- key_compat=0.5, centroids=800/3200 Hz, slopes=-0.5/0.3
- **Crossover:** key_urgency=0.75 → blended ~1900 Hz
- **Fade:** tier (4,8), energy_score ~0.7 → raw ~6.8 → snaps to **4 bars**
- **Curve:** energy_slope_out < -0.3 → **logarithmic**
- **Result:** 4-bar blend at 1900 Hz, log curve.

### Worst stretched case: pop into rock, tritone apart, both loud
- key_compat=0.1, centroids=1800/1900 Hz, slopes=0.1/0.1
- **Crossover:** key_urgency=1.0, key drives → 2800 Hz
- **Fade:** tier (2,2) → **2 bars**
- **Curve:** key_compat < 0.3 → **exponential**
- **Result:** 2-bar fade at 2800 Hz, exponential. Quick and clean.

### Unstretched (Path A): 8% BPM diff, moderate key
- bpm_diff=8%, key_compat=0.5
- **Crossover:** lerp(2200, 3000, 0.5) = 2600 Hz
- **Fade:** lerp(2.5, 1.5, 0.5) = **2.0 seconds** (no bar alignment)
- **Curve:** **exponential**
- **Result:** 2-second timed fade at 2600 Hz. Dignified quick handoff.

### Missing key data: only spectral and energy available
- key_compat=0.6 (neutral fallback), centroids=1500/2500 Hz, slopes=-0.2/0.1
- **Crossover:** key_urgency=0.6, blended → ~1600 Hz
- **Fade:** tier (4,8), energy_score ~0.58 → raw ~6.3 → snaps to **4 bars**
- **Curve:** default → **qsin**
- **Result:** Conservative 4-bar blend, equal-power. Safe when uncertain.

---

## Files to Modify

| File | Change |
|---|---|
| **New: `controllers/streams/smart_fades/models.py`** | All dataclasses: `MusicalKey` (with Camelot logic), `CrossfadeConfig`, `CrossfadeParams`, plus moved `AlignmentResult`, `TimeStretchDecision`. Also `_CAMELOT_WHEEL` lookup table. |
| **New: `controllers/streams/smart_fades/crossfade_params.py`** | `resolve_crossfade_params()`, `snap_to_musical_bars()`, private helpers for key extraction, energy slopes, spectral overlap, crossover, curve selection |
| `controllers/streams/smart_fades/alignment.py` | Remove `AlignmentResult` dataclass definition, import from `models.py` |
| `controllers/streams/smart_fades/time_stretch.py` | Remove `TimeStretchDecision` dataclass definition, import from `models.py` |
| `controllers/streams/smart_fades/fades.py` | `_build_filters()` calls `resolve_crossfade_params()` instead of inline heuristics (replaces lines 273-289), imports from `models.py` |

## Files NOT Modified

- `models/audio_analysis.py` — `AudioAnalysisData` unchanged, `musical_key` stays as `dict[str, Any] | None`
- `models/smart_fades.py` — old `MusicalKey` stays (deprecated), no changes
- `filters.py` — filter implementations unchanged, just receive different parameter values
- `mixer.py` — orchestration unchanged
- `helpers.py` — shared utilities unchanged
- `providers/smart_fades/` — analysis provider unchanged

## Testing

- Unit tests for `MusicalKey.compatibility_score()` covering all Camelot relationships (same key, adjacent, relative, distant)
- Unit tests for `MusicalKey.camelot_code` property for all 24 keys
- Unit tests for `resolve_crossfade_params()` with known inputs → expected outputs (Path A and Path B scenarios)
- Unit tests for `snap_to_musical_bars()` boundary cases (1.5, 3.0, 6.0, 12.0 thresholds)
- Unit tests for graceful degradation (missing key, missing spectral, missing energy, missing all)
- Unit tests for `_extract_key()` from raw dict and None handling
- Integration: verify `_build_filters()` produces valid FFmpeg filter chains with new parameters

## CPU Impact

None. All computations are simple arithmetic on scalar values. No FFT, no numpy operations beyond what's already computed.
