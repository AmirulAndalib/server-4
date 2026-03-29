# Simplified Alignment Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Simplify crossfade alignment from 3 cascaded strategies (~1150 lines) to a single energy-context-based strategy (~400 lines), with clean separation between temporal decisions (alignment) and tonal decisions (resolver).

**Architecture:** Key+BPM set the max fade duration (resolver). Energy knees constrain it shorter (alignment). Incoming track characterized as quiet-intro or loud-start, modulating aggressiveness. Spectral knee folded into energy knee as fallback.

**Tech Stack:** Python 3.12+, numpy, existing smart_fades infrastructure.

**Spec:** `docs/superpowers/specs/2026-03-29-simplified-alignment-design.md`

---

### Task 1: Simplify `_resolve_fade_bars` to key-only tiers

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/crossfade_params.py`
- Modify: `tests/controllers/streams/smart_fades/test_crossfade_params.py`

- [ ] **Step 1: Update `_resolve_fade_bars` to remove energy slope and spectral overlap**

In `crossfade_params.py`, replace `_resolve_fade_bars` (currently lines 342-375) with:

```python
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
```

Update the call site in `_resolve_path_b` to pass only `key_compat` and `config` (remove `slope_out`, `slope_in`, `spectral_olap` args).

- [ ] **Step 2: Rename `fade_bars`/`fade_seconds` to `max_fade_bars`/`max_fade_seconds` on `CrossfadeParams`**

In `models.py`, rename the fields:

```python
@dataclass
class CrossfadeParams:
    """Resolved crossfade parameters for filter construction."""

    crossover_freq: int
    max_fade_bars: int
    max_fade_seconds: float
    curve_type: str
    use_bar_alignment: bool
```

Update all references in `crossfade_params.py` and `fades.py` (the `min(alignment.crossfade_duration, params.fade_seconds)` line becomes `min(alignment.crossfade_duration, params.max_fade_seconds)`).

- [ ] **Step 3: Update tests**

In `test_crossfade_params.py`, update field references from `result.fade_bars` to `result.max_fade_bars` and `result.fade_seconds` to `result.max_fade_seconds`. The test assertions stay the same — the values haven't changed, only the field names.

- [ ] **Step 4: Run tests and commit**

Run: `source .venv/bin/activate && pytest tests/controllers/streams/smart_fades/ -v`
Expected: All pass.

Run: `pre-commit run --all-files` (fix any ruff issues)

```bash
git add -u && SKIP=mypy git commit -m "refactor: simplify _resolve_fade_bars to key-only tiers, rename to max_fade"
```

---

### Task 2: Create `_find_knee` unified knee finder

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/alignment.py`
- Modify: `tests/controllers/streams/smart_fades/test_alignment.py`

- [ ] **Step 1: Write tests for `_find_knee`**

Add to `test_alignment.py`:

```python
from music_assistant.controllers.streams.smart_fades.alignment import _find_knee


def test_find_knee_clear_decline() -> None:
    """Clear energy decline should return knee position."""
    energy = np.ones(45, dtype=np.float32) * 0.9
    energy[35:] = np.linspace(0.9, 0.1, 10).astype(np.float32)
    downbeats = np.arange(0, 45, 2.0)

    result = _find_knee(energy, downbeats, bpm=120.0)

    assert result is not None
    start_pos, knee_idx = result
    assert 30 <= knee_idx <= 40
    assert start_pos <= knee_idx


def test_find_knee_flat_energy_returns_none() -> None:
    """Flat energy should return None (no knee)."""
    energy = np.ones(45, dtype=np.float32) * 0.5
    downbeats = np.arange(0, 45, 2.0)

    result = _find_knee(energy, downbeats, bpm=120.0)

    assert result is None


def test_find_knee_near_silence_returns_none() -> None:
    """Near-silent track should return None."""
    energy = np.ones(45, dtype=np.float32) * 0.03
    downbeats = np.arange(0, 45, 2.0)

    result = _find_knee(energy, downbeats, bpm=120.0)

    assert result is None
```

- [ ] **Step 2: Implement `_find_knee`**

This is a refactor of the existing `_find_fadeout_start`. The logic stays the same (smooth, find peak, walk to threshold, snap to phrase boundary) but returns `tuple[float, float] | None` — `(start_pos, knee_idx)`.

Rename `_find_fadeout_start` to `_find_knee`. Update the return to include `knee_idx`. Keep the existing smoothing, peak finding, knee detection, and phrase snapping logic.

- [ ] **Step 3: Update call sites**

In `_try_energy_alignment`, update the call from `_find_fadeout_start(...)` to `_find_knee(...)` and unpack the tuple.

- [ ] **Step 4: Run tests and commit**

Run: `source .venv/bin/activate && pytest tests/controllers/streams/smart_fades/ -v`

```bash
git add -u && SKIP=mypy git commit -m "refactor: rename _find_fadeout_start to _find_knee, return knee_idx"
```

---

### Task 3: Create `_characterize_incoming`

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/alignment.py`
- Modify: `tests/controllers/streams/smart_fades/test_alignment.py`

- [ ] **Step 1: Write tests**

```python
from music_assistant.controllers.streams.smart_fades.alignment import _characterize_incoming


def test_characterize_incoming_quiet_intro() -> None:
    """Track with quiet intro should be detected."""
    energy = np.zeros(45, dtype=np.float32)
    energy[:15] = 0.05
    energy[15:] = 0.8

    result = _characterize_incoming(energy)

    assert result["has_quiet_intro"] is True
    assert result["entry_energy"] < 0.15


def test_characterize_incoming_loud_start() -> None:
    """Track starting loud should not be detected as quiet intro."""
    energy = np.zeros(45, dtype=np.float32)
    energy[:5] = np.linspace(0.27, 0.91, 5).astype(np.float32)
    energy[5:] = 0.91

    result = _characterize_incoming(energy)

    assert result["has_quiet_intro"] is False
    assert result["entry_energy"] > 0.15


def test_characterize_incoming_none_curve() -> None:
    """No energy curve returns neutral characterization."""
    result = _characterize_incoming(None)

    assert result["has_quiet_intro"] is False
    assert result["entry_energy"] == 0.5
```

- [ ] **Step 2: Implement `_characterize_incoming`**

```python
_QUIET_INTRO_THRESHOLD = 0.15
_QUIET_INTRO_WINDOW = 10


def _characterize_incoming(
    energy_head: npt.NDArray[np.float32] | None,
) -> dict[str, Any]:
    """Characterize the incoming track's opening for crossfade aggressiveness.

    :param energy_head: Per-second energy for the incoming track buffer, or None.
    :return: Dict with 'has_quiet_intro' (bool) and 'entry_energy' (float).
    """
    if energy_head is None or len(energy_head) == 0:
        return {"has_quiet_intro": False, "entry_energy": 0.5}

    window = min(_QUIET_INTRO_WINDOW, len(energy_head))
    entry_energy = float(np.mean(energy_head[:window]))
    has_quiet_intro = entry_energy < _QUIET_INTRO_THRESHOLD

    return {"has_quiet_intro": has_quiet_intro, "entry_energy": entry_energy}
```

- [ ] **Step 3: Run tests and commit**

```bash
git add -u && SKIP=mypy git commit -m "feat: add _characterize_incoming for lightweight fadein classification"
```

---

### Task 4: Rewrite `resolve_alignment` with three energy contexts

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/alignment.py`
- Modify: `tests/controllers/streams/smart_fades/test_alignment.py`

This is the core task. It replaces `_try_energy_alignment`, `_try_spectral_alignment`, and most of `_bar_count_alignment` with a single function.

- [ ] **Step 1: Write tests for the new contexts**

```python
def test_resolve_alignment_context_a_knee_found() -> None:
    """Outgoing knee + incoming quiet intro = energy strategy, moderate duration."""
    out_energy = np.ones(180, dtype=np.float32) * 0.9
    out_energy[155:] = np.linspace(0.9, 0.1, 25).astype(np.float32)
    in_energy = np.zeros(180, dtype=np.float32)
    in_energy[:15] = 0.05
    in_energy[15:40] = np.linspace(0.05, 0.8, 25).astype(np.float32)
    in_energy[40:] = 0.8

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "energy"
    assert result.fadeout_start_pos is not None
    assert result.crossfade_duration > 0


def test_resolve_alignment_context_a_knee_loud_incoming() -> None:
    """Outgoing knee + loud incoming = energy strategy, short duration."""
    out_energy = np.ones(180, dtype=np.float32) * 0.9
    out_energy[155:] = np.linspace(0.9, 0.1, 25).astype(np.float32)
    in_energy = np.ones(180, dtype=np.float32) * 0.8

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "energy"
    assert result.crossfade_duration <= 15  # short, not 8-bar minimum


def test_resolve_alignment_context_b_both_quiet() -> None:
    """Both quiet tracks = long key-driven duration."""
    out_energy = np.ones(180, dtype=np.float32) * 0.10
    in_energy = np.ones(180, dtype=np.float32) * 0.08

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "quiet"
    assert result.crossfade_duration >= 20  # will be capped by resolver's max


def test_resolve_alignment_context_c_no_knee_loud() -> None:
    """No knee, both loud = energy_ratio strategy."""
    out_energy = np.ones(180, dtype=np.float32) * 0.7
    in_energy = np.ones(180, dtype=np.float32) * 0.9

    fade_out = _make_analysis(energy_curve=out_energy)
    fade_in = _make_analysis(energy_curve=in_energy)

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "energy_ratio"


def test_resolve_alignment_no_data_fallback() -> None:
    """No energy or spectral data = bar_count fallback."""
    fade_out = _make_analysis()
    fade_in = _make_analysis()

    result = resolve_alignment(fade_out_analysis=fade_out, fade_in_analysis=fade_in)

    assert result.strategy == "bar_count"
```

- [ ] **Step 2: Rewrite `resolve_alignment`**

Replace the current cascade with the three-context approach from the spec. This is the largest code change:

1. Extract curves and downbeats (keep existing `_extract_buffer_and_downbeats`)
2. Try energy knee, fall back to spectral knee
3. Characterize incoming
4. Context A/B/C routing
5. Bar-count fallback if no energy data

- [ ] **Step 3: Remove old functions**

Delete: `_try_energy_alignment`, `_try_spectral_alignment`, `_find_fadein_entry`, `_find_quiet_region_entry`, `_find_spectral_fadein_entry`, `_find_spectral_fadeout_start`, `_calculate_energy_crossfade_duration`, `_calculate_optimal_crossfade_bars`, `_calculate_optimal_fade_timing`, `_calculate_crossfade_duration`, `_adjust_crossfade_to_downbeats`.

- [ ] **Step 4: Remove old constants**

Delete: `_RISE_GRADIENT`, `_RISE_SUSTAINED`, `_LOW_ENERGY_GUARD`, `_LOW_ENERGY_ABSOLUTE`, `_MIN_QUIET_SUSTAIN`, `_POST_QUIET_WINDOW`, `_POST_QUIET_RISE_THRESHOLD`, `_DROP_CHECK_WINDOW`, `_MAX_RISE_GRADIENT`, `_SPECTRAL_REMAINING_AVG_GUARD`.

Add: `_QUIET_THRESHOLD = 0.20`, `_ENERGY_RATIO_SHORT_FADE = 3.0`.

- [ ] **Step 5: Simplify `_bar_count_alignment`**

Reduce to ~20 lines: pick bars from BPM diff, compute duration, set fadein to first downbeat.

- [ ] **Step 6: Remove old tests, update remaining**

Remove tests for deleted functions. Update `test_resolve_alignment_*` tests. Keep fadeout/knee tests, spectral fadeout tests, phrase snapping tests, helper tests.

- [ ] **Step 7: Run full test suite and commit**

Run: `source .venv/bin/activate && pytest tests/controllers/streams/smart_fades/ -v`

```bash
git add -u && SKIP=mypy git commit -m "refactor: simplify alignment to single strategy with three energy contexts"
```

---

### Task 5: Update `fades.py` for renamed fields

**Files:**
- Modify: `music_assistant/controllers/streams/smart_fades/fades.py`

- [ ] **Step 1: Update field references**

Change `params.fade_seconds` to `params.max_fade_seconds` in `_build_filters`.

- [ ] **Step 2: Verify strategy checks still work**

The `energy_aligned` check should include all energy-based strategies:
```python
energy_aligned = alignment.strategy in ("energy", "quiet", "energy_ratio")
```

- [ ] **Step 3: Run tests and commit**

```bash
git add -u && SKIP=mypy git commit -m "refactor: update fades.py for renamed max_fade fields and new strategy names"
```

---

### Task 6: Final verification

- [ ] **Step 1: Run full test suite**

```bash
source .venv/bin/activate && pytest tests/controllers/streams/smart_fades/ -v
```

- [ ] **Step 2: Verify module structure**

```bash
wc -l music_assistant/controllers/streams/smart_fades/alignment.py
```

Expected: ~400-500 lines (down from ~1150).

- [ ] **Step 3: Verify imports are clean**

```bash
source .venv/bin/activate && python -c "
from music_assistant.controllers.streams.smart_fades.alignment import resolve_alignment, _find_knee, _characterize_incoming
from music_assistant.controllers.streams.smart_fades.crossfade_params import resolve_crossfade_params
from music_assistant.controllers.streams.smart_fades.models import CrossfadeParams
print('All imports OK')
print('CrossfadeParams fields:', [f.name for f in CrossfadeParams.__dataclass_fields__.values()])
"
```

- [ ] **Step 4: Final commit if cleanup needed**

```bash
git add -u && SKIP=mypy git commit -m "chore: final cleanup for simplified alignment"
```
