"""Burning Sky shared forecasting package."""
__version__ = "1.0.0"
ALGORITHM_VERSION = "1.4.67"

from .services.forecast_service import ForecastService

__all__ = ["ForecastService", "ALGORITHM_VERSION", "__version__"]
