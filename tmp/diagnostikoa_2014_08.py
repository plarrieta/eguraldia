#!/usr/bin/env python3
from __future__ import annotations

import urllib.request
import xml.etree.ElementTree as ET

URL = (
    "https://opendata.euskadi.eus/contenidos/ds_meteorologicos/"
    "met_stations_ds_2014/opendata/2014/C0E9/C0E9_2014_8.xml"
)

TARGET_DAYS = {"2014-8-07", "2014-8-08", "2014-08-07", "2014-08-08"}
TEMP_TAG = "Tem.Aire._a_100cm"


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def download_xml(url: str) -> bytes:
    request = urllib.request.Request(
        url,
        headers={"User-Agent": "Alegia-Eguraldi-Diagnostikoa/1.0"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        return response.read()


def parse_temperature(element: ET.Element) -> float | None:
    text = (element.text or "").strip().replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def main() -> None:
    print("XML deskargatzen...")
    raw = download_xml(URL)
    root = ET.fromstring(raw)

    found_days = 0

    for day_element in root.iter():
        if local_name(day_element.tag).lower() != "dia":
            continue

        day = (
            day_element.attrib.get("Dia")
            or day_element.attrib.get("dia")
            or ""
        ).strip()

        if day not in TARGET_DAYS:
            continue

        found_days += 1
        readings: list[tuple[str, float]] = []

        for hour_element in list(day_element):
            if local_name(hour_element.tag).lower() != "hora":
                continue

            hour = (
                hour_element.attrib.get("Hora")
                or hour_element.attrib.get("hora")
                or "??:??"
            ).strip()

            meteoros = next(
                (
                    child
                    for child in list(hour_element)
                    if local_name(child.tag).lower() == "meteoros"
                ),
                None,
            )
            if meteoros is None:
                continue

            temp_element = next(
                (
                    child
                    for child in list(meteoros)
                    if local_name(child.tag) == TEMP_TAG
                ),
                None,
            )
            if temp_element is None:
                continue

            value = parse_temperature(temp_element)
            if value is not None:
                readings.append((hour, value))

        print()
        print("=" * 62)
        print(f"EGUNA: {day}")
        print("=" * 62)

        if not readings:
            print("Ez da tenperatura-neurketarik aurkitu.")
            continue

        for hour, value in readings:
            marker = ""
            if value < 0:
                marker = "  <<< NEGATIBOA"
            elif value > 40:
                marker = "  <<< OSO ALTUA"
            print(f"{hour:>5}  {value:>7.2f} °C{marker}")

        values = [value for _, value in readings]
        negative_count = sum(1 for value in values if value < 0)
        below_minus10 = sum(1 for value in values if value < -10)
        above40 = sum(1 for value in values if value > 40)

        print()
        print("LABURPENA")
        print(f"Neurketa kopurua: {len(values)}")
        print(f"Minimo gordina: {min(values):.2f} °C")
        print(f"Maximo gordina: {max(values):.2f} °C")
        print(f"0 °C azpitik: {negative_count}")
        print(f"-10 °C azpitik: {below_minus10}")
        print(f"40 °C gainetik: {above40}")

    if found_days == 0:
        raise RuntimeError("Ez dira 2014ko abuztuaren 7 eta 8ko egunak aurkitu.")


if __name__ == "__main__":
    main()
