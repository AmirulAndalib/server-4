# Key Detection Accuracy Improvements

## Problem

The current key detection in the smart_fades provider is inaccurate on pop and electronic music. Two failure modes identified:

1. **Percussion contamination**: Electronic/percussive tracks produce flat chroma distributions (all 12 bins similarly activated) because drum hits inject broadband energy. Example: "Call on Me" (Eric Prydz) — chroma range 0.394-0.739, best correlation only 0.448, detected D# minor instead of Bb major.

2. **Tonic-dominant confusion**: When the tonic and dominant have similar chroma energy, the algorithm picks the wrong one by a tiny margin. Example: "Fate of Ophelia" (Taylor Swift) — C major 0.826 vs F major 0.823, detected C major instead of F major.

Root causes:
- No harmonic-percussive separation before chroma extraction
- Albrecht-Shanahan key profiles were trained on classical music, not pop/electronic
- No mechanism to disambiguate tonic from dominant

## Solution

Three independent improvements targeting each failure mode.

### 1. HPSS Before Chroma Extraction

**File:** `analysis_helpers.py` — `compute_stft_features()`

Apply `librosa.effects.harmonic(pcm, margin=8)` before `chroma_cqt` to isolate harmonic content and discard percussive transients. `margin=8` is the value from librosa's own "enhanced chroma" example — aggressive separation.

- Only the chroma extraction uses the harmonic signal
- Spectral centroid stays on the original PCM (needs full spectrum for brightness detection)

### 2. Sha'ath (KeyFinder) Key Profiles

**File:** `analysis_helpers.py` — profile constants

Replace Albrecht-Shanahan profiles with Sha'ath profiles (used by KeyFinder and Mixxx):

```python
_KEY_PROFILE_MAJOR = np.array(
    [7.24, 3.50, 3.58, 2.85, 5.82, 4.56, 2.45, 6.99, 3.39, 4.56, 4.07, 4.46]
)
_KEY_PROFILE_MINOR = np.array(
    [7.00, 3.14, 4.36, 5.40, 3.67, 4.09, 3.91, 6.20, 3.63, 2.87, 5.35, 3.83]
)
```

These were empirically tuned for pop/electronic music in DJ software. The profile shape better reflects pitch-class distributions in modern genres compared to Albrecht-Shanahan's classical training corpus.

### 3. Bass-Register Tonic Boost

**Files:** `analysis_helpers.py` — both `compute_stft_features()` and `detect_key()`

The tonic note almost always dominates the bass register. Extract a separate bass-only chroma and blend it into the main chroma to tip close calls.

**In `compute_stft_features()`:**
- Compute a second `chroma_cqt` on the harmonic signal limited to 1 octave (C2-B2, ~65-130Hz) via `fmin=librosa.note_to_hz('C2'), n_octaves=1`
- Average to per-second and return as a third element

**In `detect_key()`:**
- Accept optional `bass_chroma_per_second` parameter
- Blend: `blended = 0.8 * mean_chroma + 0.2 * mean_bass_chroma`
- Run correlation on the blended chroma

## Interface Changes

### `compute_stft_features()`
- **Return type changes** from `tuple[NDArray, NDArray]` to `tuple[NDArray, NDArray, NDArray]`
- Third element is `bass_chroma_per_second` with shape `(T_seconds, 12)`

### `detect_key()`
- **New optional parameter:** `bass_chroma_per_second: NDArray | None = None`

### Provider (`provider.py`)
- Accumulate `bass_chroma_chunks` alongside existing `chroma_chunks`
- Pass concatenated bass chroma to `detect_key()`

## Test Updates

- `test_compute_stft_features_sine_wave`: unpack 3 return values instead of 2
- `test_compute_stft_features_empty`: verify bass chroma shape `(0, 12)`
- `test_detect_key_*`: existing tests should still pass (bass chroma is optional, HPSS on synthetic sine is a no-op)
- Consider adding a test with synthetic percussion + tonal content to verify HPSS helps

## Non-Goals

- Switching to `chroma_cens` — it normalizes away dynamic information that the energy-weighted aggregation intentionally leverages
- Genre-specific profile selection — a single profile set that works well across pop/rock/electronic is sufficient
- Real-time latency optimization — accuracy is the priority, processing happens during streaming
