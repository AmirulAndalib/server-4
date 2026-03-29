"""Tests for smart fades models — MusicalKey and Camelot wheel."""

from music_assistant.controllers.streams.smart_fades.models import MusicalKey


def test_camelot_code_c_major_is_8b() -> None:
    """C major maps to Camelot 8B."""
    key = MusicalKey(root="C", mode="major", confidence=0.9)
    assert key.camelot_code == "8B"


def test_camelot_code_a_minor_is_8a() -> None:
    """A minor maps to Camelot 8A."""
    key = MusicalKey(root="A", mode="minor", confidence=0.9)
    assert key.camelot_code == "8A"


def test_camelot_code_d_major_is_10b() -> None:
    """D major maps to Camelot 10B."""
    key = MusicalKey(root="D", mode="major", confidence=0.9)
    assert key.camelot_code == "10B"


def test_camelot_code_f_sharp_minor_is_11a() -> None:
    """F# minor maps to Camelot 11A."""
    key = MusicalKey(root="F#", mode="minor", confidence=0.9)
    assert key.camelot_code == "11A"


def test_camelot_code_b_flat_major_is_6b() -> None:
    """Bb major maps to Camelot 6B."""
    key = MusicalKey(root="Bb", mode="major", confidence=0.9)
    assert key.camelot_code == "6B"


def test_camelot_code_e_flat_minor_is_2a() -> None:
    """Eb minor maps to Camelot 2A."""
    key = MusicalKey(root="Eb", mode="minor", confidence=0.9)
    assert key.camelot_code == "2A"


def test_camelot_code_unknown_root_returns_none() -> None:
    """Unknown root note returns None for camelot_code."""
    key = MusicalKey(root="X", mode="major", confidence=0.9)
    assert key.camelot_code is None


def test_compat_same_key_returns_1() -> None:
    """Same key should return compatibility 1.0."""
    a = MusicalKey(root="C", mode="major", confidence=0.9)
    b = MusicalKey(root="C", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == 1.0


def test_compat_adjacent_position_returns_0_9() -> None:
    """Adjacent Camelot position (8B->9B) returns 0.9."""
    a = MusicalKey(root="C", mode="major", confidence=0.9)
    b = MusicalKey(root="G", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == 0.9


def test_compat_adjacent_wraps_around() -> None:
    """Camelot distance wraps: 1B (B major) to 12B (E major) = distance 1."""
    a = MusicalKey(root="B", mode="major", confidence=0.9)
    b = MusicalKey(root="E", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == 0.9


def test_compat_relative_major_minor_returns_0_85() -> None:
    """Relative major/minor (8A->8B) returns 0.85."""
    a = MusicalKey(root="A", mode="minor", confidence=0.9)
    b = MusicalKey(root="C", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == 0.85


def test_compat_adjacent_plus_relative_returns_0_8() -> None:
    """Adjacent + relative (8A->7B) returns 0.8."""
    a = MusicalKey(root="A", mode="minor", confidence=0.9)
    b = MusicalKey(root="F", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == 0.8


def test_compat_two_positions_returns_0_5() -> None:
    """Two Camelot positions apart (8B->10B) returns 0.5."""
    a = MusicalKey(root="C", mode="major", confidence=0.9)
    b = MusicalKey(root="D", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == 0.5


def test_compat_three_positions_returns_0_2() -> None:
    """Three Camelot positions apart (8B->11B) returns 0.2."""
    a = MusicalKey(root="C", mode="major", confidence=0.9)
    b = MusicalKey(root="A", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == 0.2


def test_compat_distant_key_returns_0_1() -> None:
    """Distant key (8B->2B, distance 6) returns 0.1."""
    a = MusicalKey(root="C", mode="major", confidence=0.9)
    b = MusicalKey(root="F#", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == 0.1


def test_compat_unknown_key_returns_0_1() -> None:
    """Unknown key returns 0.1."""
    a = MusicalKey(root="X", mode="major", confidence=0.9)
    b = MusicalKey(root="C", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == 0.1


def test_compat_symmetry() -> None:
    """Compatibility score is symmetric."""
    a = MusicalKey(root="C", mode="major", confidence=0.9)
    b = MusicalKey(root="G", mode="major", confidence=0.9)
    assert a.compatibility_score(b) == b.compatibility_score(a)
