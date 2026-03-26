# Smart Crossfade Improvements — Design Specification

**Date:** 2026-03-26
**Branch:** `extended_smart_fades_analysis`
**Status:** Approved (Approach B — Priorities 1-3 for current scope)
**Contributors:** DJ/Producer expert, DSP/FFmpeg expert

## Overview

Six improvements to the smart crossfade system to achieve buttery smooth, invisible transitions. All are technically feasible with FFmpeg. The current scope (Approach B) covers priorities 1-3. Priorities 4-6 are documented for future iterations — the data plumbing prerequisite enables all six.

## Goals

- Deliver buttery smooth transitions that a casual listener wouldn't notice
- No aggressive mixing / DJ-style mixing (reserved for a future mode)
- Maintain backward compatibility — graceful fallback when extended analysis data is absent
- Prefer FFmpeg-based solutions over alternatives

## Current System

The system currently:
- Uses Beat This! neural network for accurate beat/downbeat detection
- Has BPM detection per track
- Time-stretches the outgoing track to match incoming BPM (instant jump)
- Uses FFmpeg filter chain: `TimeStretchFilter` (rubberband) -> `TrimFilter` (atrim) -> `FrequencySweepFilter` (lowpass on outgoing) -> `FrequencySweepFilter` (highpass on incoming) -> `CrossfadeFilter` (acrossfade)
- Aligns crossfades to downbeats when possible
- Has `energy_curve` (per-second RMS), `spectral_centroid_curve`, `phrase_boundaries`, and `musical_key` available in `ExtendedSmartFadesAnalysis` but NOT used in crossfade logic
- `SmartCrossFade` in `fades.py` only receives `AudioAnalysisData` (bpm, beats, downbeats, duration)

## Problems

1. **No phrase awareness:** Crossfades can start mid-phrase (e.g., overlaying beat 1 of Song B with bar 5 of an 8-bar phrase in Song A). Almost all popular music uses 4-bar or 8-bar phrases — starting mid-phrase is jarring even if beats are aligned.

2. **Instant time stretching:** BPM matching jumps instantly (e.g., 1.0x to 1.05x). Even 2-3% instant changes are audible on sustained notes, vocals, and pads. Real DJs nudge pitch gradually over 16-32 bars.

3. **Fixed crossfade curves:** System uses fixed curve shapes (exponential for short, log/linear for long) regardless of actual energy profiles of the tracks.

---

## Prerequisite: Data Plumbing

**Required for all 6 improvements.**

Add optional fields to `AudioAnalysisData`:
- `phrase_boundaries: list[float]` — phrase/section boundary timestamps in seconds
- `energy_curve: list[float]` — per-second RMS energy values
- `spectral_centroid_curve: list[float]` — per-second spectral brightness values
- `musical_key: MusicalKey | None` — root note + mode + confidence

The existing `update()` merge pattern (latest-write-wins) handles new optional fields cleanly. `SmartCrossFade.__init__` picks up what's available and degrades gracefully when absent.

**Files:** `music_assistant/models/audio_analysis.py`

---

## CURRENT SCOPE (Approach B)

---

### Priority 1: Phrase-Aligned Crossfade Timing

**Musical problem:** A crossfade starting at bar 5 of an 8-bar phrase means Song A is mid-phrase while Song B starts a new phrase. This sounds wrong even when beats are perfectly aligned. Apple Music's auto-DJ handles this by aligning energy drops in outros with energy peaks in intros, naturally aligning phrases.

**Musical rules:**
- Crossfade should START on a phrase boundary of the outgoing track (last phrase of the outro — where `energy_curve` starts consistently dropping)
- Crossfade should START on beat 1, bar 1 of a phrase in the incoming track (where energy is still low/building)
- Duration should be a multiple of 4 bars
- Outgoing energy should be declining while incoming is rising during overlap

**Energy cross-validation (critical refinement):**
After selecting a phrase-aligned crossfade start point, validate it by checking:
- Outgoing track's energy is declining in the overlap region
- Incoming track's energy is rising in the overlap region
- If both tracks are at peak energy at the phrase-aligned point, shift the crossfade start to where the energy curves actually cross, even if it breaks phrase alignment
- Rationale: A phrase-aligned crossfade where both tracks are blasting is worse than a slightly off-phrase crossfade during a natural energy transition

**Implementation approach:**
- Python logic changes in `SmartCrossFade._build()` and `_calculate_optimal_fade_timing()`
- No new FFmpeg filters
- New method in `_build()`: find the last phrase boundary before energy starts declining in outgoing track (numpy slope computation on `energy_curve`)
- Find first phrase boundary in incoming track where energy is low/rising
- Use these as crossfade start points, with existing downbeat/beat fallback hierarchy underneath
- Duration constraint to 4-bar multiples: already using bar-based calculations
- Energy cross-validation: `np.argmin(np.abs(outgoing_energy - incoming_energy))` in the candidate region to find crossing point

**Fallback hierarchy (graceful degradation):**
1. **Best:** Align to phrase boundaries on both tracks (with energy cross-validation)
2. **Good:** Align to downbeats at 4-bar multiples
3. **Acceptable:** Align to any downbeat
4. **Last resort:** Current behavior

**Effort:** Medium
**CPU impact:** None (numpy operations on small arrays)
**Files:** `fades.py`, `audio_analysis.py`

---

### Priority 2: Energy-Aware Crossfade Curves

**Musical problem:** System uses fixed curve shapes (exponential for short crossfades, logarithmic/linear for long) regardless of actual energy profiles. This means:
- A track with a natural outro fade gets an artificial fade on top, creating double-dipping
- A loud track blasting into another loud track uses the same curve as a quiet transition
- No gain compensation between tracks of different loudness

**Musical rules:**
- Use `energy_curve` RMS data to select curves dynamically
- If outgoing track has natural volume drop (outro fade), follow it instead of imposing artificial curve — or use shallower artificial curve
- If incoming track has natural build, match highpass filter removal to track's natural spectral opening
- Calculate average RMS of both tracks in crossfade region, apply gain compensation so louder track doesn't dominate
- Both tracks high energy during overlap = faster crossfade to avoid loudness buildup
- Use energy curve slope to choose between equal-power and equal-gain crossfade

**Equal-power vs equal-gain selection:**
- Compare energy curve slopes of both tracks in the crossfade region
- Similar slopes (both declining or both rising at similar rate) = equal-power crossfade (`sqrt`/`qsin` curves) — maintains constant perceived loudness
- Divergent slopes (one declining, one rising) = equal-gain crossfade (linear curves) — follows the natural energy flow
- This is a better heuristic than checking absolute energy levels

**Implementation approach:**
- Extends existing `FrequencySweepFilter` volume expression pattern (already uses `eval=frame`)
- **Gain compensation:** Compute average RMS from both tracks' `energy_curve` arrays in crossfade region. Add a static `volume=XdB` filter on the quieter track. Trivial new filter step.
- **Dynamic curves:** Pre-compute volume envelope from `energy_curve` in Python. Encode as piecewise-linear volume expression:
  ```
  volume='if(lt(t,T1),V1,if(lt(t,T2),V2,...))':eval=frame
  ```
  One control point per second (matching energy_curve resolution). This is manageable expression size.
- **Higher resolution near crossover:** Use 0.25s resolution for the 2-3 seconds around the actual crossover point (where one track hands off to the other). Per-second resolution elsewhere.
- **Equal-power crossfade:** `acrossfade` filter supports `c1=qsin:c2=qsin` (quarter-sine = equal-power). Use when both tracks are loud with similar energy slopes.
- **Natural fade detection:** Compare outgoing energy curve slope to a threshold. If already declining naturally, reduce artificial fade intensity or skip it.

**Effort:** Medium
**CPU impact:** Negligible (volume filter is multiplication)
**Files:** `fades.py`, `filters.py`

---

### Priority 3: Gradual Time Stretching

**Musical problem:** Instant BPM jump (even 2-3%) is audible on sustained notes, vocals, and pads. DJs nudge pitch gradually over 16-32 bars, letting each bar settle before adjusting again.

**Musical rules:**
- Replace single `rubberband=tempo=X` with a ramp across multiple segments
- Split the ramp into steps aligned to downbeats (every 4 beats)
- Each step should be no more than ~0.5% tempo change (e.g., 0.6 BPM at 120 BPM). Below 0.5% per step, the change is imperceptible.
- Start stretching 8-16 bars BEFORE crossfade overlap begins
- Keep max threshold at 5-6% total stretch
- Minimum 1 bar per step

**S-curve distribution (critical refinement):**
Tempo steps should follow an S-curve (slow-fast-slow), NOT linear increments. The human ear is most sensitive to changes in acceleration (jerk), not absolute speed. An S-curve minimizes perceived tempo manipulation.

Formula: `ratio_at_step = start + (end - start) * sigmoid(step)` where sigmoid = `1 / (1 + exp(-k * (step - midpoint)))`

Example for 1.0 -> 1.05 over 10 steps:
```
Step  0: 1.002
Step  1: 1.005
Step  2: 1.010
Step  3: 1.018
Step  4: 1.028
Step  5: 1.038
Step  6: 1.045
Step  7: 1.048
Step  8: 1.0495
Step  9: 1.050
```
Smaller changes at start and end mask the onset and offset of stretching.

**Implementation — Approach A (preferred): `asendcmd` + named `rubberband`**

FFmpeg 7.1.3's `rubberband` filter has the `T` (timeline-editable) flag on `tempo`. This means we can use `asendcmd` to schedule tempo changes at specific timestamps within a single FFmpeg invocation:

```
asendcmd=c='0.0 [rb] tempo 1.002; 2.0 [rb] tempo 1.005; 4.0 [rb] tempo 1.010; ...'
rubberband@rb=tempo=1.0:transients=smooth:detector=soft:pitchq=speed
```

- Schedules tempo steps at downbeat timestamps
- Single rubberband initialization (efficient)
- Computed in Python from beat analysis data, emitted as `asendcmd` timestamp strings

**Implementation — Approach B (fallback): Segmented rubberband**

If rubberband's internal state doesn't handle mid-stream tempo changes well (possible glitches at transition points):

```
atrim into 4-bar segments -> different rubberband ratio per segment -> concat
```

More complex filter graph but guaranteed to work. Higher CPU cost (multiple rubberband initializations).

**CPU optimization:**
- Use `pitchq=speed` + `transients=smooth` on the outgoing track (being attenuated anyway) for ~15-25% CPU savings over default rubberband settings
- On RPi/low-power devices, use 8-bar chunks instead of 4-bar to reduce number of steps
- Combined savings of ~15-25% on rubberband processing

**Recommendation:** Start with Approach A. Fall back to B if testing reveals audio glitches at tempo transition points.

**Effort:** High
**CPU impact:** +20-40% on stretch operations (mitigated by fast mode to ~+10-20%)
**Files:** `filters.py`, `fades.py`

---

## FUTURE SCOPE (Priorities 4-6)

These are fully designed and ready for implementation. The data plumbing prerequisite (same one needed for priorities 1-3) enables all of them.

---

### Priority 4: Large BPM Gap Handling (>10% difference)

**Musical reality:** You cannot smoothly beatmatch 90 BPM hip-hop into 140 BPM drum & bass. Attempting to time-stretch by >10% produces obvious artifacts.

**BPM gap tiers:**
- **<5%:** Current approach works fine
- **5-10%:** Gradual time stretch with S-curve ramp (Priority 3)
- **>10%:** Radio-style transition (this feature)

**Radio-style transition approach:**
New `EnergyFadeCrossFade(SmartFade)` subclass using `concat` instead of `acrossfade` (sequencing, not overlapping).

**FFmpeg filter chain:**
1. `atrim` outgoing track to low-energy phrase boundary
2. `lowpass` sweep down to 200-300 Hz ("underwater" effect) + `volume` fade-out (8-16 seconds)
3. `aevalsrc=0` generates silence gap
4. `atrim` incoming track to low-energy start point
5. `volume` fade-in + `highpass` sweep removal on incoming
6. `concat=n=3:v=0:a=1` joins the three segments

**Silence gap scaling by BPM difference:**
- 10-15% BPM diff = 0.5-1.0 seconds
- 15-25% BPM diff = 1.0-1.5 seconds
- >25% BPM diff = 1.5-2.0 seconds

Computed via linear interpolation.

**Lowpass end frequency:** 200-300 Hz (NOT the standard 1500-2500 Hz). This creates the "underwater" fadeout effect that signals the track is ending. Going below 600 Hz is fine here because we're fading OUT, not overlapping.

**Selection logic:** In `SmartFadesMixer.mix()`, check `abs(1 - bpm_ratio) > 0.10`, route to `EnergyFadeCrossFade`.

**Effort:** Medium
**CPU impact:** Low (no rubberband involved)
**Files:** `fades.py`, `filters.py`, `mixer.py`

---

### Priority 5: Harmonic Awareness

**Musical principle:** Tracks in compatible keys can overlap for longer without clashing harmonics. Incompatible keys create "harmonic mud" when both tracks are audible simultaneously.

**Camelot wheel key compatibility scoring:**
- Same key = 1.0
- Adjacent Camelot position (e.g., 8A to 7A, or 8A to 9A) = 0.9
- Relative major/minor (e.g., 8A to 8B) = 0.85
- +/- 2 Camelot positions = 0.5
- Everything else = 0.2

**Application (pure Python parameter modifier):**
- `calculate_key_compatibility(key1, key2) -> float` returning 0.0-1.0
- Score > 0.7 (compatible): allow longer overlap (up to 16 bars), lower crossover frequency
- Score < 0.3 (incompatible): shorter overlap (4-8 bars), higher crossover frequency for more spectral separation
- Applied as multipliers on `crossfade_duration` and `crossover_freq` before they reach FFmpeg

**Implementation:** Camelot wheel as a lookup table (24 keys x 24 keys = 576 entries) or computed from Camelot number/letter.

**Effort:** Low
**CPU impact:** None
**Files:** `fades.py` (new helper function)

---

### Priority 6: Spectral-Aware Crossover Frequency

**Musical problem:** Current crossover frequency formula `1500 + (avg_bpm - 90) * 20` is a rough heuristic that doesn't account for actual spectral content of the tracks being mixed.

**Rules:**
- Bass-heavy outgoing + treble-heavy incoming = lower crossover (~800 Hz)
- Similar spectral profiles = standard DJ mixer crossover (~1000-1200 Hz)
- Treble-heavy outgoing + bass-heavy incoming = higher crossover (~1500-2000 Hz)

**Implementation:**
- Average the `spectral_centroid_curve` values in the crossfade region for both tracks
- Map centroid difference to crossover frequency
- Clamp to 600-3000 Hz range (guardrails):
  - Below 600 Hz cuts into kick drum territory (hollow sound)
  - Above 3000 Hz doesn't separate enough (vocal clash)
- Feeds directly into existing `FrequencySweepFilter.target_freq` — no structural changes

**Effort:** Low
**CPU impact:** None
**Files:** `fades.py`

---

## Cross-Cutting Concerns

### Decision Logging
Log which transition strategy was selected and why. Example:
```
"phrase-aligned crossfade at bar 32, 8-bar duration, key compatibility 0.9, energy cross at 4.2s into overlap, gradual stretch 1.0->1.03 over 6 bars (S-curve), equal-power curves selected (similar energy slopes)"
```
Extends the existing verbose logging pattern in the smart fades code. Makes debugging bad transitions straightforward — listen to a transition that sounded wrong and immediately see what decisions were made.

### Backward Compatibility
All improvements check for field presence and fall back to current behavior when extended analysis data is absent. A track analyzed before these changes will still get the current crossfade quality. Re-analysis is not required but will unlock better transitions.

### Performance on Low-Power Devices (RPi, NAS)
- Priorities 1, 2, 5, 6: Zero additional CPU cost
- Priority 3 (gradual stretching): +20-40% on rubberband operations, mitigated to ~+10-20% with `transients=smooth` + `pitchq=speed`. Use 8-bar chunks instead of 4-bar on constrained hardware.
- Priority 4 (large BPM gap): Lower CPU than current approach (no rubberband at all)

### Architecture Summary
| Priority | New FFmpeg Concepts | New Python Logic | New Classes |
|----------|-------------------|-----------------|-------------|
| Prereq | None | Optional fields on AudioAnalysisData | None |
| 1 | None | Phrase selection + energy validation | None |
| 2 | Richer volume expressions | Energy slope analysis, gain computation | None |
| 3 | `asendcmd` for tempo scheduling | S-curve sigmoid computation | GradualTimeStretchFilter |
| 4 | `concat`, `aevalsrc` | BPM gap detection, silence scaling | EnergyFadeCrossFade |
| 5 | None | Camelot wheel compatibility | None |
| 6 | None | Spectral centroid mapping | None |

### Updated Filter Chain (after all 6)
```
[BPM gap >10%?] ──yes──> EnergyFadeCrossFade (concat-based, no overlap)
       │
       no
       │
       v
GradualTimeStretchFilter (asendcmd + rubberband, S-curve steps)
       │
       v
TrimFilter (phrase-aligned start position)
       │
       v
FrequencySweepFilter (lowpass on outgoing, spectral-centroid-derived crossover, energy-aware curve)
       │
       v
FrequencySweepFilter (highpass on incoming, energy-aware curve)
       │
       v
GainCompensationFilter (volume=XdB on quieter track)
       │
       v
CrossfadeFilter (acrossfade, equal-power or equal-gain based on energy slopes)
       │
       v
[Harmonic awareness adjusts crossfade_duration and crossover_freq before chain runs]
```
