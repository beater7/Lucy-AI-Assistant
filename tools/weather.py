import datetime
import json
import re
from urllib.error import HTTPError, URLError
from urllib.parse import quote_plus
from urllib.request import Request, urlopen


def get_json(url):
    request = Request(url, headers={"User-Agent": "Lucy Voice Assistant"})
    try:
        with urlopen(request, timeout=20) as response:
            result = json.loads(response.read().decode("utf-8"))
    except HTTPError as error:
        details = error.read().decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Weather service returned HTTP {error.code}: {details}") from error
    except (URLError, TimeoutError) as error:
        raise RuntimeError("Couldn't connect to the weather service.") from error
    except json.JSONDecodeError as error:
        raise RuntimeError("The weather service returned invalid data.") from error
    return result

def known_philippines_locations(text):
    locations = []
    if re.search(r"\bmakati\b", text, re.IGNORECASE):
        locations.append("Makati City, Philippines")
    if re.search(
        r"\b(?:bgc|bonifacio\s+global\s+city|taguig)\b",
        text,
        re.IGNORECASE,
    ):
        locations.append("Taguig, Philippines")
    return locations

def lookup_location(location):
    if re.search(r"\bto(?:\s+[a-z])?\s*$", location, re.IGNORECASE):
        raise ValueError(f"I need the complete location name, not {location}.")

    aliases = known_philippines_locations(location)
    if len(aliases) == 1:
        location = aliases[0]

    parts = [part.strip() for part in location.split(",", maxsplit=1)]
    requested_name = re.sub(r"[^a-z0-9 ]", "", parts[0].lower()).split()
    requested_name = {
        token for token in requested_name if token not in {"city", "town", "the", "of"}
    }
    requested_country = parts[1].casefold() if len(parts) == 2 else None
    url = (
        "https://geocoding-api.open-meteo.com/v1/search"
        f"?name={quote_plus(location)}&count=10&language=en&format=json"
    )
    results = get_json(url).get("results", [])
    if not results:
        raise ValueError(f"I couldn't find a location called {location}.")

    matching_places = []
    for place in results:
        place_name = re.sub(
            r"[^a-z0-9 ]", "", place.get("name", "").lower()
        ).split()
        place_name = {
            token for token in place_name if token not in {"city", "town", "the", "of"}
        }
        country_matches = (
            not requested_country
            or place.get("country", "").casefold() == requested_country
        )
        if requested_name and requested_name.issubset(place_name) and country_matches:
            matching_places.append(place)

    if not matching_places:
        raise ValueError(f"I couldn't confidently match the location {location}.")
    place = matching_places[0]
    if place.get("feature_code") == "PCLI":
        raise ValueError(f"Which city in {location} should I look up?")
    return place

def describe_timezone(location=None):
    if location:
        place = lookup_location(location)
        zone = place.get("timezone")
        place_name = ", ".join(
            part for part in (place.get("name"), place.get("country")) if part
        )
        if not zone:
            raise ValueError(f"I couldn't find a time zone for {location}.")
        return f"{place_name} is in the {zone} time zone."

    local_time = datetime.datetime.now().astimezone()
    zone = local_time.tzname() or str(local_time.tzinfo)
    offset = local_time.strftime("%z")
    if len(offset) == 5:
        offset = f"{offset[:3]}:{offset[3:]}"
    return f"Your computer's local time zone is {zone} (UTC{offset})."

def describe_time(location=None):
    if not location:
        return f"The current time is {datetime.datetime.now():%I:%M %p}."

    place = lookup_location(location)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={place['latitude']}&longitude={place['longitude']}"
        "&current=temperature_2m&timezone=auto&forecast_days=1"
    )
    result = get_json(url)
    current = result.get("current", {})
    time_text = current.get("time")
    if not time_text:
        raise ValueError("The time service didn't return the current local time.")
    local_time = datetime.datetime.fromisoformat(time_text)
    place_name = ", ".join(
        part for part in (place.get("name"), place.get("country")) if part
    )
    zone = result.get("timezone")
    zone_text = f" in {zone}" if zone else ""
    return f"It's {local_time:%I:%M %p} in {place_name}{zone_text}."

def describe_weather(location, when):
    if not location:
        return None
    place = lookup_location(location)
    url = (
        "https://api.open-meteo.com/v1/forecast"
        f"?latitude={place['latitude']}&longitude={place['longitude']}"
        "&daily=weather_code,temperature_2m_max,temperature_2m_min"
        "&temperature_unit=celsius&timezone=auto&forecast_days=3"
    )
    forecast = get_json(url)
    daily = forecast.get("daily", {})
    dates = daily.get("time", [])
    index = 1 if when == "tomorrow" else 0
    if len(dates) <= index:
        raise ValueError("The weather service didn't return that forecast.")

    try:
        code = daily["weather_code"][index]
        high = daily["temperature_2m_max"][index]
        low = daily["temperature_2m_min"][index]
    except (KeyError, IndexError, TypeError) as error:
        raise ValueError("The weather service didn't return a complete forecast.") from error
    conditions = {
        0: "clear",
        1: "mostly clear",
        2: "partly cloudy",
        3: "overcast",
        45: "foggy",
        48: "foggy",
        51: "light drizzle",
        53: "drizzle",
        55: "heavy drizzle",
        61: "light rain",
        63: "rain",
        65: "heavy rain",
        71: "light snow",
        73: "snow",
        75: "heavy snow",
        80: "rain showers",
        81: "rain showers",
        82: "heavy rain showers",
        95: "thunderstorms",
        96: "thunderstorms with hail",
        99: "thunderstorms with hail",
    }.get(code, "changeable")
    day_name = "tomorrow" if index else "today"
    place_name = ", ".join(
        part for part in (place.get("name"), place.get("country")) if part
    )
    return (
        f"{day_name.capitalize()} in {place_name}, expect {conditions}, "
        f"with a high of {high:.0f} and a low of {low:.0f} degrees Celsius."
    )
