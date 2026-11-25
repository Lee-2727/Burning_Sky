"""Locate the user-maintained place workbook for both desktop and web."""
from pathlib import Path

def find_place_workbook():
    burning_root = Path(__file__).resolve().parent.parent
    package_root = burning_root.parent
    candidates = (
        burning_root / "china_city_coordinates.xlsx",
        burning_root / "shared_locations" / "china_city_coordinates.xlsx",
        package_root / "shared_locations" / "china_city_coordinates.xlsx",
        package_root / "china_city_coordinates.xlsx",
    )
    return next((str(p) for p in candidates if p.is_file()), str(candidates[0]))
