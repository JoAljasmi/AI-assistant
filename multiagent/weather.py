"""Weather skill — current conditions and today's range for a place, via the
free, keyless Open-Meteo API (no signup, no key). It geocodes the place name to
coordinates, then fetches the forecast.
"""
import requests

_GEO = "https://geocoding-api.open-meteo.com/v1/search"
_FORECAST = "https://api.open-meteo.com/v1/forecast"

# A subset of WMO weather codes -> human-readable conditions.
_CODES = {
    0: "clear sky", 1: "mainly clear", 2: "partly cloudy", 3: "overcast",
    45: "fog", 48: "rime fog",
    51: "light drizzle", 53: "drizzle", 55: "dense drizzle",
    61: "light rain", 63: "rain", 65: "heavy rain",
    71: "light snow", 73: "snow", 75: "heavy snow",
    80: "rain showers", 81: "rain showers", 82: "violent rain showers",
    95: "thunderstorm", 96: "thunderstorm with hail", 99: "thunderstorm with hail",
}


def _describe(code):
    return _CODES.get(code, f"weather code {code}")


def _format(place_name, country, current, daily):
    """Build the reply string from the API pieces. Split out so it's testable
    without hitting the network."""
    temp = current.get("temperature_2m")
    cond = _describe(current.get("weather_code"))
    wind = current.get("wind_speed_10m")
    hi = (daily.get("temperature_2m_max") or [None])[0]
    lo = (daily.get("temperature_2m_min") or [None])[0]
    where = ", ".join(p for p in (place_name, country) if p)
    return (f"Weather in {where}: {cond}, {temp}°C now "
            f"(today {lo}–{hi}°C), wind {wind} km/h.")


def weather(location):
    """Return a short weather summary for a place name."""
    if not location or not location.strip():
        return "[error: weather needs a place name]"

    try:
        geo = requests.get(
            _GEO, params={"name": location.strip(), "count": 1}, timeout=15
        ).json()
    except requests.RequestException as e:
        return f"[error: geocoding failed: {e}]"

    results = geo.get("results")
    if not results:
        return f"[error: couldn't find a place called {location!r}]"

    place = results[0]
    try:
        fc = requests.get(_FORECAST, params={
            "latitude": place["latitude"],
            "longitude": place["longitude"],
            "current": "temperature_2m,weather_code,wind_speed_10m",
            "daily": "temperature_2m_max,temperature_2m_min",
            "timezone": "auto",
        }, timeout=15).json()
    except requests.RequestException as e:
        return f"[error: forecast failed: {e}]"

    return _format(
        place.get("name", location),
        place.get("country", ""),
        fc.get("current", {}),
        fc.get("daily", {}),
    )
