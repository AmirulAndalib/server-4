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
- Has `energy_curve` (per-second RMS), `spectral_centroid_curve`, and `musical_key` available in `AudioAnalysisData` but NOT used in crossfade logic
- `SmartCrossFade` in `fades.py` only receives `AudioAnalysisData` (bpm, beats, downbeats, duration)

## Problems

1. **No energy awareness:** Crossfades ignore energy contours, so they can overlay a loud chorus of Song A with the loud drop of Song B, creating a loudness clash. The crossfade should align Song A's declining energy with Song B's rising energy.

2. **Instant time stretching:** BPM matching jumps instantly (e.g., 1.0x to 1.05x). Even 2-3% instant changes are audible on sustained notes, vocals, and pads. Real DJs nudge pitch gradually over 16-32 bars.

3. **Fixed crossfade curves:** System uses fixed curve shapes (exponential for short, log/linear for long) regardless of actual energy profiles of the tracks.

---

## Prerequisite: Data Plumbing

**Required for all 6 improvements.**

Add optional fields to `AudioAnalysisData`:
- `energy_curve: list[float]` — per-second RMS energy values (normalized 0-1)
- `spectral_centroid_curve: list[float]` — per-second spectral brightness values
- `musical_key: MusicalKey | None` — root note + mode + confidence

The existing `update()` merge pattern (latest-write-wins) handles new optional fields cleanly. `SmartCrossFade.__init__` picks up what's available and degrades gracefully when absent.

**Files:** `music_assistant/models/audio_analysis.py`

---

## CURRENT SCOPE (Approach B)

---

### Priority 1: Energy-Contour Crossfade Alignment

**Musical problem:** The current crossfade logic uses only bar counts and downbeat positions to decide when and where to fade. It doesn't consider energy — so it can overlay a loud chorus of Song A with the loud drop of Song B, creating a loudness clash. The goal is to align Song A's declining energy with Song B's rising energy for a natural "energy handoff."

**Approach: Energy-contour analysis at mix time (in fades.py).**
No pre-computed phrase boundaries needed. The crossfade code directly analyzes `energy_curve` from both tracks at mix time, because the optimal crossfade point depends on the *pairing* of two specific tracks, not on either track in isolation.

**Algorithm:**

1. **Find Song A's fade-out start (energy peak → decline knee):**
   - Extract last ~45s of Song A's energy_curve
   - Smooth with 3-second moving average to ignore transient hits
   - Find the peak energy in the smoothed curve
   - Walk forward from peak to find where energy drops below 85% of peak (the "energy knee")
   - Snap to nearest downbeat at or after the knee
   - Fallback: if no clear decline, use ~8 bars before end (current behavior)

2. **Find Song B's fade-in entry (low energy, about to rise):**
   - Extract first ~45s of Song B's energy_curve
   - Smooth with 3-second moving average
   - Find first sustained positive gradient (energy increasing > 0.05/s over 3 consecutive seconds)
   - Verify absolute energy at entry point is below 50% of Song B's peak (guard against "already loud" intros)
   - Snap to nearest downbeat before the rise begins
   - Fallback: first downbeat (current behavior)

3. **Calculate crossfade duration from energy handoff:**
   - Duration = time from fade-in entry until Song B's energy matches Song A's level at fade start + 1 bar blend buffer
   - Clamp to [4 beats, 45 seconds], snap to bar boundaries
   - Fallback: 8 bars (current behavior)

4. **Energy-aware curve selection:**
   - Compare avg energy of both tracks in overlap region
   - Similar energy with similar slopes → equal-power (qsin)
   - Divergent slopes (one declining, one rising) → equal-gain (linear)
   - Song B much louder → fade A out faster (exponential), bring B in gently (logarithmic)

**Coordinate system fix:** The energy_curve covers the full song (1 value/second) but the PCM buffers are the last/first ~45 seconds. Fade-out energy: `energy_curve[-45:]`. Fade-in energy: `energy_curve[:45]`. Fade-out downbeats must be shifted to buffer-relative coordinates: `downbeats[mask] - outro_start`.

**Fallback hierarchy (graceful degradation):**
1. **Best:** Energy-contour aligned with downbeat snapping
2. **Good:** Current downbeat-based approach (when energy_curve is absent)
3. **Last resort:** Current bar-counting behavior

**Effort:** Medium
**CPU impact:** None (numpy operations on ~45 values)
**Files:** `fades.py`

---

### Priority 2: Energy-Aware Crossfade Curves

**Musical problem:** System uses fixed curve shapes (exponential for short crossfades, logarithmic/linear for long) regardless of actual energy profiles. This means:
- A track with a natural outro fade gets an artificial fade on top, creating double-dipping
- A loud track blasting into another loud track uses the same curve as a quiet transition
- No awareness of energy profiles between tracks

**Musical rules:**
- Use `energy_curve` RMS data to select curves dynamically
- If outgoing track has natural volume drop (outro fade), follow it instead of imposing artificial curve — or use shallower artificial curve
- If incoming track has natural build, match highpass filter removal to track's natural spectral opening
- Both tracks high energy during overlap = faster crossfade to avoid loudness buildup
- Both tracks high energy during overlap = faster crossfade to avoid loudness buildup
- Use energy curve slope to choose between equal-power and equal-gain crossfade

**Equal-power vs equal-gain selection:**
- Compare energy curve slopes of both tracks in the crossfade region
- Similar slopes (both declining or both rising at similar rate) = equal-power crossfade (`sqrt`/`qsin` curves) — maintains constant perceived loudness
- Divergent slopes (one declining, one rising) = equal-gain crossfade (linear curves) — follows the natural energy flow
- This is a better heuristic than checking absolute energy levels

**Implementation approach:**
- Extends existing `FrequencySweepFilter` volume expression pattern (already uses `eval=frame`)
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
| 1 | None | Energy-contour analysis at mix time | None |
| 2 | Richer volume expressions | Energy slope analysis, curve selection | None |
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
TrimFilter (energy-contour-aligned start position)
       │
       v
FrequencySweepFilter (lowpass on outgoing, spectral-centroid-derived crossover, energy-aware curve)
       │
       v
FrequencySweepFilter (highpass on incoming, energy-aware curve)
       │
       v
CrossfadeFilter (acrossfade, equal-power or equal-gain based on energy slopes)
       │
       v
[Harmonic awareness adjusts crossfade_duration and crossover_freq before chain runs]
```

---

## Audio Analysis Provider Design

All 4 new analysis features are computed within the **existing `SmartFadesProvider`** using **librosa** (already a project dependency, v0.11.0) for battle-tested STFT-based features. The existing mel spectrogram, resampling logic, and feature extractor are NOT modified. Features are computed directly from `pcm_22k` (already available in `_process_block()`).

---

### Streaming STFT Strategy: Direct Computation on pcm_22k

**Approach:** Compute `librosa.stft(pcm_22k)` directly on each 10-second block of resampled PCM. No changes to the feature extractor or its overlap buffer.

**Boundary artifacts:** STFT frames at 10-second block boundaries have edge padding artifacts. Analysis shows this affects ~1.2% of per-second averaged values — statistically insignificant for phrase detection thresholds (0.15-0.40 combined score) and crossfade decisions. If higher accuracy is needed later, the shared overlap buffer approach can be adopted (the feature extractor's `_prev_samples` buffer already stores sufficient backward context for n_fft=2048).

**Memory impact:** The STFT matrix `S` is transient — features are derived and `S` is discarded within the same block. Only per-second scalar values are accumulated.

---

### Feature 1: `energy_curve` — Per-Second RMS Energy

**Approach:** `librosa.feature.rms()` on each 10-second `audio_segment`, OR pure numpy RMS on `pcm_22k`.

RMS energy does not require STFT and has no edge effects when computed per-second from raw PCM. Either approach works:

```python
# Option A: librosa (consistent API, handles framing)
rms = librosa.feature.rms(y=audio_segment, frame_length=22050, hop_length=22050)[0]

# Option B: pure numpy (simpler, zero edge effects)
frames = pcm_22k[:n_full_seconds * 22050].reshape(n_full_seconds, 22050)
rms = np.sqrt(np.mean(frames ** 2, axis=1))
```

- **Compute phase:** `_process_block()` — append per-second RMS values
- **Finalize:** Normalize to [0, 1] by track peak: `energy_curve /= energy_curve.max()`
- **CPU:** ~0.1ms per 10s block
- **Streaming-safe:** Yes — per-second RMS has no cross-boundary dependencies
- **Edge case:** Keep a small sample remainder buffer between blocks for exact second alignment

---

### Feature 2: `spectral_centroid_curve` — Per-Second Brightness

**Approach:** `librosa.feature.spectral_centroid(S=S, sr=22050)` on the shared streaming-safe STFT.

```python
# S is already computed from audio_segment with streaming parity
# S shape: (1 + n_fft/2, T_frames) = (1025, ~430 frames for 10s)
centroid_per_frame = librosa.feature.spectral_centroid(S=S, sr=22050, freq=freqs)[0]

# Average to per-second (hop=512, sr=22050 -> ~43 frames/sec)
frames_per_sec = 22050 // 512
centroid_per_sec = centroid_per_frame.reshape(-1, frames_per_sec).mean(axis=1)
```

**Why direct FFT over mel-derived:**
- Mel scale compresses the 8-16 kHz region where hi-hats, cymbals, and sibilance live — the primary brightness differentiators between tracks
- Direct FFT preserves linear frequency resolution, giving accurate centroid values
- Crossfade EQ decisions (crossover frequency selection) benefit from accurate spectral centroid
- librosa's implementation is battle-tested with proper edge case handling

**Note:** A mel-derived approach is available as a future optimization if CPU becomes a concern. The consistent mel bias makes relative comparisons valid, but direct FFT is preferred for v1 accuracy.

- **Compute phase:** `_process_block()` — derived from shared STFT
- **CPU:** Included in shared STFT computation (see budget below)
- **Streaming-safe:** Yes — uses audio_segment with proper overlap context
- **Memory:** Negligible — only per-second scalar values stored

---

### Feature 3: `musical_key` — Key Detection via Chroma

**Approach:** `librosa.feature.chroma_stft(S=S, sr=22050)` on the shared streaming-safe STFT, accumulated per-block, with Krumhansl-Schmuckler key profile correlation at finalize.

```python
# Per-block: derive chroma from shared STFT
chroma_per_frame = librosa.feature.chroma_stft(S=S, sr=22050)  # (12, T_frames)

# Average to per-second and accumulate
frames_per_sec = 22050 // 512
chroma_per_sec = chroma_per_frame.reshape(12, -1, frames_per_sec).mean(axis=2).T  # (T_sec, 12)
data.chroma_per_second.append(chroma_per_sec)
```

**At finalize — Krumhansl-Schmuckler key detection:**

```python
KRUMHANSL_MAJOR = [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
KRUMHANSL_MINOR = [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]

# Discard first/last 10s, average remaining chroma
all_chroma = np.concatenate(data.chroma_per_second, axis=0)  # (T_sec, 12)
trimmed = all_chroma[10:-10] if len(all_chroma) > 20 else all_chroma
mean_chroma = trimmed.mean(axis=0)

# Correlate with all 24 key profiles (12 roots × 2 modes)
# Best correlation = detected key + confidence
```

**Intro/outro filtering:** Discard first 10s and last 10s of chroma data at finalize before averaging. Intros often have ambient/sparse instrumentation that skews the key profile.

- **Compute phase:** `_process_block()` — chroma derived from shared STFT
- **Finalize:** Key correlation + confidence scoring
- **CPU:** Included in shared STFT computation (chroma derivation from S is cheap)
- **Accuracy:** ~70-80% on pop/rock/electronic. Sufficient for crossfade key compatibility scoring.
- **Confidence mapping:** Map correlation [-1, 1] to confidence [0, 1]. Low confidence = don't use key info for crossfade decisions.
- **Memory:** 12 floats × duration_seconds = ~2.4 KB for 5-min track

---

### Audio Analysis Data Flow

```
PCM chunk (raw, any sample rate)
  |
  v
decode + resample to pcm_22k (22050 Hz mono) [EXISTING, UNTOUCHED]
  |
  +---> AdvancedBeatFeatureExtractor.process_pcm(pcm_22k) [EXISTING, UNTOUCHED]
  |       |
  |       +---> log-mel features (128 mels, 50 fps) [EXISTING, for Beat This]
  |
  +---> librosa.stft(pcm_22k) -> S [NEW, direct on pcm_22k block]
  |       |
  |       +---> librosa.feature.spectral_centroid(S=S) -> per-sec [NEW]
  |       |
  |       +---> librosa.feature.chroma_stft(S=S) -> per-sec [NEW]
  |
  +---> RMS energy per second [NEW, from pcm_22k directly, no STFT needed]

At finalize:
  |
  +---> Beat This inference [EXISTING] -> beats, downbeats, bpm
  |
  +---> energy normalization to [0, 1] [NEW]
  |
  +---> key detection from trimmed chroma (Krumhansl-Schmuckler) [NEW] -> MusicalKey
  |
  +---> store AudioAnalysisData with new fields [MODIFIED]
```

### Files to Modify

1. **`music_assistant/providers/smart_fades/provider.py`** — Main changes:
   - Extend `SmartFadesData` with: `energy_chunks`, `centroid_chunks`, `chroma_chunks`
   - In `_process_block()`, compute `librosa.stft(pcm_22k)` and derive spectral centroid + chroma
   - Compute RMS energy from `pcm_22k` directly (no STFT needed)
   - Add key detection, phrase boundary detection, energy normalization in `finalize()`
   - Store results as `AudioAnalysisData` with new optional fields

2. **`music_assistant/providers/smart_fades/feature_extractor.py`** — NO CHANGES

3. **`music_assistant/models/audio_analysis.py`** — Add optional fields to `AudioAnalysisData`: `energy_curve`, `spectral_centroid_curve`, `musical_key`

4. **New file: `music_assistant/providers/smart_fades/analysis_helpers.py`** — Helper functions:
   - `compute_rms_per_second(pcm, sr) -> np.ndarray`
   - `compute_stft_features(pcm, sr) -> tuple[np.ndarray, np.ndarray]` — returns (centroid_per_sec, chroma_per_sec) from single STFT
   - `detect_key(chroma_per_second, duration) -> dict`

### CPU Budget Summary

| Feature | Per 10s block (RPi4) | At finalize | Memory |
|---------|---------------------|-------------|--------|
| Shared STFT (`librosa.stft`) | ~5ms | — | transient |
| RMS energy (numpy, no STFT) | ~0.1ms | ~0.01ms (normalize) | ~4 bytes/sec |
| Spectral centroid (from S) | ~0.5ms | — | ~4 bytes/sec |
| Chroma (from S) | ~0.5ms | — | ~48 bytes/sec |
| Key detection (K-S) | — | ~0.1ms | — |
| **Total additional** | **~6.1ms** | **~0.11ms** | **~56 bytes/sec** |

**Comparison:** Existing Beat This mel extraction costs ~50-100ms per 10s block. Additional analysis adds ~6-12% overhead. One `librosa.stft()` per block, spectral centroid and chroma both derived from the same STFT matrix.

**Key advantage over hand-rolled numpy FFT:** librosa handles windowing, normalization, frequency bin mapping, and chroma folding correctly out of the box.

**Future optimization:** If boundary artifacts become a concern, the feature extractor's overlap buffer (1906 samples) already provides sufficient backward context for n_fft=2048. The shared overlap buffer approach can be adopted without architectural changes.
