# Key Detection Accuracy Improvements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Improve key detection accuracy on pop/electronic music by adding harmonic isolation, switching to genre-appropriate profiles, and adding bass-register tonic disambiguation.

**Architecture:** Three independent changes to `analysis_helpers.py` (HPSS before chroma, Sha'ath profiles, bass chroma extraction + blending) plus wiring in `provider.py` to accumulate and pass bass chroma. All changes are backward-compatible — `detect_key` gains an optional parameter.

**Tech Stack:** Python 3.12+, librosa (HPSS, chroma_cqt), numpy, pytest

**Spec:** `docs/superpowers/specs/2026-03-28-key-detection-accuracy-design.md`

---

### Task 1: Add HPSS to `compute_stft_features`

**Files:**
- Modify: `music_assistant/providers/smart_fades/analysis_helpers.py:85-92`
- Test: `tests/providers/smart_fades/test_analysis_helpers.py`

- [ ] **Step 1: Write failing test for HPSS — verifying chroma is cleaner with percussion**

Add a test that creates a signal with a clear 440Hz tone plus broadband noise (simulating percussion). With HPSS, the A chroma bin should dominate more clearly.

In `tests/providers/smart_fades/test_analysis_helpers.py`, add:

```python
def test_compute_stft_features_harmonic_isolation() -> None:
    """HPSS should produce cleaner chroma even with percussive noise."""
    sr = 22050
    duration = 5
    t = np.linspace(0, duration, sr * duration, endpoint=False, dtype=np.float32)
    # Pure 440Hz tone + broadband clicks every 0.5s (simulating percussion)
    sine = np.sin(2 * np.pi * 440 * t)
    rng = np.random.default_rng(42)
    clicks = np.zeros_like(sine)
    for i in range(0, len(clicks), sr // 2):
        clicks[i : i + 100] = rng.standard_normal(min(100, len(clicks) - i))
    mixed = (sine + 0.5 * clicks).astype(np.float32)

    centroid, chroma, bass_chroma = compute_stft_features(mixed, sr)

    # A (bin 9) should be clearly dominant despite percussion
    mean_chroma = chroma.mean(axis=0)
    assert np.argmax(mean_chroma) == 9, (
        f"Expected A (bin 9) dominant, got bin {np.argmax(mean_chroma)}"
    )
    # The gap between A and the next strongest bin should be meaningful
    sorted_chroma = np.sort(mean_chroma)[::-1]
    assert sorted_chroma[0] > sorted_chroma[1] * 1.2, (
        "A bin should be at least 20% stronger than next bin after HPSS"
    )
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `pytest tests/providers/smart_fades/test_analysis_helpers.py::test_compute_stft_features_harmonic_isolation -v`

Expected: FAIL — `compute_stft_features` returns 2 values, not 3.

- [ ] **Step 3: Implement HPSS and bass chroma in `compute_stft_features`**

In `music_assistant/providers/smart_fades/analysis_helpers.py`, make these changes:

1. Update the return type annotation and docstring:

```python
def compute_stft_features(
    pcm: npt.NDArray[np.float32],
    sr: int = 22050,
    n_fft: int = 2048,
    hop_length: int = 512,
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    """Compute spectral centroid, chroma, and bass chroma from PCM audio.

    Applies harmonic-percussive separation before chroma extraction for
    cleaner pitch-class detection on percussive/electronic tracks.

    :param pcm: Audio samples as float32 array at the given sample rate.
    :param sr: Sample rate in Hz.
    :param n_fft: FFT window size.
    :param hop_length: Hop length between STFT frames.
    :return: Tuple of (centroid_per_second, chroma_per_second, bass_chroma_per_second).
             centroid_per_second shape: (T_seconds,)
             chroma_per_second shape: (T_seconds, 12)
             bass_chroma_per_second shape: (T_seconds, 12)
    """
```

2. Update the early return for short audio:

```python
    if len(pcm) < n_fft:
        empty_centroid = np.array([], dtype=np.float32)
        empty_chroma = np.zeros((0, 12), dtype=np.float32)
        return empty_centroid, empty_chroma, empty_chroma
```

3. After the spectral centroid computation (after line 83), add HPSS and replace the chroma block:

```python
    # Isolate harmonic content for chroma — removes percussive transients
    # that inject broadband energy into all chroma bins.
    # margin=8 is aggressive separation (librosa "enhanced chroma" recipe).
    pcm_harmonic = librosa.effects.harmonic(pcm, margin=8)

    # Full-range chroma via CQT on harmonic signal
    chroma_per_frame = librosa.feature.chroma_cqt(
        y=pcm_harmonic,
        sr=sr,
        hop_length=hop_length,
        tuning=0.0,
    )  # shape: (12, T_frames)

    # Bass-register chroma (C2-B2, ~65-130Hz) for tonic disambiguation
    bass_chroma_per_frame = librosa.feature.chroma_cqt(
        y=pcm_harmonic,
        sr=sr,
        hop_length=hop_length,
        tuning=0.0,
        fmin=librosa.note_to_hz("C2"),
        n_octaves=1,
    )  # shape: (12, T_frames)
```

4. Update the zero-seconds early return:

```python
    if n_full_seconds == 0:
        empty_centroid = np.array([], dtype=np.float32)
        empty_chroma = np.zeros((0, 12), dtype=np.float32)
        return empty_centroid, empty_chroma, empty_chroma
```

5. After the existing chroma averaging block (line 112), add bass chroma averaging:

```python
    trimmed_bass = bass_chroma_per_frame[:, : n_full_seconds * frames_per_sec]
    bass_chroma_per_sec = trimmed_bass.reshape(12, n_full_seconds, frames_per_sec).mean(axis=2).T
```

6. Update the return statement:

```python
    return (
        centroid_per_sec.astype(np.float32),
        chroma_per_sec.astype(np.float32),
        bass_chroma_per_sec.astype(np.float32),
    )
```

- [ ] **Step 4: Fix existing tests for 3-tuple return**

In `tests/providers/smart_fades/test_analysis_helpers.py`:

Update `test_compute_stft_features_sine_wave`:
```python
    centroid_per_sec, chroma_per_sec, bass_chroma_per_sec = compute_stft_features(sine, sr)
```
(rest of the test stays the same)

Update `test_compute_stft_features_empty`:
```python
    centroid, chroma, bass_chroma = compute_stft_features(empty, sr)
    assert len(centroid) == 0
    assert chroma.shape[1] == 12
    assert bass_chroma.shape[1] == 12
```

- [ ] **Step 5: Run all tests to verify they pass**

Run: `pytest tests/providers/smart_fades/test_analysis_helpers.py -v`

Expected: All tests PASS including the new HPSS test.

- [ ] **Step 6: Commit**

```bash
git add music_assistant/providers/smart_fades/analysis_helpers.py tests/providers/smart_fades/test_analysis_helpers.py
git commit -m "feat: add HPSS and bass chroma to compute_stft_features

Isolate harmonic content before chroma extraction to prevent
percussive transients from flattening chroma distributions.
Add bass-register chroma output for tonic disambiguation."
```

---

### Task 2: Switch to Sha'ath key profiles

**Files:**
- Modify: `music_assistant/providers/smart_fades/analysis_helpers.py:117-125`
- Test: `tests/providers/smart_fades/test_analysis_helpers.py`

- [ ] **Step 1: Verify existing key detection tests still pass before changing profiles**

Run: `pytest tests/providers/smart_fades/test_analysis_helpers.py::test_detect_key_c_major tests/providers/smart_fades/test_analysis_helpers.py::test_detect_key_a_minor tests/providers/smart_fades/test_analysis_helpers.py::test_detect_key_filters_intro_outro -v`

Expected: All 3 PASS (baseline).

- [ ] **Step 2: Replace profiles**

In `music_assistant/providers/smart_fades/analysis_helpers.py`, replace the profile constants:

```python
# Sha'ath key profiles (libKeyFinder / Mixxx) — empirically tuned for
# Pearson-correlation key-finding on pop, rock, and electronic music.
# Better genre fit than classical-trained Albrecht-Shanahan profiles.
_KEY_PROFILE_MAJOR = np.array(
    [7.24, 3.50, 3.58, 2.85, 5.82, 4.56, 2.45, 6.99, 3.39, 4.56, 4.07, 4.46]
)
_KEY_PROFILE_MINOR = np.array(
    [7.00, 3.14, 4.36, 5.40, 3.67, 4.09, 3.91, 6.20, 3.63, 2.87, 5.35, 3.83]
)
```

Also update the `detect_key` docstring first line:

```python
    """Detect musical key using Sha'ath profiles with Pearson correlation.
```

- [ ] **Step 3: Run key detection tests to verify they still pass**

Run: `pytest tests/providers/smart_fades/test_analysis_helpers.py::test_detect_key_c_major tests/providers/smart_fades/test_analysis_helpers.py::test_detect_key_a_minor tests/providers/smart_fades/test_analysis_helpers.py::test_detect_key_filters_intro_outro -v`

Expected: All 3 PASS. The synthetic chroma (pure triads) should still detect correctly with Sha'ath profiles.

- [ ] **Step 4: Commit**

```bash
git add music_assistant/providers/smart_fades/analysis_helpers.py
git commit -m "feat: switch to Sha'ath key profiles for pop/electronic accuracy

Replace Albrecht-Shanahan (classical-trained) with Sha'ath profiles
used by KeyFinder and Mixxx, empirically tuned for modern genres."
```

---

### Task 3: Add bass chroma blending to `detect_key`

**Files:**
- Modify: `music_assistant/providers/smart_fades/analysis_helpers.py:129-214`
- Test: `tests/providers/smart_fades/test_analysis_helpers.py`

- [ ] **Step 1: Write failing test for bass-register tonic disambiguation**

This test creates a scenario where the full-range chroma is ambiguous between F major and C major (dominant confusion), but the bass chroma clearly shows F as the tonic.

In `tests/providers/smart_fades/test_analysis_helpers.py`, add:

```python
def test_detect_key_bass_tonic_disambiguation() -> None:
    """Bass chroma should disambiguate tonic from dominant.

    Full-range chroma is ambiguous between F major and C major.
    Bass chroma clearly shows F as the bass note (tonic).
    """
    chroma = np.zeros((20, 12), dtype=np.float32)
    # F major triad with C nearly as strong as F (tonic-dominant ambiguity)
    chroma[:, 5] = 0.9   # F
    chroma[:, 9] = 0.7   # A
    chroma[:, 0] = 0.85  # C — almost as strong as F

    # Bass chroma: F dominates the bass register
    bass_chroma = np.zeros((20, 12), dtype=np.float32)
    bass_chroma[:, 5] = 1.0  # F strong in bass
    bass_chroma[:, 0] = 0.2  # C weak in bass

    key = detect_key(chroma, duration=20.0, bass_chroma_per_second=bass_chroma)

    assert key["root"] == "F", f"Expected F major, got {key['root']} {key['mode']}"
    assert key["mode"] == "major"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/providers/smart_fades/test_analysis_helpers.py::test_detect_key_bass_tonic_disambiguation -v`

Expected: FAIL — `detect_key` does not accept `bass_chroma_per_second` parameter.

- [ ] **Step 3: Implement bass chroma blending in `detect_key`**

In `music_assistant/providers/smart_fades/analysis_helpers.py`, modify `detect_key`:

1. Add the new parameter to the signature:

```python
def detect_key(
    chroma_per_second: npt.NDArray[np.float32],
    duration: float,
    energy_per_second: npt.NDArray[np.float32] | None = None,
    bass_chroma_per_second: npt.NDArray[np.float32] | None = None,
) -> dict:
    """Detect musical key using Sha'ath profiles with Pearson correlation.

    Filters out the first and last 10 seconds of chroma data to avoid
    intro/outro skew from ambient pads or sparse instrumentation.
    When energy_per_second is provided, weights chroma by RMS energy so
    loud sections (choruses, drops) contribute more than quiet breakdowns.
    When bass_chroma_per_second is provided, blends bass-register chroma
    into the main chroma to help disambiguate tonic from dominant.

    :param chroma_per_second: Array of shape (T_seconds, 12) with chroma energy per second.
    :param duration: Total track duration in seconds.
    :param energy_per_second: Optional RMS energy per second for energy-weighted aggregation.
    :param bass_chroma_per_second: Optional bass-register chroma for tonic disambiguation.
    :return: Dict with keys 'root', 'mode', 'confidence' (MusicalKey-compatible).
    """
```

2. After the existing intro/outro trimming block (lines 149-154), add bass chroma trimming:

```python
    if len(chroma_per_second) > 20:
        trimmed = chroma_per_second[10:-10]
        trimmed_energy = energy_per_second[10:-10] if energy_per_second is not None else None
        trimmed_bass = bass_chroma_per_second[10:-10] if bass_chroma_per_second is not None else None
    else:
        trimmed = chroma_per_second
        trimmed_energy = energy_per_second
        trimmed_bass = bass_chroma_per_second
```

3. After the `mean_chroma` computation (after line 166), add bass chroma blending:

```python
    # Blend bass-register chroma to help disambiguate tonic from dominant.
    # The tonic almost always dominates the bass, while the dominant does not.
    if trimmed_bass is not None and len(trimmed_bass) == len(trimmed):
        mean_bass = trimmed_bass.mean(axis=0)
        bass_norm = mean_bass.sum()
        if bass_norm > 1e-10:
            mean_bass = mean_bass / bass_norm
            mean_chroma = 0.8 * mean_chroma + 0.2 * mean_bass
```

This goes right after the energy-weighted aggregation block and before the `mean_chroma.sum() < 1e-10` check.

- [ ] **Step 4: Run all key detection tests**

Run: `pytest tests/providers/smart_fades/test_analysis_helpers.py -v`

Expected: All tests PASS including the new disambiguation test.

- [ ] **Step 5: Commit**

```bash
git add music_assistant/providers/smart_fades/analysis_helpers.py tests/providers/smart_fades/test_analysis_helpers.py
git commit -m "feat: add bass chroma blending for tonic-dominant disambiguation

Blend 20% bass-register chroma into the main chroma vector before
key correlation. The tonic dominates the bass register, which tips
close calls (e.g., F major vs C major) toward the true key."
```

---

### Task 4: Wire bass chroma through `provider.py`

**Files:**
- Modify: `music_assistant/providers/smart_fades/provider.py:79-95` (SmartFadesData)
- Modify: `music_assistant/providers/smart_fades/provider.py:234-239` (finalize)
- Modify: `music_assistant/providers/smart_fades/provider.py:337-347` (_process_block)
- Test: `tests/providers/smart_fades/test_provider.py`

- [ ] **Step 1: Add `bass_chroma_chunks` to `SmartFadesData`**

In `music_assistant/providers/smart_fades/provider.py`, add to the `SmartFadesData` dataclass (after line 95):

```python
    bass_chroma_chunks: list[np.ndarray] = field(default_factory=list)
```

- [ ] **Step 2: Update `_process_block` to accumulate bass chroma**

In `_process_block`, update the `compute_stft_features` call (lines 339-347):

```python
        # Extended analysis: spectral centroid + chroma from shared STFT
        if len(pcm_22k) >= 2048:
            centroid, chroma, bass_chroma = await asyncio.to_thread(
                compute_stft_features,
                pcm_22k,
                ANALYSIS_SAMPLE_RATE,
            )
            if len(centroid) > 0:
                data.centroid_chunks.append(centroid)
            if len(chroma) > 0:
                data.chroma_chunks.append(chroma)
                data.bass_chroma_chunks.append(bass_chroma)
```

- [ ] **Step 3: Update `finalize` to pass bass chroma to `detect_key`**

In the finalize method (lines 234-239):

```python
        musical_key = None
        if data.chroma_chunks:
            chroma_all = np.concatenate(data.chroma_chunks, axis=0)
            bass_chroma_all = (
                np.concatenate(data.bass_chroma_chunks, axis=0)
                if data.bass_chroma_chunks
                else None
            )
            raw_energy = np.concatenate(data.energy_chunks) if data.energy_chunks else None
            musical_key = detect_key(
                chroma_all,
                duration,
                energy_per_second=raw_energy,
                bass_chroma_per_second=bass_chroma_all,
            )
```

- [ ] **Step 4: Run integration tests**

Run: `pytest tests/providers/smart_fades/test_provider.py -v`

Expected: All tests PASS. The integration test validates key structure (root in note names, mode in major/minor, confidence in [0,1]) which still holds.

- [ ] **Step 5: Run all smart_fades tests**

Run: `pytest tests/providers/smart_fades/ -v`

Expected: All tests PASS.

- [ ] **Step 6: Run pre-commit**

Run: `pre-commit run --all-files`

Expected: All checks PASS.

- [ ] **Step 7: Commit**

```bash
git add music_assistant/providers/smart_fades/provider.py
git commit -m "feat: wire bass chroma through provider for key detection

Accumulate bass chroma chunks alongside full-range chroma and pass
them to detect_key for tonic-dominant disambiguation."
```
