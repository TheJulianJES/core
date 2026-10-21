"""Provide integration utilities."""

XY_COLOR_FACTOR = 65536

# The Label of OperationalCredentials' UpdateFabricLabel command is a Matter `string`
# constrained to `max 32`; a character string constraint is "in bytes", and a `string`
# "SHALL NOT contain truncated code points" (Matter 1.6 core specification, 11.18.6.11.1,
# 7.18.3.3 and 7.19.2.40).
FABRIC_LABEL_MAX_BYTES = 32


def renormalize(
    number: float, from_range: tuple[float, float], to_range: tuple[float, float]
) -> float:
    """Change value from from_range to to_range."""
    delta1 = from_range[1] - from_range[0]
    delta2 = to_range[1] - to_range[0]
    return (delta2 * (number - from_range[0]) / delta1) + to_range[0]


def convert_to_matter_hs(hass_hs: tuple[float, float]) -> tuple[float, float]:
    """Convert Home Assistant HS to Matter HS."""

    return (
        hass_hs[0] / 360 * 254,
        renormalize(hass_hs[1], (0, 100), (0, 254)),
    )


def convert_to_hass_hs(matter_hs: tuple[float, float]) -> tuple[float, float]:
    """Convert Matter HS to Home Assistant HS."""

    return (
        matter_hs[0] * 360 / 254,
        renormalize(matter_hs[1], (0, 254), (0, 100)),
    )


def convert_to_matter_xy(hass_xy: tuple[float, float]) -> tuple[float, float]:
    """Convert Home Assistant XY to Matter XY."""

    return (hass_xy[0] * XY_COLOR_FACTOR, hass_xy[1] * XY_COLOR_FACTOR)


def convert_to_hass_xy(matter_xy: tuple[float, float]) -> tuple[float, float]:
    """Convert Matter XY to Home Assistant XY."""

    return (matter_xy[0] / XY_COLOR_FACTOR, matter_xy[1] / XY_COLOR_FACTOR)


def truncate_fabric_label(label: str) -> str:
    """Truncate a fabric label to the length Matter allows, keeping code points whole.

    Encoding drops anything UTF-8 cannot represent, such as an unpaired surrogate;
    decoding drops the code point the byte limit would otherwise cut in half.
    """
    encoded = label.encode("utf-8", errors="ignore")
    return encoded[:FABRIC_LABEL_MAX_BYTES].decode("utf-8", errors="ignore")
