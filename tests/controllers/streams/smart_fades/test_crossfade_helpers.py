"""Tests for energy-contour crossfade alignment helpers."""

import numpy as np
import pytest

from music_assistant.controllers.streams.smart_fades.crossfade_helpers import (
    find_fadeout_start,
    find_fadein_entry,
    calculate_energy_crossfade_duration,
)


def test_find_fadeout_start_clear_decline() -> None:
    """Should find the knee where energy drops below 85% of peak."""
    # 45 seconds: high energy for 20s, then decline
    energy = np.ones(45, dtype=np.float32) * 0.9
    energy[20:] = np.linspace(0.9, 0.1, 25).astype(np.float32)
    # Downbeats every 2s (120 BPM, 4/4)
    downbeats = np.arange(0, 45, 2.0)

    result = find_fadeout_start(energy, downbeats)

    # Peak at ~sec 19, 85% threshold = 0.765. Decline crosses this around sec 22-24.
    assert result is not None
    assert 20 <= result <= 28, f"Expected fade start around 20-28s, got {result}"


def test_find_fadeout_start_flat_energy() -> None:
    """Flat energy should fall back to default (None)."""
    energy = np.ones(45, dtype=np.float32) * 0.8
    downbeats = np.arange(0, 45, 2.0)

    result = find_fadeout_start(energy, downbeats)

    # No clear decline — should return None (fallback to current behavior)
    assert result is None


def test_find_fadein_entry_clear_build() -> None:
    """Should find the entry point where energy is low and about to rise."""
    # 45 seconds: quiet for 10s, then build to full energy
    energy = np.zeros(45, dtype=np.float32)
    energy[:10] = 0.1
    energy[10:25] = np.linspace(0.1, 0.9, 15).astype(np.float32)
    energy[25:] = 0.9
    downbeats = np.arange(0, 45, 2.0)

    result = find_fadein_entry(energy, downbeats)

    # Should enter before the build starts (~sec 8-12)
    assert result is not None
    assert 4 <= result <= 14, f"Expected entry around 4-14s, got {result}"


def test_find_fadein_entry_already_loud() -> None:
    """Track that starts loud should return None (fallback)."""
    energy = np.ones(45, dtype=np.float32) * 0.9
    downbeats = np.arange(0, 45, 2.0)

    result = find_fadein_entry(energy, downbeats)

    assert result is None


def test_calculate_energy_crossfade_duration() -> None:
    """Duration should cover the energy handoff period."""
    # Song A declining from 0.8
    energy_out = np.linspace(0.8, 0.1, 30).astype(np.float32)
    # Song B rising from 0.1 to 0.9
    energy_in = np.linspace(0.1, 0.9, 30).astype(np.float32)

    duration = calculate_energy_crossfade_duration(
        energy_out=energy_out,
        fadeout_start=0,
        energy_in=energy_in,
        fadein_entry=0,
        bpm=120.0,
    )

    # Song A starts at 0.8. Song B reaches 0.8 at ~index 26.
    # Plus 1 bar blend buffer (2s at 120 BPM).
    assert 4 <= duration <= 40, f"Expected duration 4-40s, got {duration}"
