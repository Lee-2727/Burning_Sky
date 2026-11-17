"""Provider interfaces. Provider code must not contain forecast scoring logic."""
from __future__ import annotations
from typing import Protocol
from burning_sky.models import ForecastDataset, ForecastRequest

class WeatherProvider(Protocol):
    provider_id: str
    def fetch_forecast(self, request: ForecastRequest) -> ForecastDataset:
        """Fetch and normalize source data; do not calculate fire-cloud scores."""
        ...
