"""Test the Matter integration utilities."""

import pytest

from homeassistant.components.matter.util import truncate_fabric_label


@pytest.mark.parametrize(
    ("label", "expected"),
    [
        ("Home", "Home"),
        ("", ""),
        # Exactly at the limit, in bytes.
        ("a" * 32, "a" * 32),
        ("a" * 33, "a" * 32),
        # A multi-byte code point must not be cut in half: "ä" is two bytes, so the
        # 32-byte window ends mid-character and the whole character is dropped.
        (f"{'a' * 31}ä", "a" * 31),
        # 16 two-byte characters are already 32 bytes, well under 32 characters.
        ("ä" * 16, "ä" * 16),
        ("ä" * 17, "ä" * 16),
        # A four-byte code point is kept whole or dropped whole.
        (f"{'a' * 28}🏠", f"{'a' * 28}🏠"),
        (f"{'a' * 29}🏠", "a" * 29),
        # The reported real-world case: 37 bytes, cut to 31 characters / 32 bytes.
        ("Bauer Straße 111, 12345 Oberbaumhaus", "Bauer Straße 111, 12345 Oberbau"),
        # A str can hold an unpaired surrogate, which UTF-8 cannot encode. Dropping it
        # beats raising, including when the label is short enough to need no truncation.
        ("\ud800", ""),
        ("Hom\ud800e", "Home"),
        (f"{'a' * 32}\ud800", "a" * 32),
    ],
)
def test_truncate_fabric_label(label: str, expected: str) -> None:
    """Test that a fabric label is truncated to 32 UTF-8 bytes on a code point boundary."""
    result = truncate_fabric_label(label)

    assert result == expected
    assert len(result.encode("utf-8")) <= 32
