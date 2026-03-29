"""Unified crossfade parameter resolution.

Combines key compatibility, spectral centroid, and energy contours
into crossover frequency, fade length, and curve type decisions.
"""

from __future__ import annotations

import logging
import math
from typing import Any

import numpy as np
import numpy.typing as npt

from music_assistant.controllers.streams.smart_fades.helpers import (
    SMART_CROSSFADE_DURATION,
)
from music_assistant.controllers.streams.smart_fades.models import (
    CrossfadeConfig,
    CrossfadeParams,
    MusicalKey,
    TimeStretchDecision,
)
from music_assistant.models.audio_analysis import AudioAnalysisData


def resolve_crossfade_params(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    stretch: TimeStretchDecision,
    config: CrossfadeConfig | None = None,
    logger: logging.Logger | None = None,
) -> CrossfadeParams:
    """Compute unified crossfade parameters from all available signals.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param stretch: Time-stretch decision (tells us if BPM is matched).
    :param config: Tunable parameters. Uses defaults if None.
    :param logger: Optional logger for decision logging.
    """
    if config is None:
        config = CrossfadeConfig()

    key_out = _extract_key(fade_out_analysis.musical_key)
    key_in = _extract_key(fade_in_analysis.musical_key)
    key_compat = _resolve_key_compat(key_out, key_in, config)

    if logger:
        _log_key_debug(logger, fade_out_analysis, fade_in_analysis, key_out, key_in, key_compat)

    if stretch.bpm_diff_percent > config.stretch_threshold_pct:
        return _resolve_path_a(key_compat, config, logger)

    return _resolve_path_b(
        fade_out_analysis=fade_out_analysis,
        fade_in_analysis=fade_in_analysis,
        key_compat=key_compat,
        config=config,
        logger=logger,
    )


def snap_to_musical_bars(bars: float) -> int:
    """Snap fade length to musically coherent bar counts (powers of 2).

    Uses downward bias: 5 bars snaps to 4, not 8. A slightly short
    clean transition beats a slightly long problematic one.

    :param bars: Raw bar count to snap.
    """
    if bars <= 1.5:
        return 1
    if bars <= 3.0:
        return 2
    if bars <= 6.0:
        return 4
    if bars <= 12.0:
        return 8
    return 16


def _extract_key(raw: dict[str, Any] | None) -> MusicalKey | None:
    """Construct a MusicalKey from the raw musical_key dict in AudioAnalysisData.

    :param raw: Raw dict with 'root', 'mode', 'confidence' keys, or None.
    """
    if raw is None:
        return None
    try:
        return MusicalKey(
            root=raw["root"],
            mode=raw["mode"],
            confidence=raw["confidence"],
        )
    except (KeyError, TypeError):
        return None


def _compute_spectral_overlap(centroid_a: float, centroid_b: float) -> float:
    """Compute 0-1 spectral similarity from two centroids.

    Uses log-frequency ratio since pitch perception is logarithmic.
    1.0 = identical, 0.0 = two or more octaves apart.
    Returns 0.0 if either centroid is missing (zero).

    :param centroid_a: Spectral centroid of track A in Hz.
    :param centroid_b: Spectral centroid of track B in Hz.
    """
    if centroid_a <= 0 or centroid_b <= 0:
        return 0.0
    hi = max(centroid_a, centroid_b)
    lo = min(centroid_a, centroid_b)
    return max(0.0, min(1.0, 1.0 - math.log2(hi / lo)))


def _resolve_key_compat(
    key_out: MusicalKey | None,
    key_in: MusicalKey | None,
    config: CrossfadeConfig,
) -> float:
    """Resolve key compatibility, falling back to neutral when keys are absent.

    :param key_out: Outgoing track's key, or None.
    :param key_in: Incoming track's key, or None.
    :param config: Crossfade configuration.
    """
    if key_out is None or key_in is None:
        return config.key_compat_neutral
    return key_out.compatibility_score(key_in)


def _log_key_debug(
    logger: logging.Logger,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    key_out: MusicalKey | None,
    key_in: MusicalKey | None,
    key_compat: float,
) -> None:
    """Log detailed key compatibility debug info."""
    raw_out = fade_out_analysis.musical_key
    raw_in = fade_in_analysis.musical_key

    if key_out is None or key_in is None:
        logger.debug(
            "Key compat: %.2f (neutral fallback) — raw_out=%s, raw_in=%s, "
            "extracted_out=%s, extracted_in=%s",
            key_compat,
            raw_out,
            raw_in,
            key_out,
            key_in,
        )
        return

    logger.debug(
        "Key compat: %.2f — out=%s %s (conf=%.2f, camelot=%s), in=%s %s (conf=%.2f, camelot=%s)",
        key_compat,
        key_out.root,
        key_out.mode,
        key_out.confidence,
        key_out.camelot_code or "?",
        key_in.root,
        key_in.mode,
        key_in.confidence,
        key_in.camelot_code or "?",
    )


def _avg_centroid(
    curve: npt.NDArray[np.float32] | None,
    tail: bool,
    window: int = SMART_CROSSFADE_DURATION,
) -> float:
    """Average spectral centroid over the tail or head of the curve.

    :param curve: Per-second spectral centroid array, or None.
    :param tail: If True, average the last `window` seconds. Otherwise the first.
    :param window: Number of seconds to average.
    """
    if curve is None or len(curve) == 0:
        return 0.0
    segment = curve[-window:] if tail else curve[:window]
    return float(np.mean(segment))


def _compute_energy_slope(
    curve: npt.NDArray[np.float32] | None,
    tail: bool,
    window: int = SMART_CROSSFADE_DURATION,
) -> float:
    """Compute energy gradient in the crossfade region.

    Positive = energy rising, negative = energy falling.

    :param curve: Per-second RMS energy array (normalized 0-1), or None.
    :param tail: If True, use the last `window` seconds. Otherwise the first.
    :param window: Number of seconds to analyze.
    """
    if curve is None or len(curve) < 2:
        return 0.0
    segment = curve[-window:] if tail else curve[:window]
    if len(segment) < 2:
        return 0.0
    x = np.arange(len(segment), dtype=np.float64)
    return float(np.polyfit(x, segment.astype(np.float64), 1)[0])


def _resolve_path_a(
    key_compat: float,
    config: CrossfadeConfig,
    logger: logging.Logger | None,
) -> CrossfadeParams:
    """Resolve Path A: unstretched, quick timed fade.

    :param key_compat: Key compatibility score 0-1.
    :param config: Crossfade configuration.
    :param logger: Optional logger.
    """
    incompat = 1.0 - key_compat
    crossover = int(
        config.path_a_crossover_low
        + incompat * (config.path_a_crossover_high - config.path_a_crossover_low)
    )
    fade_seconds = config.path_a_max_fade_sec + incompat * (
        config.path_a_min_fade_sec - config.path_a_max_fade_sec
    )
    fade_seconds = round(fade_seconds, 2)

    if logger:
        logger.debug(
            "Crossfade params Path A: key_compat=%.2f "
            "-> crossover=%dHz, fade=%.2fs, curve=exponential",
            key_compat,
            crossover,
            fade_seconds,
        )

    return CrossfadeParams(
        crossover_freq=crossover,
        max_fade_bars=0,
        max_fade_seconds=fade_seconds,
        curve_type="exponential",
        use_bar_alignment=False,
    )


def _resolve_path_b(
    *,
    fade_out_analysis: AudioAnalysisData,
    fade_in_analysis: AudioAnalysisData,
    key_compat: float,
    config: CrossfadeConfig,
    logger: logging.Logger | None,
) -> CrossfadeParams:
    """Resolve Path B: stretched, beats aligned.

    Three-stage pipeline: crossover -> fade length -> curve type.

    :param fade_out_analysis: Analysis data for the outgoing track.
    :param fade_in_analysis: Analysis data for the incoming track.
    :param key_compat: Resolved key compatibility 0-1.
    :param config: Crossfade configuration.
    :param logger: Optional logger.
    """
    fade_in_bpm = fade_in_analysis.bpm or 120.0

    centroid_out = _avg_centroid(fade_out_analysis.spectral_centroid_curve, tail=True)
    centroid_in = _avg_centroid(fade_in_analysis.spectral_centroid_curve, tail=False)
    spectral_olap = _compute_spectral_overlap(centroid_out, centroid_in)

    slope_out = _compute_energy_slope(fade_out_analysis.energy_curve, tail=True)
    slope_in = _compute_energy_slope(fade_in_analysis.energy_curve, tail=False)

    crossover_freq = _resolve_crossover_freq(key_compat, centroid_out, centroid_in, config)
    max_fade_bars = _resolve_fade_bars(key_compat, config)
    curve_type = _resolve_curve_type(key_compat, spectral_olap, slope_out, slope_in, config)

    bar_duration = 4.0 * 60.0 / fade_in_bpm
    max_fade_seconds = round(max_fade_bars * bar_duration, 2)

    if logger:
        logger.debug(
            "Crossfade params Path B: key_compat=%.2f, centroids=%.0f/%.0fHz, "
            "slopes=%.2f/%.2f -> crossover=%dHz, fade=%dbars (%.1fs), curve=%s "
            "(spectral_overlap=%.2f)",
            key_compat,
            centroid_out,
            centroid_in,
            slope_out,
            slope_in,
            crossover_freq,
            max_fade_bars,
            max_fade_seconds,
            curve_type,
            spectral_olap,
        )

    return CrossfadeParams(
        crossover_freq=crossover_freq,
        max_fade_bars=max_fade_bars,
        max_fade_seconds=max_fade_seconds,
        curve_type=curve_type,
        use_bar_alignment=True,
    )


def _resolve_crossover_freq(
    key_compat: float,
    centroid_out: float,
    centroid_in: float,
    config: CrossfadeConfig,
) -> int:
    """Blend key-driven and spectral-driven crossover frequencies.

    :param key_compat: Key compatibility 0-1.
    :param centroid_out: Average spectral centroid of outgoing track tail (Hz).
    :param centroid_in: Average spectral centroid of incoming track head (Hz).
    :param config: Crossfade configuration.
    """
    crossover_key = config.crossover_key_base + (1.0 - key_compat) * config.crossover_key_range

    if centroid_out > 0 and centroid_in > 0:
        spectral_mid = math.sqrt(centroid_out * centroid_in)
        crossover_spectral = max(
            config.crossover_min,
            min(config.crossover_max, spectral_mid * config.crossover_spectral_scale),
        )
    else:
        crossover_spectral = crossover_key

    key_urgency = max(0.0, min(1.0, (1.0 - key_compat) * config.key_urgency_steepness))
    crossover = key_urgency * crossover_key + (1.0 - key_urgency) * crossover_spectral
    return int(max(config.crossover_min, min(config.crossover_max, crossover)))


def _resolve_fade_bars(
    key_compat: float,
    config: CrossfadeConfig,
) -> int:
    """Max fade length from key compatibility tiers.

    :param key_compat: Key compatibility 0-1.
    :param config: Crossfade configuration.
    """
    if key_compat >= config.key_threshold_compatible:
        return config.key_tier_compatible[1]
    if key_compat >= config.key_threshold_moderate:
        return config.key_tier_moderate[1]
    if key_compat >= config.key_threshold_clashing:
        return config.key_tier_incompatible[1]
    return config.key_tier_clashing[1]


def _resolve_curve_type(
    key_compat: float,
    spectral_olap: float,
    slope_out: float,
    slope_in: float,
    config: CrossfadeConfig,
) -> str:
    """Select curve type using priority chain.

    :param key_compat: Key compatibility 0-1.
    :param spectral_olap: Spectral overlap 0-1.
    :param slope_out: Outgoing energy slope.
    :param slope_in: Incoming energy slope.
    :param config: Crossfade configuration.
    """
    if key_compat < config.key_compat_exp_threshold:
        return "exponential"
    if (
        spectral_olap > config.spectral_overlap_linear_threshold
        and key_compat > config.key_compat_linear_threshold
    ):
        return "linear"
    if slope_out < config.energy_slope_natural_fade:
        return "logarithmic"
    if slope_in > config.energy_slope_building:
        return "exponential"
    return "qsin"
