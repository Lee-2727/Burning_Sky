"""Application service that invokes the independent, deterministic algorithm core."""
from __future__ import annotations
from typing import Any, Mapping, Sequence
from burning_sky import algorithm

class ForecastService:
    """Shared service used by future desktop/web/API clients.

    It accepts already-normalized data. It deliberately does not fetch weather,
    create widgets, draw charts, or write files.
    """
    algorithm_version = "1.4.67"

    @staticmethod
    def analyze_manual_case(data: Mapping[str, Any]) -> dict[str, Any]:
        return algorithm.analyze(dict(data))

    @staticmethod
    def analyze_cloud_path(samples: Sequence[Mapping[str, Any]], aod: float | None = None,
                           local_low_cloud_above_ground: bool | None = None) -> dict[str, Any]:
        return algorithm.infer_from_cloud_path(
            list(samples), aod=aod,
            local_low_cloud_above_ground=local_low_cloud_above_ground,
        )

    @staticmethod
    def score_two_day_event(hourly: Mapping[str, Sequence[Any]], index: int, model_id: str,
                            ground_elevation_m: float = 0.0, aod: float | None = None):
        return algorithm.score_two_day_event_shared_core(
            hourly, index, model_id, ground_elevation_m=ground_elevation_m, aod=aod
        )

    @staticmethod
    def apply_path_scoring(inferred: Mapping[str, Any], geometry: Mapping[str, Any],
                           boundaries: Mapping[str, Any]) -> dict[str, Any]:
        return algorithm.apply_path_score_pair(dict(inferred), dict(geometry), dict(boundaries))
