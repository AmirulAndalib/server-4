"""Helper functions for extended audio analysis features.

Computes energy curves, spectral centroids, chroma/key detection,
and phrase boundary detection from streaming PCM audio. Uses librosa
for STFT-based features computed directly on pcm_22k blocks.
"""

from __future__ import annotations

import librosa
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


def compute_stft_features(
    pcm: npt.NDArray[np.float32],
    sr: int = 22050,
    n_fft: int = 2048,
    hop_length: int = 512,
) -> tuple[npt.NDArray[np.float32], npt.NDArray[np.float32]]:
    """Compute spectral centroid and chroma from a single librosa STFT.

    Computes one STFT on the pcm block and derives both features from it.
    Per-second averaging reduces per-frame data to one value per second.

    Boundary artifacts at 10s block edges are negligible (~1.2% on per-second
    values) and do not affect phrase detection thresholds.

    :param pcm: Audio samples as float32 array at the given sample rate.
    :param sr: Sample rate in Hz.
    :param n_fft: FFT window size.
    :param hop_length: Hop length between STFT frames.
    :return: Tuple of (centroid_per_second, chroma_per_second).
             centroid_per_second shape: (T_seconds,)
             chroma_per_second shape: (T_seconds, 12)
    """
    if len(pcm) < n_fft:
        empty_centroid = np.array([], dtype=np.float32)
        empty_chroma = np.zeros((0, 12), dtype=np.float32)
        return empty_centroid, empty_chroma

    # Compute STFT once — all features derived from this
    stft_matrix = np.abs(librosa.stft(y=pcm, n_fft=n_fft, hop_length=hop_length, center=True))

    # Spectral centroid per frame
    centroid_per_frame = librosa.feature.spectral_centroid(
        S=stft_matrix,
        sr=sr,
        n_fft=n_fft,
        hop_length=hop_length,
    )[0]

    # Chroma per frame (12 bins)
    chroma_per_frame = librosa.feature.chroma_stft(
        S=stft_matrix,
        sr=sr,
        n_fft=n_fft,
        hop_length=hop_length,
    )  # shape: (12, T_frames)

    # Average to per-second
    frames_per_sec = sr // hop_length
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

    return centroid_per_sec.astype(np.float32), chroma_per_sec.astype(np.float32)


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

    # Map realistic correlation range [0.3, 0.9] to confidence [0, 1]
    # A correlation of 0.3 (ambiguous) = 0.0, 0.9 (very clear) = 1.0
    confidence = max(0.0, min(1.0, (best_corr - 0.3) / 0.6))

    return {
        "root": _NOTE_NAMES[best_root],
        "mode": best_mode,
        "confidence": round(confidence, 3),
    }


def detect_phrase_boundaries(
    downbeats: npt.NDArray[np.float64],
    energy_curve: npt.NDArray[np.float32],
    centroid_curve: npt.NDArray[np.float32],
    bpm: float,
) -> list[dict]:
    """Detect phrase/section boundaries using anchor-based phase alignment.

    Scores ALL downbeats by energy + centroid delta, finds the strongest
    transition as a phase anchor, then uses bar-grid alignment relative
    to the anchor as a multiplicative bonus (not a hard gate).

    This avoids the phase-offset problem where the first detected downbeat
    may not align with a phrase boundary (pickup notes, mid-phrase starts).

    :param downbeats: Array of downbeat timestamps in seconds.
    :param energy_curve: Normalized [0,1] RMS energy per second.
    :param centroid_curve: Spectral centroid (Hz) per second.
    :param bpm: Track BPM.
    :return: List of PhraseBoundary-compatible dicts with 'time', 'confidence', 'boundary_type'.
    """
    if len(downbeats) < 4:
        return []

    # Phase 1: Score every downbeat by energy + centroid delta
    scores: list[tuple[int, float, float]] = []  # (index, time, raw_score)
    for i, db_time in enumerate(downbeats):
        sec_idx = int(db_time)
        if sec_idx < 2 or sec_idx >= len(energy_curve) - 2:
            continue

        e_before = float(np.mean(energy_curve[max(0, sec_idx - 2) : sec_idx]))
        e_after = float(np.mean(energy_curve[sec_idx : min(len(energy_curve), sec_idx + 2)]))
        energy_delta = abs(e_after - e_before) / max(e_before, 1e-10)

        if sec_idx < len(centroid_curve) - 2:
            c_before = float(np.mean(centroid_curve[max(0, sec_idx - 2) : sec_idx]))
            c_after = float(np.mean(centroid_curve[sec_idx : min(len(centroid_curve), sec_idx + 2)]))
            centroid_delta = abs(c_after - c_before) / max(c_before, 1e-10)
        else:
            centroid_delta = 0.0

        raw_score = 0.6 * energy_delta + 0.4 * centroid_delta
        scores.append((i, float(db_time), raw_score))

    if not scores:
        return []

    # Phase 2: Find the anchor — highest-scoring downbeat
    anchor_entry = max(scores, key=lambda s: s[2])
    anchor_idx = anchor_entry[0]

    # Phase 3: Score with bar-grid bonus relative to anchor
    boundaries: list[dict] = []
    for i, db_time, raw_score in scores:
        bars_from_anchor = abs(i - anchor_idx)

        # Bar-grid alignment bonus: reward multiples of 4/8/16 bars from anchor
        if bars_from_anchor == 0:
            grid_bonus = 1.0  # Anchor itself — no bonus needed
        elif bars_from_anchor % 16 == 0:
            grid_bonus = 1.5  # Strong prior for 16-bar alignment
        elif bars_from_anchor % 8 == 0:
            grid_bonus = 1.3
        elif bars_from_anchor % 4 == 0:
            grid_bonus = 1.1
        else:
            grid_bonus = 1.0  # Off-grid — raw score must be strong enough alone

        adjusted_score = raw_score * grid_bonus

        # Single threshold — grid bonus handles the tiered logic
        if adjusted_score > 0.25:
            if adjusted_score > 0.5:
                boundary_type = "section"
            else:
                boundary_type = "phrase"
            boundaries.append({
                "time": db_time,
                "confidence": round(min(1.0, adjusted_score), 3),
                "boundary_type": boundary_type,
            })

    return boundaries
