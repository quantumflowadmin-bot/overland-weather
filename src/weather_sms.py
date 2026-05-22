import os
import sys
import requests
from datetime import datetime
from zoneinfo import ZoneInfo
from twilio.rest import Client

LOCATIONS = [
    {"name": "Cradle Mountain", "lat": -41.68, "lon": 145.94, "note": "N"},
    {"name": "Mt Pelion West",  "lat": -41.83, "lon": 145.97, "note": "Mid"},
    {"name": "Lake St Clair",   "lat": -42.06, "lon": 146.17, "note": "S"},
]

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"

CURRENT_FIELDS = [
    "temperature_2m", "apparent_temperature", "relative_humidity_2m",
    "precipitation", "snowfall", "weather_code",
    "wind_speed_10m", "wind_gusts_10m", "wind_direction_10m", "visibility",
]

HOURLY_FIELDS = [
    "temperature_2m", "precipitation_probability", "precipitation",
    "snowfall", "weather_code", "wind_speed_10m", "wind_gusts_10m",
]

WMO_CODES = {
    0: "Clear", 1: "Mainly clear", 2: "Partly cloudy", 3: "Overcast",
    45: "Fog", 48: "Icy fog",
    51: "Lt drizzle", 53: "Drizzle", 55: "Hvy drizzle",
    61: "Lt rain", 63: "Rain", 65: "Hvy rain",
    71: "Lt snow", 73: "Snow", 75: "Hvy snow", 77: "Snow grains",
    80: "Lt showers", 81: "Showers", 82: "Hvy showers",
    85: "Snow showers", 86: "Hvy snow showers",
    95: "Thunderstorm", 96: "Storm+hail", 99: "Storm+hvy hail",
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
def fetch_weather(lat, lon, retries=3):
    params = {
        "latitude": lat, "longitude": lon,
        "current": ",".join(CURRENT_FIELDS),
        "hourly": ",".join(HOURLY_FIELDS),
        "forecast_days": 2,
        "timezone": "Australia/Hobart",
    }
    for attempt in range(retries):
        try:
            resp = requests.get(OPEN_METEO_URL, params=params, timeout=20)
            resp.raise_for_status()
            return resp.json()
        except requests.exceptions.Timeout:
            print(f"  Timeout attempt {attempt+1}/{retries} for {lat},{lon}")
            if attempt == retries - 1:
                raise
    raise Exception("Max retries exceeded")

def parse_current(data):
    c = data["current"]
    return {
        "temperature": c["temperature_2m"],
        "feels_like": c["apparent_temperature"],
        "humidity": c["relative_humidity_2m"],
        "precipitation": c["precipitation"],
        "snowfall": c["snowfall"],
        "weather_code": c["weather_code"],
        "wind_speed": c["wind_speed_10m"],
        "wind_gusts": c["wind_gusts_10m"],
        "wind_direction": degrees_to_cardinal(c["wind_direction_10m"]),
        "visibility": c["visibility"],
    }

def parse_outlook(data, current_time):
    times = data["hourly"]["time"]
    codes = data["hourly"]["weather_code"]
    temps = data["hourly"]["temperature_2m"]
    precip_prob = data["hourly"]["precipitation_probability"]
    precip = data["hourly"]["precipitation"]
    snow = data["hourly"]["snowfall"]
    gusts = data["hourly"]["wind_gusts_10m"]

    current_hour = current_time.strftime("%Y-%m-%dT%H:00")
    try:
        start = times.index(current_hour)
    except ValueError:
        start = 0

    next_12 = list(zip(
        times[start:start+12],
        codes[start:start+12],
        temps[start:start+12],
        precip_prob[start:start+12],
        precip[start:start+12],
        snow[start:start+12],
        gusts[start:start+12],
    ))

    max_gust = max(g for *_, g in next_12)
    max_precip = max(p for _, _, _, _, p, _, _ in next_12)
    max_snow = max(s for _, _, _, _, _, s, _ in next_12)
    max_prob = max(pp for _, _, _, pp, _, _, _ in next_12)
    min_temp = min(t for _, _, t, _, _, _, _ in next_12)
    worst_code = max(c for _, c, *_ in next_12)

    danger = max_gust > 80 or max_snow > 2 or worst_code in (95, 96, 99)

    summary = weather_code_to_text(worst_code)
    outlook = (
        f"12hr: {summary}\n"
        f"Lo:{min_temp:.0f}C Rain%:{max_prob}% "
        f"Mx gust:{max_gust:.0f}km/h"
    )
    if max_snow > 0:
        outlook += f" Snow:{max_snow:.1f}cm"
    if danger:
        outlook = "DANGER " + outlook
    return outlook

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
    tz = ZoneInfo("Australia/Hobart")
    now = datetime.now(tz)
    now_str = now.strftime("%a %d %b %H:%M")

    for loc in LOCATIONS:
        try:
            raw = fetch_weather(loc["lat"], loc["lon"])
            current = parse_current(raw)
            outlook = parse_outlook(raw, now)

            danger = "DANGER " if is_dangerous(current) else ""
            msg = (
                f"{danger}{loc['name']}({loc['note']}) {now_str}\n"
                f"{weather_code_to_text(current['weather_code'])}\n"
                f"Tmp:{current['temperature']}C fl:{current['feels_like']}C\n"
                f"Wnd:{current['wind_speed']}km/h {current['wind_direction']} "
                f"gst:{current['wind_gusts']}km/h\n"
                f"Rain:{current['precipitation']}mm Snow:{current['snowfall']}cm\n"
                f"Hum:{current['humidity']}% Vis:{current['visibility']/1000:.1f}km\n"
                f"{outlook}"
            )
            print(f"\n{msg}\n")
            send_sms(msg)
            print(f"  Sent: {loc['name']}")
        except Exception as e:
            print(f"  FAILED {loc['name']}: {e}")

if __name__ == "__main__":
    main()
