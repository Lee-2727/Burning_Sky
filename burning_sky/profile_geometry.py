"""Spherical cross-section curve sampling for GUI diagnostics.

Surface arc distance is measured from the target toward the sun. The exact
line-of-sight relation is not a polynomial; the familiar parabolic curve is
its short-distance approximation. Earth intersections never become valid again.
"""
import math
from .spherical_raytrace import EARTH_RADIUS_KM, _ray_height_km, _earth_occluded


def curve_minimum(target_base_km, solar_alt_deg, max_distance_km):
    d = max(0.0, min(float(max_distance_km),
                     -math.radians(float(solar_alt_deg))*EARTH_RADIUS_KM))
    return d, _ray_height_km(target_base_km, solar_alt_deg, d)


def sample_ray_curve(target_base_km, solar_alt_deg, max_distance_km, count=1201):
    if count < 2 or max_distance_km <= 0:
        raise ValueError("count must be >=2 and max_distance_km must be positive")
    output=[]
    for i in range(count):
        d=float(max_distance_km)*i/(count-1)
        h=_ray_height_km(target_base_km,solar_alt_deg,d)
        earth_blocked=_earth_occluded(target_base_km,solar_alt_deg,d)
        output.append((d,h,earth_blocked))
    return output
