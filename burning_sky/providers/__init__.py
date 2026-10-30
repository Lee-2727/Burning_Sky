"""Weather and satellite data provider interfaces and adapters."""
from .base import WeatherProvider
from .open_meteo import CallbackOpenMeteoProvider

__all__ = ["WeatherProvider", "CallbackOpenMeteoProvider"]
