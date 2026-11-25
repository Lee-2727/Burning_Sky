"""Compatibility adapter for the current Open-Meteo fetching implementation.

The existing v1.4.67 request and parsing code remains in the compatibility runtime
for this migration release. Inject it here as a callback when moving provider I/O
out of the legacy UI/runtime in the next migration step.
"""
from __future__ import annotations
from typing import Callable, Any
from burning_sky.models import ForecastDataset, ForecastRequest

class CallbackOpenMeteoProvider:
    provider_id = "open-meteo"
    def __init__(self, fetch_callback: Callable[[ForecastRequest], ForecastDataset] | None = None):
        self._fetch_callback = fetch_callback
    def fetch_forecast(self, request: ForecastRequest) -> ForecastDataset:
        if self._fetch_callback is None:
            raise NotImplementedError(
                "Open-Meteo I/O is still hosted by the v1.4.67 compatibility runtime; "
                "the callback adapter is the migration boundary."
            )
        result = self._fetch_callback(request)
        if not isinstance(result, ForecastDataset):
            raise TypeError("provider callback must return ForecastDataset")
        result.validate()
        return result
