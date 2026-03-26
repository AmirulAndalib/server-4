# Step 1: Smart Fades Provider Enhancements — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add energy curve, spectral centroid, musical key, and phrase boundary computation to the existing SmartFadesProvider using librosa, piggybacking on the shared overlap buffer.

**Architecture:** The feature extractor exposes its `audio_segment` (already constructed with overlap context) alongside log-mel features. The provider computes one `librosa.stft()` per 10s block on that segment, then derives spectral centroid, chroma, and RMS from it. At finalize, key detection (Krumhansl-Schmuckler) and phrase boundary detection (4/8/16-bar heuristic with energy + centroid deltas) run on the accumulated data. Results are stored as `AudioAnalysisData` with the new optional fields.

**Tech Stack:** Python 3.12+, librosa 0.11.0 (existing dependency), numpy, torchaudio (existing)

**Spec:** `docs/superpowers/specs/2026-03-26-smart-crossfade-improvements-design.md` — "Audio Analysis Provider Design" section

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `music_assistant/models/audio_analysis.py` | Modify | Add optional fields: `energy_curve`, `spectral_centroid_curve`, `phrase_boundaries`, `musical_key` |
| `music_assistant/providers/smart_fades/feature_extractor.py` | Modify | Return `(log_mel_features, audio_segment)` tuple from `process_pcm()` and `finalize()` |
| `music_assistant/providers/smart_fades/provider.py` | Modify | Extend `SmartFadesData`, add librosa STFT + feature extraction in `_process_block()`, add key/phrase detection in `finalize()` |
| `music_assistant/providers/smart_fades/analysis_helpers.py` | Create | Helper functions: RMS, STFT features, key detection, phrase boundaries |
| `tests/providers/smart_fades/test_analysis_helpers.py` | Create | Unit tests for all helper functions |
| `tests/providers/smart_fades/test_provider.py` | Modify | Extend existing integration test to verify new fields |

---

### Task 1: Add optional fields to AudioAnalysisData

**Files:**
- Modify: `music_assistant/models/audio_analysis.py:13-44`

- [ ] **Step 1: Add the new fields**

```python
# In music_assistant/models/audio_analysis.py, add imports and fields.
# After line 9 (mashumaro.config import), add:
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from music_assistant.models.smart_fades import MusicalKey, PhraseBoundary

# Inside AudioAnalysisData class, after line 27 (duration field), add:

    # Extended analysis (populated by smart fades provider)
    energy_curve: npt.NDArray[np.float32] | None = None
    spectral_centroid_curve: npt.NDArray[np.float32] | None = None
    phrase_boundaries: list | None = None  # list[PhraseBoundary] at runtime
    musical_key: dict | None = None  # MusicalKey as dict for serialization
```

Note: We use `list` and `dict` instead of the concrete types to avoid circular imports. The smart_fades models import from here. At runtime, phrase_boundaries contains `PhraseBoundary` dicts and musical_key contains a `MusicalKey` dict (both are DataClassDictMixin so they serialize to dicts).

- [ ] **Step 2: Run pre-commit to verify**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 3: Run existing tests to verify no regression**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_provider.py -v`
Expected: `test_beat_detection` PASSES (existing behavior unchanged — new fields default to None)

- [ ] **Step 4: Commit**

```bash
git add music_assistant/models/audio_analysis.py
git commit -m "feat: add energy, spectral, phrase, key fields to AudioAnalysisData"
```

---

### Task 2: Expose audio_segment from feature extractor

**Files:**
- Modify: `music_assistant/providers/smart_fades/feature_extractor.py:94-198`
- Modify: `music_assistant/providers/smart_fades/provider.py:272-298`

- [ ] **Step 1: Modify process_pcm() return type**

In `feature_extractor.py`, change `process_pcm` (line 94) to return `tuple[np.ndarray, np.ndarray]` — `(log_mel_features, audio_segment)`.

```python
    async def process_pcm(self, pcm: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Process a PCM chunk and return log-mel features and the overlap-corrected audio segment.

        :param pcm: Audio samples as float32 array.
        :return: Tuple of (log-mel features with shape (T, n_mels), audio_segment with overlap).
        """

        def _process_sync() -> tuple[np.ndarray, np.ndarray]:
            chunk_start = self._total_samples
            chunk_end = chunk_start + len(pcm)

            # Determine which frames belong to this chunk.
            if chunk_start == 0:
                first_frame = 0
            elif self._last_output_frame >= 0:
                first_frame = self._last_output_frame + 1
            else:
                first_frame = (chunk_start + self.hop_length - 1) // self.hop_length

            last_frame = (chunk_end - 1) // self.hop_length
            output_last_frame = last_frame - self._frames_to_delay

            if output_last_frame < first_frame:
                self._prev_samples = pcm[-self._keep_samples :].copy()
                self._total_samples = chunk_end
                empty = np.array([], dtype=np.float32).reshape(0, self._n_mels)
                return empty, np.array([], dtype=np.float32)

            needed_start = max(0, first_frame * self.hop_length - self.n_fft // 2)
            audio_start = (needed_start // self.hop_length) * self.hop_length

            if self._prev_samples is not None and audio_start < chunk_start:
                prev_needed = chunk_start - audio_start
                prev_to_use = self._prev_samples[-prev_needed:]
                audio_segment = np.concatenate([prev_to_use, pcm])
            else:
                audio_segment = pcm
                audio_start = chunk_start

            self._prev_samples = pcm[-self._keep_samples :].copy()
            self._total_samples = chunk_end

            tensor = torch.from_numpy(audio_segment).to(self._device)
            with torch.no_grad():
                mel = self._mel_spec(tensor)
                log_mel = torch.log1p(1000.0 * mel)
            features = log_mel.T.cpu().numpy().astype(np.float32)

            segment_first_global_frame = audio_start // self.hop_length
            start_in_segment = first_frame - segment_first_global_frame
            end_in_segment = output_last_frame - segment_first_global_frame + 1
            start_in_segment = max(0, start_in_segment)
            end_in_segment = min(len(features), end_in_segment)

            self._last_output_frame = output_last_frame

            return features[start_in_segment:end_in_segment], audio_segment

        return await asyncio.to_thread(_process_sync)
```

- [ ] **Step 2: Modify finalize() return type**

In `feature_extractor.py`, change `finalize` (line 174) to also return the audio segment:

```python
    async def finalize(self) -> tuple[np.ndarray, np.ndarray]:
        """Flush delayed frames and process any remaining samples.

        :return: Tuple of (final log-mel features, audio_segment used).
        """

        def _finalize_sync() -> tuple[np.ndarray, np.ndarray]:
            if self._prev_samples is None or len(self._prev_samples) == 0:
                empty = np.array([], dtype=np.float32).reshape(0, self._n_mels)
                return empty, np.array([], dtype=np.float32)

            total_frames = 1 + self._total_samples // self.hop_length
            extra_count = total_frames - self._last_output_frame - 1
            if extra_count <= 0:
                empty = np.array([], dtype=np.float32).reshape(0, self._n_mels)
                return empty, self._prev_samples.copy()

            tensor = torch.from_numpy(self._prev_samples).to(self._device)
            with torch.no_grad():
                mel = self._mel_spec(tensor)
                log_mel = torch.log1p(1000.0 * mel)
            features = log_mel.T.cpu().numpy().astype(np.float32)

            return features[-extra_count:], self._prev_samples.copy()

        return await asyncio.to_thread(_finalize_sync)
```

- [ ] **Step 3: Update provider._process_block() to unpack the tuple**

In `provider.py`, update `_process_block` (line 293) to handle the new return type:

```python
        # Change line 293 from:
        # feats = await data.features.process_pcm(pcm_22k)
        # To:
        feats, _audio_segment = await data.features.process_pcm(pcm_22k)
```

- [ ] **Step 4: Update provider.finalize() to unpack the tuple**

In `provider.py`, update `finalize` (line 189):

```python
        # Change line 189 from:
        # final_feats = await data.features.finalize()
        # To:
        final_feats, _final_segment = await data.features.finalize()
```

- [ ] **Step 5: Run tests to verify no regression**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_provider.py -v`
Expected: `test_beat_detection` PASSES (return type changed but existing behavior preserved)

- [ ] **Step 6: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 7: Commit**

```bash
git add music_assistant/providers/smart_fades/feature_extractor.py music_assistant/providers/smart_fades/provider.py
git commit -m "feat: expose audio_segment from feature extractor for librosa analysis"
```

---

### Task 3: Create analysis_helpers.py with RMS energy

**Files:**
- Create: `music_assistant/providers/smart_fades/analysis_helpers.py`
- Create: `tests/providers/smart_fades/test_analysis_helpers.py`

- [ ] **Step 1: Write the failing test for RMS energy**

Create `tests/providers/smart_fades/test_analysis_helpers.py`:

```python
"""Tests for smart fades analysis helper functions."""

import numpy as np
import pytest

from music_assistant.providers.smart_fades.analysis_helpers import compute_rms_per_second


def test_compute_rms_per_second_sine_wave() -> None:
    """RMS of a sine wave at known amplitude should be amplitude / sqrt(2)."""
    sr = 22050
    duration = 5  # seconds
    amplitude = 0.5
    t = np.linspace(0, duration, sr * duration, endpoint=False, dtype=np.float32)
    sine = amplitude * np.sin(2 * np.pi * 440 * t)

    rms = compute_rms_per_second(sine, sr)

    assert len(rms) == duration
    expected_rms = amplitude / np.sqrt(2)
    for val in rms:
        assert abs(val - expected_rms) < 0.01, f"Expected ~{expected_rms:.3f}, got {val:.3f}"


def test_compute_rms_per_second_silence() -> None:
    """RMS of silence should be zero."""
    sr = 22050
    silence = np.zeros(sr * 3, dtype=np.float32)

    rms = compute_rms_per_second(silence, sr)

    assert len(rms) == 3
    for val in rms:
        assert val == 0.0


def test_compute_rms_per_second_partial_second() -> None:
    """Samples less than 1 second should return empty array."""
    sr = 22050
    short = np.ones(sr // 2, dtype=np.float32)

    rms = compute_rms_per_second(short, sr)

    assert len(rms) == 0
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_analysis_helpers.py::test_compute_rms_per_second_sine_wave -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'music_assistant.providers.smart_fades.analysis_helpers'`

- [ ] **Step 3: Create analysis_helpers.py with compute_rms_per_second**

Create `music_assistant/providers/smart_fades/analysis_helpers.py`:

```python
"""Helper functions for extended audio analysis features.

Computes energy curves, spectral centroids, chroma/key detection,
and phrase boundary detection from streaming PCM audio. Uses librosa
for STFT-based features piggybacking on the shared overlap buffer
from AdvancedBeatFeatureExtractor.
"""

from __future__ import annotations

import numpy as np
import numpy.typing as npt


def compute_rms_per_second(
    pcm: npt.NDArray[np.float32],
    sr: int = 22050,
) -> npt.NDArray[np.float32]:
    """Compute RMS energy per second from PCM audio.

    No STFT involved — pure amplitude computation with zero edge effects.
    Streaming-safe: each second is independent.

    :param pcm: Audio samples as float32 array at the given sample rate.
    :param sr: Sample rate in Hz.
    :return: Array of RMS values, one per full second of audio.
    """
    n_full_seconds = len(pcm) // sr
    if n_full_seconds == 0:
        return np.array([], dtype=np.float32)
    trimmed = pcm[: n_full_seconds * sr]
    frames = trimmed.reshape(n_full_seconds, sr)
    return np.sqrt(np.mean(frames**2, axis=1)).astype(np.float32)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_analysis_helpers.py -v`
Expected: All 3 tests PASS

- [ ] **Step 5: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add music_assistant/providers/smart_fades/analysis_helpers.py tests/providers/smart_fades/test_analysis_helpers.py
git commit -m "feat: add compute_rms_per_second helper for energy curve analysis"
```

---

### Task 4: Add STFT-based feature extraction (spectral centroid + chroma)

**Files:**
- Modify: `music_assistant/providers/smart_fades/analysis_helpers.py`
- Modify: `tests/providers/smart_fades/test_analysis_helpers.py`

- [ ] **Step 1: Write the failing test for STFT feature extraction**

Add to `tests/providers/smart_fades/test_analysis_helpers.py`:

```python
from music_assistant.providers.smart_fades.analysis_helpers import (
    LibrosaFrameTracker,
    compute_rms_per_second,
    compute_stft_features,
)


def test_compute_stft_features_sine_wave() -> None:
    """Spectral centroid of a 440 Hz sine should be close to 440 Hz."""
    sr = 22050
    duration = 5
    t = np.linspace(0, duration, sr * duration, endpoint=False, dtype=np.float32)
    sine = np.sin(2 * np.pi * 440 * t)

    tracker = LibrosaFrameTracker(sr=sr)
    centroid_per_sec, chroma_per_sec = compute_stft_features(sine, sr, tracker)

    assert len(centroid_per_sec) == duration
    # Spectral centroid of a pure 440 Hz tone should be ~440 Hz
    for val in centroid_per_sec:
        assert 400 < val < 480, f"Expected centroid ~440 Hz, got {val:.1f}"

    # Chroma should have 12 bins per second
    assert chroma_per_sec.shape == (duration, 12)
    # 440 Hz = A4, which is chroma bin 9 (A). Should be dominant.
    mean_chroma = chroma_per_sec.mean(axis=0)
    assert np.argmax(mean_chroma) == 9, f"Expected A (bin 9) dominant, got bin {np.argmax(mean_chroma)}"


def test_compute_stft_features_empty() -> None:
    """Empty audio should return empty arrays."""
    sr = 22050
    empty = np.array([], dtype=np.float32)
    tracker = LibrosaFrameTracker(sr=sr)
    centroid, chroma = compute_stft_features(empty, sr, tracker)
    assert len(centroid) == 0
    assert chroma.shape[1] == 12


def test_librosa_frame_tracker_delay() -> None:
    """Frame tracker should delay frames for streaming parity."""
    tracker = LibrosaFrameTracker(sr=22050, n_fft=2048, hop_length=512)
    assert tracker.frames_to_delay == 2  # ceil(1024 / 512)
    assert tracker.last_output_frame == -1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_analysis_helpers.py::test_compute_stft_features_sine_wave -v`
Expected: FAIL — `ImportError: cannot import name 'compute_stft_features'`

- [ ] **Step 3: Implement LibrosaFrameTracker and compute_stft_features**

Add to `music_assistant/providers/smart_fades/analysis_helpers.py`:

```python
import math
from dataclasses import dataclass, field

import librosa


@dataclass
class LibrosaFrameTracker:
    """Track frame positions for streaming-safe librosa STFT.

    Mirrors the delay-and-recompute pattern from AdvancedBeatFeatureExtractor
    but for librosa's STFT params (n_fft=2048, hop=512 by default).
    """

    sr: int = 22050
    n_fft: int = 2048
    hop_length: int = 512
    last_output_frame: int = -1
    total_samples: int = 0
    frames_to_delay: int = field(init=False)

    def __post_init__(self) -> None:
        """Compute frames_to_delay from STFT params."""
        self.frames_to_delay = math.ceil((self.n_fft // 2) / self.hop_length)


def compute_stft_features(
    audio_segment: npt.NDArray[np.float32],
    sr: int,
    tracker: LibrosaFrameTracker,
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    """Compute spectral centroid and chroma from a shared librosa STFT.

    Uses the audio_segment (with overlap context from the feature extractor)
    to compute a streaming-safe STFT. Derives per-second spectral centroid
    and per-second 12-bin chroma from the same STFT matrix.

    :param audio_segment: PCM float32 array with overlap context from feature extractor.
    :param sr: Sample rate (should be 22050).
    :param tracker: Frame tracker for streaming parity.
    :return: Tuple of (centroid_per_second, chroma_per_second).
             centroid_per_second shape: (T_seconds,)
             chroma_per_second shape: (T_seconds, 12)
    """
    if len(audio_segment) < tracker.n_fft:
        empty_centroid = np.array([], dtype=np.float32)
        empty_chroma = np.zeros((0, 12), dtype=np.float32)
        return empty_centroid, empty_chroma

    # Compute STFT once — all features derived from this
    stft_matrix = np.abs(librosa.stft(
        y=audio_segment,
        n_fft=tracker.n_fft,
        hop_length=tracker.hop_length,
        center=True,
    ))

    # Extract frame range for this block (parallel to feature_extractor logic)
    n_frames = stft_matrix.shape[1]
    if tracker.last_output_frame < 0:
        first_frame = 0
    else:
        # Frames from audio_segment include overlap; compute offset
        first_frame = 0  # We process all frames from the segment

    # Delay last frames for recompute with next block's forward context
    output_end = n_frames - tracker.frames_to_delay
    if output_end <= 0:
        empty_centroid = np.array([], dtype=np.float32)
        empty_chroma = np.zeros((0, 12), dtype=np.float32)
        return empty_centroid, empty_chroma

    stft_block = stft_matrix[:, :output_end]

    # Spectral centroid per frame, then average to per-second
    centroid_per_frame = librosa.feature.spectral_centroid(
        S=stft_block, sr=sr, n_fft=tracker.n_fft, hop_length=tracker.hop_length,
    )[0]

    # Chroma per frame
    chroma_per_frame = librosa.feature.chroma_stft(
        S=stft_block, sr=sr, n_fft=tracker.n_fft, hop_length=tracker.hop_length,
    )  # shape: (12, T_frames)

    # Average to per-second
    frames_per_sec = sr // tracker.hop_length
    if frames_per_sec == 0:
        frames_per_sec = 1
    n_full_seconds = len(centroid_per_frame) // frames_per_sec

    if n_full_seconds == 0:
        empty_centroid = np.array([], dtype=np.float32)
        empty_chroma = np.zeros((0, 12), dtype=np.float32)
        return empty_centroid, empty_chroma

    trimmed_centroid = centroid_per_frame[: n_full_seconds * frames_per_sec]
    centroid_per_sec = trimmed_centroid.reshape(n_full_seconds, frames_per_sec).mean(axis=1)

    trimmed_chroma = chroma_per_frame[:, : n_full_seconds * frames_per_sec]
    chroma_per_sec = trimmed_chroma.reshape(12, n_full_seconds, frames_per_sec).mean(axis=2).T

    tracker.last_output_frame += output_end

    return centroid_per_sec.astype(np.float32), chroma_per_sec.astype(np.float32)
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_analysis_helpers.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add music_assistant/providers/smart_fades/analysis_helpers.py tests/providers/smart_fades/test_analysis_helpers.py
git commit -m "feat: add librosa STFT-based spectral centroid and chroma extraction"
```

---

### Task 5: Add key detection helper

**Files:**
- Modify: `music_assistant/providers/smart_fades/analysis_helpers.py`
- Modify: `tests/providers/smart_fades/test_analysis_helpers.py`

- [ ] **Step 1: Write the failing test for key detection**

Add to `tests/providers/smart_fades/test_analysis_helpers.py`:

```python
from music_assistant.providers.smart_fades.analysis_helpers import detect_key


def test_detect_key_c_major() -> None:
    """Chroma weighted toward C, E, G should detect C major."""
    # 20 seconds of chroma, one per second
    chroma = np.zeros((20, 12), dtype=np.float32)
    # C=0, E=4, G=7 are the C major triad
    chroma[:, 0] = 1.0  # C
    chroma[:, 4] = 0.8  # E
    chroma[:, 7] = 0.6  # G

    key = detect_key(chroma, duration=20.0)

    assert key["root"] == "C"
    assert key["mode"] == "major"
    assert key["confidence"] > 0.5


def test_detect_key_a_minor() -> None:
    """Chroma weighted toward A, C, E should detect A minor."""
    chroma = np.zeros((20, 12), dtype=np.float32)
    # A=9, C=0, E=4 are the A minor triad
    chroma[:, 9] = 1.0  # A
    chroma[:, 0] = 0.8  # C
    chroma[:, 4] = 0.6  # E

    key = detect_key(chroma, duration=20.0)

    assert key["root"] == "A"
    assert key["mode"] == "minor"
    assert key["confidence"] > 0.5


def test_detect_key_filters_intro_outro() -> None:
    """First and last 10s should be excluded from key detection."""
    # 30 seconds of chroma: first 10s = F major, middle 10s = C major, last 10s = F major
    chroma = np.zeros((30, 12), dtype=np.float32)
    # First/last 10s: F major (F=5, A=9, C=0)
    chroma[:10, 5] = 1.0
    chroma[:10, 9] = 0.8
    chroma[:10, 0] = 0.6
    chroma[20:, 5] = 1.0
    chroma[20:, 9] = 0.8
    chroma[20:, 0] = 0.6
    # Middle 10s: C major
    chroma[10:20, 0] = 1.0
    chroma[10:20, 4] = 0.8
    chroma[10:20, 7] = 0.6

    key = detect_key(chroma, duration=30.0)

    # Should detect C major (middle section), not F major (intro/outro)
    assert key["root"] == "C"
    assert key["mode"] == "major"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_analysis_helpers.py::test_detect_key_c_major -v`
Expected: FAIL — `ImportError: cannot import name 'detect_key'`

- [ ] **Step 3: Implement detect_key**

Add to `music_assistant/providers/smart_fades/analysis_helpers.py`:

```python
# Module-level constants for Krumhansl-Schmuckler key profiles
_KRUMHANSL_MAJOR = np.array(
    [6.35, 2.23, 3.48, 2.33, 4.38, 4.09, 2.52, 5.19, 2.39, 3.66, 2.29, 2.88]
)
_KRUMHANSL_MINOR = np.array(
    [6.33, 2.68, 3.52, 5.38, 2.60, 3.53, 2.54, 4.75, 3.98, 2.69, 3.34, 3.17]
)
_NOTE_NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def detect_key(
    chroma_per_second: npt.NDArray[np.float32],
    duration: float,
) -> dict:
    """Detect musical key using Krumhansl-Schmuckler algorithm.

    Filters out the first and last 10 seconds of chroma data to avoid
    intro/outro skew from ambient pads or sparse instrumentation.

    :param chroma_per_second: Array of shape (T_seconds, 12) with chroma energy per second.
    :param duration: Total track duration in seconds.
    :return: Dict with keys 'root', 'mode', 'confidence' (MusicalKey-compatible).
    """
    if len(chroma_per_second) == 0:
        return {"root": "C", "mode": "major", "confidence": 0.0}

    # Filter out first/last 10 seconds
    if len(chroma_per_second) > 20:
        trimmed = chroma_per_second[10:-10]
    else:
        trimmed = chroma_per_second

    mean_chroma = trimmed.mean(axis=0)

    if mean_chroma.sum() < 1e-10:
        return {"root": "C", "mode": "major", "confidence": 0.0}

    best_corr = -2.0
    best_root = 0
    best_mode = "major"

    for shift in range(12):
        rotated = np.roll(mean_chroma, -shift)
        corr_major = float(np.corrcoef(rotated, _KRUMHANSL_MAJOR)[0, 1])
        corr_minor = float(np.corrcoef(rotated, _KRUMHANSL_MINOR)[0, 1])
        if corr_major > best_corr:
            best_corr = corr_major
            best_root = shift
            best_mode = "major"
        if corr_minor > best_corr:
            best_corr = corr_minor
            best_root = shift
            best_mode = "minor"

    confidence = max(0.0, min(1.0, (best_corr + 1.0) / 2.0))

    return {
        "root": _NOTE_NAMES[best_root],
        "mode": best_mode,
        "confidence": round(confidence, 3),
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_analysis_helpers.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add music_assistant/providers/smart_fades/analysis_helpers.py tests/providers/smart_fades/test_analysis_helpers.py
git commit -m "feat: add Krumhansl-Schmuckler key detection with intro/outro filtering"
```

---

### Task 6: Add phrase boundary detection helper

**Files:**
- Modify: `music_assistant/providers/smart_fades/analysis_helpers.py`
- Modify: `tests/providers/smart_fades/test_analysis_helpers.py`

- [ ] **Step 1: Write the failing test for phrase boundary detection**

Add to `tests/providers/smart_fades/test_analysis_helpers.py`:

```python
from music_assistant.providers.smart_fades.analysis_helpers import detect_phrase_boundaries


def test_detect_phrase_boundaries_energy_drop() -> None:
    """Should detect a phrase boundary at an 8-bar downbeat with energy drop."""
    bpm = 120.0
    bar_duration = 4 * (60.0 / bpm)  # 2.0 seconds per bar
    # 32 downbeats = 32 bars, each 2s apart
    downbeats = np.arange(32) * bar_duration

    # Energy: high for first 16 bars, drops to low for last 16 bars
    duration_sec = int(32 * bar_duration)  # 64 seconds
    energy = np.ones(duration_sec, dtype=np.float32) * 0.8
    energy[32:] = 0.2  # Drop at bar 16 (second 32)
    centroid = np.ones(duration_sec, dtype=np.float32) * 1000.0

    boundaries = detect_phrase_boundaries(downbeats, energy, centroid, bpm)

    # Should find a boundary near second 32 (bar 16, which is a 16-bar boundary)
    boundary_times = [b["time"] for b in boundaries]
    assert any(abs(t - 32.0) < 2.0 for t in boundary_times), (
        f"Expected boundary near 32.0s, got {boundary_times}"
    )


def test_detect_phrase_boundaries_spectral_change() -> None:
    """Should detect a boundary from spectral centroid change even without energy change."""
    bpm = 120.0
    bar_duration = 4 * (60.0 / bpm)
    downbeats = np.arange(32) * bar_duration

    duration_sec = int(32 * bar_duration)
    energy = np.ones(duration_sec, dtype=np.float32) * 0.5  # Flat energy
    centroid = np.ones(duration_sec, dtype=np.float32) * 500.0
    centroid[32:] = 2000.0  # Big spectral jump at bar 16

    boundaries = detect_phrase_boundaries(downbeats, energy, centroid, bpm)

    boundary_times = [b["time"] for b in boundaries]
    assert any(abs(t - 32.0) < 2.0 for t in boundary_times), (
        f"Expected boundary near 32.0s from spectral change, got {boundary_times}"
    )


def test_detect_phrase_boundaries_too_few_downbeats() -> None:
    """Should return empty list with fewer than 4 downbeats."""
    downbeats = np.array([0.0, 2.0, 4.0])
    energy = np.ones(10, dtype=np.float32)
    centroid = np.ones(10, dtype=np.float32)

    boundaries = detect_phrase_boundaries(downbeats, energy, centroid, 120.0)

    assert boundaries == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_analysis_helpers.py::test_detect_phrase_boundaries_energy_drop -v`
Expected: FAIL — `ImportError: cannot import name 'detect_phrase_boundaries'`

- [ ] **Step 3: Implement detect_phrase_boundaries**

Add to `music_assistant/providers/smart_fades/analysis_helpers.py`:

```python
def detect_phrase_boundaries(
    downbeats: npt.NDArray[np.float64],
    energy_curve: npt.NDArray[np.float32],
    centroid_curve: npt.NDArray[np.float32],
    bpm: float,
) -> list[dict]:
    """Detect phrase/section boundaries from structural features.

    Checks 4-bar, 8-bar, and 16-bar downbeat boundaries. Scores each by
    combined energy delta + spectral centroid delta. Lower thresholds for
    longer bar groupings (stronger structural prior).

    :param downbeats: Array of downbeat timestamps in seconds.
    :param energy_curve: Normalized [0,1] RMS energy per second.
    :param centroid_curve: Spectral centroid (Hz) per second.
    :param bpm: Track BPM.
    :return: List of PhraseBoundary-compatible dicts with 'time', 'confidence', 'boundary_type'.
    """
    if len(downbeats) < 4:
        return []

    boundaries: list[dict] = []

    for i, db_time in enumerate(downbeats):
        is_16bar = i % 16 == 0
        is_8bar = i % 8 == 0
        is_4bar = i % 4 == 0

        if not is_4bar:
            continue

        sec_idx = int(db_time)
        if sec_idx < 2 or sec_idx >= len(energy_curve) - 2:
            continue

        # Energy delta (2s window before/after)
        e_before = float(np.mean(energy_curve[max(0, sec_idx - 2) : sec_idx]))
        e_after = float(np.mean(energy_curve[sec_idx : min(len(energy_curve), sec_idx + 2)]))
        energy_delta = abs(e_after - e_before) / max(e_before, 1e-10)

        # Spectral centroid delta
        if sec_idx < len(centroid_curve) - 2:
            c_before = float(np.mean(centroid_curve[max(0, sec_idx - 2) : sec_idx]))
            c_after = float(np.mean(centroid_curve[sec_idx : min(len(centroid_curve), sec_idx + 2)]))
            centroid_delta = abs(c_after - c_before) / max(c_before, 1e-10)
        else:
            centroid_delta = 0.0

        combined_score = 0.6 * energy_delta + 0.4 * centroid_delta

        if is_16bar and combined_score > 0.15:
            boundaries.append({
                "time": float(db_time),
                "confidence": round(min(1.0, combined_score), 3),
                "boundary_type": "section",
            })
        elif is_8bar and combined_score > 0.25:
            boundary_type = "section" if combined_score > 0.5 else "phrase"
            boundaries.append({
                "time": float(db_time),
                "confidence": round(min(1.0, combined_score * 0.9), 3),
                "boundary_type": boundary_type,
            })
        elif is_4bar and combined_score > 0.4:
            boundaries.append({
                "time": float(db_time),
                "confidence": round(min(1.0, combined_score * 0.7), 3),
                "boundary_type": "phrase",
            })

    return boundaries
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_analysis_helpers.py -v`
Expected: All tests PASS

- [ ] **Step 5: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 6: Commit**

```bash
git add music_assistant/providers/smart_fades/analysis_helpers.py tests/providers/smart_fades/test_analysis_helpers.py
git commit -m "feat: add phrase boundary detection with energy + centroid deltas"
```

---

### Task 7: Integrate analysis features into SmartFadesProvider

**Files:**
- Modify: `music_assistant/providers/smart_fades/provider.py:73-86` (SmartFadesData)
- Modify: `music_assistant/providers/smart_fades/provider.py:272-298` (_process_block)
- Modify: `music_assistant/providers/smart_fades/provider.py:175-234` (finalize)

- [ ] **Step 1: Extend SmartFadesData with new accumulator fields**

In `provider.py`, add fields to `SmartFadesData` (after line 86):

```python
@dataclass
class SmartFadesData:
    """Per-session data for smart fades analysis."""

    item_id: str
    provider: str
    input_audio_format: AudioFormat
    block_samples: int
    features: AdvancedBeatFeatureExtractor
    resampler: soxr.ResampleStream | None = None
    pcm_buffer: list[np.ndarray] = field(default_factory=list)
    pcm_samples: int = 0
    total_pcm_samples: int = 0
    feature_blocks: list[np.ndarray] = field(default_factory=list)
    # Extended analysis accumulators
    energy_chunks: list[np.ndarray] = field(default_factory=list)
    centroid_chunks: list[np.ndarray] = field(default_factory=list)
    chroma_chunks: list[np.ndarray] = field(default_factory=list)
    librosa_tracker: LibrosaFrameTracker | None = None
```

Add the import at the top of `provider.py`:

```python
from .analysis_helpers import (
    LibrosaFrameTracker,
    compute_rms_per_second,
    compute_stft_features,
    detect_key,
    detect_phrase_boundaries,
)
```

- [ ] **Step 2: Initialize librosa_tracker in start_analysis**

In `provider.py`, in `start_analysis()` (around line 145 where `SmartFadesData` is constructed), ensure `librosa_tracker` is initialized:

```python
        data = SmartFadesData(
            item_id=stream_details.item_id,
            provider=stream_details.provider,
            input_audio_format=audio_format,
            block_samples=block_samples,
            features=AdvancedBeatFeatureExtractor(device=self._device),
            resampler=resampler,
            librosa_tracker=LibrosaFrameTracker(sr=ANALYSIS_SAMPLE_RATE),
        )
```

- [ ] **Step 3: Add feature extraction to _process_block**

In `provider.py`, update `_process_block` to compute features from the audio_segment:

```python
    async def _process_block(self, data: SmartFadesData, *, last: bool = False) -> None:
        """Resample accumulated PCM buffer and extract features."""
        pcm_raw = np.concatenate(data.pcm_buffer)
        data.pcm_buffer.clear()
        data.pcm_samples = 0

        if data.resampler is not None:
            pcm_22k = await asyncio.to_thread(data.resampler.resample_chunk, pcm_raw, last)
        else:
            pcm_22k = pcm_raw

        data.total_pcm_samples += len(pcm_22k)

        start_time = time.perf_counter()
        feats, audio_segment = await data.features.process_pcm(pcm_22k)
        elapsed_ms = (time.perf_counter() - start_time) * 1000

        if feats.size:
            data.feature_blocks.append(feats)

        # Extended analysis: RMS energy from raw PCM (no STFT, streaming-safe)
        rms = compute_rms_per_second(pcm_22k, ANALYSIS_SAMPLE_RATE)
        if len(rms) > 0:
            data.energy_chunks.append(rms)

        # Extended analysis: spectral centroid + chroma from shared STFT
        if len(audio_segment) >= 2048 and data.librosa_tracker is not None:
            centroid, chroma = await asyncio.to_thread(
                compute_stft_features, audio_segment, ANALYSIS_SAMPLE_RATE, data.librosa_tracker,
            )
            if len(centroid) > 0:
                data.centroid_chunks.append(centroid)
            if len(chroma) > 0:
                data.chroma_chunks.append(chroma)

        self.logger.debug("Processed 10s of PCM chunks in %.1fms", elapsed_ms)
```

- [ ] **Step 4: Update finalize to compute and store extended analysis**

In `provider.py`, update `finalize()` to run key detection and phrase boundaries after Beat This inference:

```python
    async def finalize(self, session_id: str) -> None:
        """Finalize beat tracking and store results."""
        data = self._data.pop(session_id, None)
        if not data:
            return

        # Flush remaining buffered PCM
        if data.pcm_samples:
            await self._process_block(data, last=True)

        # Get final features with end padding
        final_feats, _final_segment = await data.features.finalize()
        if final_feats.size:
            data.feature_blocks.append(final_feats)

        if not data.feature_blocks:
            return

        feats = np.concatenate(data.feature_blocks, axis=0)

        self.logger.debug(
            "Running model inference on %d frames (%.1fs of audio)",
            feats.shape[0],
            feats.shape[0] * 0.02,
        )

        beats, downbeats = await asyncio.to_thread(self._run_inference_sync, feats)
        duration = data.total_pcm_samples / ANALYSIS_SAMPLE_RATE

        if len(beats) < 2:
            self.logger.debug("Not enough beats detected, skipping storage")
            return

        bpm = _calculate_overall_bpm(beats)

        # Build extended analysis fields
        energy_curve = None
        if data.energy_chunks:
            energy_curve = np.concatenate(data.energy_chunks)
            peak = energy_curve.max()
            if peak > 0:
                energy_curve = energy_curve / peak  # Normalize to [0, 1]

        spectral_centroid_curve = None
        if data.centroid_chunks:
            spectral_centroid_curve = np.concatenate(data.centroid_chunks)

        musical_key = None
        if data.chroma_chunks:
            chroma_all = np.concatenate(data.chroma_chunks, axis=0)
            musical_key = detect_key(chroma_all, duration)

        phrase_boundaries = []
        if energy_curve is not None and spectral_centroid_curve is not None and len(downbeats) >= 4:
            phrase_boundaries = detect_phrase_boundaries(
                downbeats, energy_curve, spectral_centroid_curve, bpm,
            )

        analysis = AudioAnalysisData(
            bpm=bpm,
            beats=beats,
            downbeats=downbeats,
            duration=duration,
            energy_curve=energy_curve,
            spectral_centroid_curve=spectral_centroid_curve,
            musical_key=musical_key,
            phrase_boundaries=phrase_boundaries if phrase_boundaries else None,
        )

        await self.mass.music.set_audio_analysis(
            data.item_id,
            data.provider,
            self.domain,
            analysis,
            analysis_version=self.analysis_version,
        )

        self.logger.info(
            "Stored beat analysis for %s: BPM=%.1f, %d beats, %d downbeats, "
            "key=%s, %d phrase boundaries",
            data.item_id,
            bpm,
            len(beats),
            len(downbeats),
            musical_key["root"] + " " + musical_key["mode"] if musical_key else "unknown",
            len(phrase_boundaries),
        )
```

- [ ] **Step 5: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 6: Run existing tests to verify no regression**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_provider.py -v`
Expected: `test_beat_detection` PASSES (extended fields are populated but existing assertions still hold)

- [ ] **Step 7: Commit**

```bash
git add music_assistant/providers/smart_fades/provider.py
git commit -m "feat: integrate librosa analysis features into SmartFadesProvider"
```

---

### Task 8: Add integration test for extended analysis

**Files:**
- Modify: `tests/providers/smart_fades/test_provider.py`

- [ ] **Step 1: Add integration test for extended fields**

Add to `tests/providers/smart_fades/test_provider.py`:

```python
async def test_extended_analysis_fields(provider: SmartFadesProvider) -> None:
    """Test that extended analysis fields (energy, centroid, key, phrases) are populated."""
    audio_format = AudioFormat(
        content_type=ContentType.PCM_F32LE,
        bit_depth=32,
        sample_rate=44100,
        channels=2,
    )

    stream_details = Mock()
    stream_details.item_id = "test_120bpm"
    stream_details.provider = "test"
    stream_details.queue_id = "test"
    stream_details.uri = "test://120bpm"

    session_id = "test:test:test_120bpm_extended"
    await provider.start_analysis(session_id, stream_details, audio_format)

    pcm_data = FIXTURE_PCM.read_bytes()
    chunk_size = 44100 * 2 * 4  # 1 second at 44100 Hz, stereo, float32
    offset = 0
    while offset < len(pcm_data):
        chunk = pcm_data[offset : offset + chunk_size]
        await provider.process_pcm_chunk(session_id, chunk)
        offset += chunk_size

    await provider.finalize(session_id)

    call_args = provider.mass.music.set_audio_analysis.call_args
    analysis = call_args[0][3]

    # Energy curve should be populated and normalized to [0, 1]
    assert analysis.energy_curve is not None
    assert len(analysis.energy_curve) > 0
    assert analysis.energy_curve.max() <= 1.0
    assert analysis.energy_curve.min() >= 0.0

    # Spectral centroid should be populated with positive Hz values
    assert analysis.spectral_centroid_curve is not None
    assert len(analysis.spectral_centroid_curve) > 0
    assert all(v >= 0 for v in analysis.spectral_centroid_curve)

    # Musical key should be detected
    assert analysis.musical_key is not None
    assert analysis.musical_key["root"] in [
        "C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B",
    ]
    assert analysis.musical_key["mode"] in ["major", "minor"]
    assert 0.0 <= analysis.musical_key["confidence"] <= 1.0

    # BPM and beats should still be correct
    assert analysis.bpm is not None
    assert 115 < analysis.bpm < 125
```

- [ ] **Step 2: Run the test**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/test_provider.py::test_extended_analysis_fields -v`
Expected: PASS

- [ ] **Step 3: Run all tests**

Run: `cd /Users/marvin/git/music-assistant/server && pytest tests/providers/smart_fades/ -v`
Expected: All tests PASS

- [ ] **Step 4: Run pre-commit**

Run: `cd /Users/marvin/git/music-assistant/server && pre-commit run --all-files`
Expected: All checks pass

- [ ] **Step 5: Commit**

```bash
git add tests/providers/smart_fades/test_provider.py
git commit -m "test: add integration test for extended analysis fields"
```
