#!/usr/bin/env python3
from __future__ import annotations

import json, math, re, statistics, time
import urllib.error, urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

STATION_CODE = "C0E9"
STATION_NAME = "Alegia"
FIRST_YEAR = 2012
LAST_YEAR = date.today().year
OUTPUT_DIR = Path("data")
DATA_FILE = OUTPUT_DIR / "datuak.json"
ANOMALY_FILE = OUTPUT_DIR / "anomaliak.json"
TIMEOUT = 45
USER_AGENT = "Alegia-Eguraldi-Historikoa/2.0"

ABS_TEMP_MIN, ABS_TEMP_MAX = -35.0, 50.0
ABS_HUM_MIN, ABS_HUM_MAX = 0.0, 100.0
ABS_RAIN_MIN, ABS_RAIN_MAX_HOURLY = 0.0, 300.0
TEMP_MEDIAN_MAX_DEVIATION = 15.0


def fetch_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def fetch_text(url):
    raw = fetch_bytes(url)
    if raw is None:
        return None
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def index_urls(year):
    root = f"https://opendata.euskadi.eus/contenidos/ds_meteorologicos/met_stations_ds_{year}/opendata"
    return [f"{root}/url.txt", f"{root}/urls.txt", f"{root}/URL.txt"]


def direct_url(year, month):
    return (
        "https://opendata.euskadi.eus/contenidos/ds_meteorologicos/"
        f"met_stations_ds_{year}/opendata/{year}/{STATION_CODE}/"
        f"{STATION_CODE}_{year}_{month}.xml"
    )


def discover_urls(year):
    for url in index_urls(year):
        content = fetch_text(url)
        if not content:
            continue
        found = {
            u.rstrip(",;)")
            for u in re.findall(r"https?://[^\s\"'<>]+", content)
            if STATION_CODE.lower() in u.lower()
            and u.lower().endswith(".xml")
            and not u.lower().endswith(".xsd")
        }
        if found:
            def key(u):
                m = re.search(rf"{year}_(\d+)\.xml$", u, re.I)
                return int(m.group(1)) if m else 99
            return sorted(found, key=key)

    found = []
    for month in range(1, 13):
        url = direct_url(year, month)
        if fetch_bytes(url) is not None:
            found.append(url)
        time.sleep(0.05)
    return found


def local_name(tag):
    return tag.split("}", 1)[-1]


def normalized_name(tag):
    return re.sub(r"[^a-z0-9]", "", local_name(tag).lower())


def number(raw):
    if raw is None:
        return None
    try:
        value = float(raw.strip().replace(",", "."))
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def find_value(meteoros, kind):
    patterns = {
        "temperature": ("temaire", "temperaturaaire", "airtemperature"),
        "rain": ("precip", "precipitacion", "prezipitazio"),
        "humidity": ("humedad", "hezetasun", "humidity"),
    }
    for child in list(meteoros):
        if any(p in normalized_name(child.tag) for p in patterns[kind]):
            return number(child.text)
    return None


def normalize_date(raw):
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw.strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def clean_temperatures(values, day, anomalies):
    valid = []
    for value in values:
        if ABS_TEMP_MIN <= value <= ABS_TEMP_MAX:
            valid.append(value)
        else:
            anomalies.append({"data": day, "aldagaia": "tenperatura", "balioa": value,
                              "arrazoia": "muga fisikoetatik kanpo"})
    if len(valid) < 3:
        return valid
    median = statistics.median(valid)
    cleaned = []
    for value in valid:
        if abs(value - median) <= TEMP_MEDIAN_MAX_DEVIATION:
            cleaned.append(value)
        else:
            anomalies.append({"data": day, "aldagaia": "tenperatura", "balioa": value,
                              "arrazoia": "eguneko medianatik gehiegi aldenduta"})
    return cleaned


def parse_month(raw, anomalies):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        anomalies.append({"data": None, "aldagaia": "XML", "balioa": None,
                          "arrazoia": str(exc)})
        return []

    rows = []
    for day_element in [e for e in root.iter() if local_name(e.tag).lower() == "dia"]:
        day = normalize_date(day_element.attrib.get("Dia") or day_element.attrib.get("dia") or "")
        if not day:
            continue

        temperatures, rains, humidities = [], [], []
        for hour in [e for e in list(day_element) if local_name(e.tag).lower() == "hora"]:
            meteoros = next((e for e in list(hour) if local_name(e.tag).lower() == "meteoros"), None)
            if meteoros is None:
                continue

            temp = find_value(meteoros, "temperature")
            rain = find_value(meteoros, "rain")
            hum = find_value(meteoros, "humidity")

            if temp is not None:
                temperatures.append(temp)
            if rain is not None:
                if ABS_RAIN_MIN <= rain <= ABS_RAIN_MAX_HOURLY:
                    rains.append(rain)
                else:
                    anomalies.append({"data": day, "aldagaia": "prezipitazioa",
                                      "balioa": rain, "arrazoia": "mugatik kanpo"})
            if hum is not None:
                if ABS_HUM_MIN <= hum <= ABS_HUM_MAX:
                    humidities.append(hum)
                else:
                    anomalies.append({"data": day, "aldagaia": "hezetasuna",
                                      "balioa": hum, "arrazoia": "0–100 % tartetik kanpo"})

        temperatures = clean_temperatures(temperatures, day, anomalies)
        if not temperatures:
            continue

        rows.append({
            "data": day,
            "estazioa": STATION_NAME,
            "kodea": STATION_CODE,
            "max": round(max(temperatures), 1),
            "min": round(min(temperatures), 1),
            "euria": round(sum(rains), 1) if rains else 0.0,
            "hezetasuna": round(statistics.mean(humidities), 1) if humidities else None,
        })
    return rows


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    by_date, anomalies = {}, []

    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        urls = discover_urls(year)
        print(f"{year}: {len(urls)} XML")
        for url in urls:
            raw = fetch_bytes(url)
            if raw is None:
                continue
            for row in parse_month(raw, anomalies):
                by_date[row["data"]] = row
            time.sleep(0.08)

    rows = [by_date[d] for d in sorted(by_date)]
    if not rows:
        raise RuntimeError("Ez da daturik aurkitu.")

    meta = {
        "iturria": "Euskalmet / Euskadi Open Data",
        "estazioa": STATION_NAME,
        "kodea": STATION_CODE,
        "sortua_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "lehen_data": rows[0]["data"],
        "azken_data": rows[-1]["data"],
        "egun_kopurua": len(rows),
        "anomalia_kopurua": len(anomalies),
    }
    DATA_FILE.write_text(json.dumps({"meta": meta, "datuak": rows},
                                    ensure_ascii=False, indent=2) + "\n",
                         encoding="utf-8")
    ANOMALY_FILE.write_text(json.dumps({"meta": meta, "anomaliak": anomalies},
                                       ensure_ascii=False, indent=2) + "\n",
                            encoding="utf-8")
    print(f"Sortuta: {DATA_FILE} ({len(rows)} egun)")
    print(f"Sortuta: {ANOMALY_FILE} ({len(anomalies)} anomalia)")


if __name__ == "__main__":
    main()
