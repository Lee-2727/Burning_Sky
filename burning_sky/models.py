"""Shared, provider-neutral data contracts. No GUI or network dependencies."""
from __future__ import annotations
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Sequence

@dataclass(frozen=True)
class ModelMetadata:
    provider: str
    model_id: str
    initialization_time: datetime | None = None
    fetched_at: datetime | None = None
    native_time_step_hours: float | None = None
    native_resolution_km: float | None = None
    cloud_source_kind: str = "native"  # native | humidity_derived | satellite_observation
    extra: Mapping[str, Any] = field(default_factory=dict)

@dataclass
class ForecastDataset:
    """Normalized forecast container while retaining source-specific raw data."""
    metadata: ModelMetadata
    valid_times: Sequence[datetime]
    hourly: Mapping[str, Sequence[Any]]
    raw: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        n = len(self.valid_times)
        mismatches = {key: len(values) for key, values in self.hourly.items() if len(values) != n}
        if mismatches:
            raise ValueError(f"Hourly arrays must match valid_times length {n}: {mismatches}")

@dataclass(frozen=True)
class ForecastRequest:
    latitude: float
    longitude: float
    start: datetime
    end: datetime
    timezone_name: str = "Asia/Shanghai"
    elevation_m: float = 0.0
    options: Mapping[str, Any] = field(default_factory=dict)

    def validate(self) -> None:
        if not -90 <= self.latitude <= 90:
            raise ValueError("latitude must be between -90 and 90")
        if not -180 <= self.longitude <= 180:
            raise ValueError("longitude must be between -180 and 180")
        if self.end < self.start:
            raise ValueError("end must not be earlier than start")
