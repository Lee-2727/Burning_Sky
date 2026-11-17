"""Shared illustrative twilight cloud hue palette, v2.

Colour is not a measured spectral radiance or guarantee of purple skies.
Sun elevation is the dominant cue; haze/saturation and validated illumination
are applied by callers. This module performs no I/O.
"""
from __future__ import annotations

KEYS = (
    (1.0, (255, 208, 131)),
    (0.0, (255, 166, 91)),
    (-1.3, (251, 104, 69)),
    (-2.4, (235, 70, 91)),
    (-3.6, (218, 83, 139)),
    (-4.8, (180, 85, 160)),
    (-6.0, (119, 72, 135)),
    (-8.0, (48, 38, 69)),
)

def cloud_rgb(solar_altitude_deg):
    alt = float(solar_altitude_deg)
    if alt >= KEYS[0][0]:
        return KEYS[0][1]
    if alt <= KEYS[-1][0]:
        return KEYS[-1][1]
    for (high_alt, high_rgb), (low_alt, low_rgb) in zip(KEYS, KEYS[1:]):
        if low_alt <= alt <= high_alt:
            u = (high_alt - alt) / (high_alt - low_alt)
            # Smoothstep prevents abrupt band changes at colour boundaries.
            u = u*u*(3.0 - 2.0*u)
            return tuple(int(round(a*(1-u)+b*u)) for a,b in zip(high_rgb,low_rgb))
    return KEYS[-1][1]


def cloud_hex(solar_altitude_deg):
    return '#%02x%02x%02x' % cloud_rgb(solar_altitude_deg)
