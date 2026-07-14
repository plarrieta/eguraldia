#!/usr/bin/env python3
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime


STATION_CODE = "C0E9"

# Webgunean orain agertzen diren 4 errekorrak
TARGET_DATES = {
    "2026-06-24": "Egun beroena",
    "2017-02-15": "Egun hotzena",
    "2025-06-13": "Egun euritsuena",
    "2012-04-28": "Hezetasun handiena",
}


def local_name(tag):
    return tag.split("}", 1)[-1]


def normalized_name(tag):
    return "".join(
        c for c in local_name(tag).lower()
        if c.isalnum()
    )


def number(raw):
    if raw is None:
        return None

    try:
        return float(raw.strip().replace(",", "."))
    except ValueError:
        return None


def direct_url(year, month):
    return (
        "https://opendata.euskadi.eus/contenidos/"
        f"ds_meteorologicos/met_stations_ds_{year}/opendata/"
        f"{year}/{STATION_CODE}/"
        f"{STATION_CODE}_{year}_{month}.xml"
    )


def download_xml(url):
    print(f"\nXML deskargatzen: {url}")

    request = urllib.request.Request(
        url,
        headers={
            "User-Agent": "Alegia-Eguraldi-Diagnostikoa/2.0"
        }
    )

    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def normalize_date(raw):
    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d",
    ):
        try:
            return datetime.strptime(
                raw.strip(), fmt
            ).strftime("%Y-%m-%d")
        except ValueError:
            pass

    return None


def find_value(meteoros, kind):
    patterns = {
        "temperature": (
            "temaire",
            "temperaturaaire",
            "airtemperature",
        ),
        "rain": (
            "precip",
            "precipitacion",
            "prezipitazio",
        ),
        "humidity": (
            "humedad",
            "hezetasun",
            "humidity",
        ),
    }

    for child in list(meteoros):
        name = normalized_name(child.tag)

        if any(
            pattern in name
            for pattern in patterns[kind]
        ):
            return number(child.text)

    return None


def analyse_day(day_element, date_string, record_name):
    temperatures = []
    rains = []
    humidities = []

    print("\n" + "=" * 70)
    print(f"{record_name.upper()}")
    print(f"EGUNA: {date_string}")
    print("=" * 70)

    for hour in list(day_element):
        if local_name(hour.tag).lower() != "hora":
            continue

        hour_text = (
            hour.attrib.get("Hora")
            or hour.attrib.get("hora")
            or "?"
        )

        meteoros = next(
            (
                element
                for element in list(hour)
                if local_name(element.tag).lower() == "meteoros"
            ),
            None,
        )

        if meteoros is None:
            continue

        temp = find_value(meteoros, "temperature")
        rain = find_value(meteoros, "rain")
        hum = find_value(meteoros, "humidity")

        if temp is not None:
            temperatures.append(temp)

        if rain is not None:
            rains.append(rain)

        if hum is not None:
            humidities.append(hum)

        print(
            f"{hour_text:>5} | "
            f"T: {temp if temp is not None else '--':>7} °C | "
            f"P: {rain if rain is not None else '--':>7} | "
            f"H: {hum if hum is not None else '--':>7} %"
        )

    print("\nLABURPENA")
    print("-" * 40)

    if temperatures:
        print(f"Tenperatura maximo gordina: {max(temperatures):.2f} °C")
        print(f"Tenperatura minimo gordina: {min(temperatures):.2f} °C")
        print(f"Tenperatura-neurketak: {len(temperatures)}")
    else:
        print("Ez dago tenperatura-daturik.")

    if rains:
        print(f"Prezipitazioen batura gordina: {sum(rains):.2f}")
        print(f"Prezipitazio-neurketak: {len(rains)}")
    else:
        print("Ez dago prezipitazio-daturik.")

    if humidities:
        print(f"Hezetasun maximo gordina: {max(humidities):.2f} %")
        print(
            f"Hezetasunaren batezbestekoa: "
            f"{sum(humidities) / len(humidities):.2f} %"
        )
        print(f"Hezetasun-neurketak: {len(humidities)}")
    else:
        print("Ez dago hezetasun-daturik.")


def main():
    # Urte-hilabete bakoitzeko XML bakarra deskargatu,
    # nahiz eta hilabete berean errekor bat baino gehiago egon.
    grouped = {}

    for date_string, record_name in TARGET_DATES.items():
        dt = datetime.strptime(date_string, "%Y-%m-%d")
        key = (dt.year, dt.month)

        grouped.setdefault(key, []).append(
            (date_string, record_name)
        )

    for (year, month), targets in sorted(grouped.items()):
        url = direct_url(year, month)

        try:
            raw = download_xml(url)
            root = ET.fromstring(raw)

        except Exception as exc:
            print(
                f"ERROREA {year}-{month:02d}: {exc}"
            )
            continue

        found_dates = set()

        for day_element in root.iter():
            if local_name(day_element.tag).lower() != "dia":
                continue

            day = normalize_date(
                day_element.attrib.get("Dia")
                or day_element.attrib.get("dia")
                or ""
            )

            if not day:
                continue

            for target_date, record_name in targets:
                if day == target_date:
                    analyse_day(
                        day_element,
                        target_date,
                        record_name,
                    )
                    found_dates.add(target_date)

        for target_date, record_name in targets:
            if target_date not in found_dates:
                print(
                    f"\nEZ DA AURKITU: "
                    f"{record_name} — {target_date}"
                )


if __name__ == "__main__":
    main()
