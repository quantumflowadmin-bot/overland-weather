import os
import sys
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from twilio.rest import Client

LOCATIONS = [
    {"name": "Cradle Mountain", "lat": -41.68, "lon": 145.94, "note": "North - front arrival"},
    {"name": "Mt Pelion West",  "lat": -41.83, "lon": 145.97, "note": "Mid - alpine core"},
    {"name": "Lake St Clair",   "lat": -42.06, "lon": 146.17, "note": "South - front exit"},
]

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

CURRENT_FIELDS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m",
    "precipitation", "snowfall", "weather_code",
    "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "visibility",
]

WMO_CODES = {
    0: "Clear sky", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Light drizzle", 53: "Drizzle", 55: "Heavy drizzle",
    61: "Light rain", 63: "Rain", 65: "Heavy rain",
    71: "Light snow", 73: "Snow", 75: "Heavy snow", 77: "Snow grains",
    80: "Light showers", 81: "Showers", 82: "Heavy showers",
    85: "Snow showers", 86: "Heavy snow showers",
    95: "Thunderstorm", 96: "Thunderstorm w/ hail", 99: "Thunderstorm w/ heavy hail",
}

WIND_DIRECTIONS = ["N","NNE","NE","ENE","E","ESE","SE","SSE","S","SSW","SW","WSW","W","WNW","NW","NNW"]

def degrees_to_cardinal(degrees):
    return WIND_DIRECTIONS[round(degrees / 22.5) % 16]

def weather_code_to_text(code):
    return WMO_CODES.get(code, f"Code {code}")

def is_dangerous(data):
    return (
        data["wind_gusts"] > 80 or data["snowfall"] > 2 or
        data["precipitation"] > 10 or data["visibility"] < 1000 or
        data["weather_code"] in (95, 96, 99)
    )

def fetch_weather(lat, lon):
    params = {
        "latitude": lat, "longitude": lon,
        "current": ",".join(CURRENT_FIELDS),
        "timezone": "Australia/Hobart",
    }
    resp = requests.get(OPEN_METEO_URL, params=params, timeout=10)
    resp.raise_for_status()
    c = resp.json()["current"]
    return {
        "temperature": c["temperature_2m"], "feels_like": c["apparent_temperature"],
        "humidity": c["relative_humidity_2m"], "precipitation": c["precipitation"],
        "snowfall": c["snowfall"], "weather_code": c["weather_code"],
        "wind_speed": c["wind_speed_10m"], "wind_gusts": c["wind_gusts_10m"],
        "wind_direction": degrees_to_cardinal(c["wind_direction_10m"]),
        "visibility": c["visibility"],
    }

def format_location_block(loc, data):
    danger = "DANGER " if is_dangerous(data) else ""
    return "\n".join([
        f"{danger}{loc['name']} ({loc['note']})",
        f"  {weather_code_to_text(data['weather_code'])}",
        f"  Temp: {data['temperature']}C (feels {data['feels_like']}C)",
        f"  Wind: {data['wind_speed']} km/h {data['wind_direction']}, gusts {data['wind_gusts']} km/h",
        f"  Rain: {data['precipitation']} mm  Snow: {data['snowfall']} cm",
        f"  Humidity: {data['humidity']}%  Vis: {data['visibility']/1000:.1f} km",
    ])

def build_message(results):
    now = datetime.now(ZoneInfo("Australia/Hobart")).strftime("%a %d %b, %H:%M AEDT")
    any_danger = any(is_dangerous(r["data"]) for r in results)
    header = f"OVERLAND WEATHER - {now}"
    if any_danger:
        header += "\nDANGEROUS CONDITIONS - check before departing"
    blocks = [format_location_block(r["loc"], r["data"]) for r in results]
    return "\n\n".join([header] + blocks + ["Data: open-meteo.com"])

def send_sms(body):
    client = Client(os.environ["TWILIO_ACCOUNT_SID"], os.environ["TWILIO_AUTH_TOKEN"])
    for number in [os.environ["TWILIO_TO_NUMBER"], os.environ["TWILIO_TO_NUMBER_2"]]:
        client.messages.create(
            body=body,
            from_=os.environ["TWILIO_FROM_NUMBER"],
            to=number,
        )

def main():
    print("Fetching weather for Overland Track triangulation points...")
    results, errors = [], []
    for loc in LOCATIONS:
        try:
            data = fetch_weather(loc["lat"], loc["lon"])
            results.append({"loc": loc, "data": data})
            print(f"  OK {loc['name']}")
        except Exception as e:
            errors.append(f"{loc['name']}: {e}")
            print(f"  FAILED {loc['name']}: {e}")
    if not results:
        print("All fetches failed - aborting.")
        sys.exit(1)
    message = build_message(results)
    if errors:
        message += f"\n\nFailed to fetch: {', '.join(errors)}"
    print("\n--- Message preview ---")
    print(message)
    print("-----------------------\n")
    send_sms(message)
    print("SMS sent to all recipients.")

if __name__ == "__main__":
    main()