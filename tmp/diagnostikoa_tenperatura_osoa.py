#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import re
import statistics
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

STATION_CODE = "C0E9"
STATION_NAME = "Alegia"
FIRST_YEAR = 2012
LAST_YEAR = date.today().year

TIMEOUT = 45
USER_AGENT = "Alegia-Eguraldi-Diagnostikoa/3.0"

OUTPUT_DIR = Path("tmp")
JSON_FILE = OUTPUT_DIR / "tenperatura_anomaliak.json"
CSV_FILE = OUTPUT_DIR / "tenperatura_anomaliak.csv"

TEMP_TAG = "Tem.Aire._a_100cm"

ABS_TEMP_MIN = -20.0
ABS_TEMP_MAX = 45.0
MAX_CONSECUTIVE_JUMP = 12.0
MAX_LOCAL_DEVIATION = 10.0
MAX_DAILY_RANGE = 35.0
MIN_READINGS_PER_DAY = 100
SUSPICIOUS_RATIO_LIMIT = 0.08


def fetch_bytes(url: str) -> bytes | None:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def fetch_text(url: str) -> str | None:
    raw = fetch_bytes(url)
    if raw is None:
        return None
    for encoding in ("utf-8", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            pass
    return raw.decode("utf-8", errors="replace")


def index_urls(year: int) -> list[str]:
    root = (
        "https://opendata.euskadi.eus/contenidos/ds_meteorologicos/"
        f"met_stations_ds_{year}/opendata"
    )
    return [f"{root}/url.txt", f"{root}/urls.txt", f"{root}/URL.txt"]


def direct_url(year: int, month: int) -> str:
    return (
        "https://opendata.euskadi.eus/contenidos/ds_meteorologicos/"
        f"met_stations_ds_{year}/opendata/{year}/{STATION_CODE}/"
        f"{STATION_CODE}_{year}_{month}.xml"
    )


def discover_urls(year: int) -> list[str]:
    for url in index_urls(year):
        content = fetch_text(url)
        if not content:
            continue

        found = {
            item.rstrip(",;)")
            for item in re.findall(r"https?://[^\s\"'<>]+", content)
            if STATION_CODE.lower() in item.lower()
            and item.lower().endswith(".xml")
            and not item.lower().endswith(".xsd")
        }

        if found:
            def month_key(item: str) -> int:
                match = re.search(rf"{year}_(\d+)\.xml$", item, re.I)
                return int(match.group(1)) if match else 99

            return sorted(found, key=month_key)

    found = []
    for month in range(1, 13):
        url = direct_url(year, month)
        if fetch_bytes(url) is not None:
            found.append(url)
        time.sleep(0.05)
    return found


def local_name(tag: str) -> str:
    return tag.split("}", 1)[-1]


def normalize_date(raw: str) -> str | None:
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def parse_number(raw: str | None) -> float | None:
    if raw is None:
        return None
    try:
        value = float(raw.strip().replace(",", "."))
    except ValueError:
        return None
    return value if math.isfinite(value) else None


def extract_day_readings(day_element: ET.Element) -> list[dict]:
    readings = []

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

        value = parse_number(temp_element.text)
        if value is not None:
            readings.append({"ordua": hour, "balioa": value})

    return readings


def local_median(values: list[float], index: int, radius: int = 2) -> float | None:
    neighbors = [
        values[i]
        for i in range(max(0, index - radius), min(len(values), index + radius + 1))
        if i != index
    ]
    return statistics.median(neighbors) if len(neighbors) >= 2 else None


def analyze_day(day: str, readings: list[dict]) -> dict | None:
    if not readings:
        return None

    values = [item["balioa"] for item in readings]
    suspicious_indexes: set[int] = set()
    reasons_by_index: dict[int, list[str]] = {}

    def mark(index: int, reason: str) -> None:
        suspicious_indexes.add(index)
        reasons_by_index.setdefault(index, []).append(reason)

    for i, value in enumerate(values):
        if value < ABS_TEMP_MIN or value > ABS_TEMP_MAX:
            mark(i, "muga absolutuetatik kanpo")

    for i in range(1, len(values)):
        jump = abs(values[i] - values[i - 1])
        if jump > MAX_CONSECUTIVE_JUMP:
            mark(i, f"aurreko neurketarekiko {jump:.1f} °C-ko jauzia")

    for i, value in enumerate(values):
        median = local_median(values, i)
        if median is None:
            continue
        deviation = abs(value - median)
        if deviation > MAX_LOCAL_DEVIATION:
            mark(i, f"inguruko medianatik {deviation:.1f} °C aldenduta")

    raw_range = max(values) - min(values)
    suspicious_ratio = len(suspicious_indexes) / len(values)

    day_reasons = []
    if len(values) < MIN_READINGS_PER_DAY:
        day_reasons.append("neurketak gutxiegi")
    if raw_range > MAX_DAILY_RANGE:
        day_reasons.append(f"eguneko tartea {raw_range:.1f} °C")
    if suspicious_ratio > SUSPICIOUS_RATIO_LIMIT:
        day_reasons.append(f"neurketen %{suspicious_ratio * 100:.1f} susmagarriak")

    if not day_reasons and not suspicious_indexes:
        return None

    suspicious_readings = []
    for index in sorted(suspicious_indexes):
        item = readings[index]
        suspicious_readings.append({
            "ordua": item["ordua"],
            "balioa": round(item["balioa"], 2),
            "arrazoiak": reasons_by_index.get(index, []),
            "aurrekoa": round(values[index - 1], 2) if index > 0 else None,
            "hurrengoa": (
                round(values[index + 1], 2)
                if index + 1 < len(values)
                else None
            ),
        })

    return {
        "data": day,
        "neurketa_kopurua": len(values),
        "min_gordina": round(min(values), 2),
        "max_gordina": round(max(values), 2),
        "tarte_gordina": round(raw_range, 2),
        "susmagarri_kopurua": len(suspicious_indexes),
        "susmagarri_portzentajea": round(suspicious_ratio * 100, 2),
        "egunaren_arrazoiak": day_reasons,
        "neurketak": suspicious_readings,
    }


def parse_month(raw: bytes) -> list[dict]:
    root = ET.fromstring(raw)
    results = []

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

        result = analyze_day(day, extract_day_readings(day_element))
        if result is not None:
            results.append(result)

    return results


def write_outputs(results: list[dict]) -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    meta = {
        "estazioa": STATION_NAME,
        "kodea": STATION_CODE,
        "lehen_urtea": FIRST_YEAR,
        "azken_urtea": LAST_YEAR,
        "sortua_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "egun_susmagarriak": len(results),
    }

    JSON_FILE.write_text(
        json.dumps({"meta": meta, "egunak": results}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )

    with CSV_FILE.open("w", newline="", encoding="utf-8-sig") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow([
            "DATA",
            "NEURKETA_KOPURUA",
            "MIN_GORDINA",
            "MAX_GORDINA",
            "TARTE_GORDINA",
            "SUSMAGARRI_KOPURUA",
            "SUSMAGARRI_PORTZENTAIA",
            "EGUNAREN_ARRAZOIAK",
        ])
        for result in results:
            writer.writerow([
                result["data"],
                result["neurketa_kopurua"],
                result["min_gordina"],
                result["max_gordina"],
                result["tarte_gordina"],
                result["susmagarri_kopurua"],
                result["susmagarri_portzentajea"],
                " | ".join(result["egunaren_arrazoiak"]),
            ])


def main() -> None:
    results = []

    for year in range(FIRST_YEAR, LAST_YEAR + 1):
        urls = discover_urls(year)
        print(f"{year}: {len(urls)} XML")

        for url in urls:
            raw = fetch_bytes(url)
            if raw is None:
                print(f"  EZIN DESKARGATU: {url}")
                continue

            try:
                results.extend(parse_month(raw))
            except ET.ParseError as exc:
                print(f"  XML AKASTUNA: {url}: {exc}")

            time.sleep(0.05)

    results.sort(
        key=lambda item: (
            -item["susmagarri_portzentajea"],
            -item["tarte_gordina"],
            item["data"],
        )
    )

    write_outputs(results)

    print()
    print("=" * 72)
    print("DIAGNOSTIKOAREN EMAITZA")
    print("=" * 72)
    print(f"Egun susmagarriak: {len(results)}")
    print(f"JSON txostena: {JSON_FILE}")
    print(f"CSV txostena: {CSV_FILE}")

    print()
    print("20 EGUN SUSMAGARRIENAK")
    print("-" * 72)

    for result in results[:20]:
        reasons = ", ".join(result["egunaren_arrazoiak"]) or "neurketak susmagarriak"
        print(
            f"{result['data']} | "
            f"min {result['min_gordina']:.1f} °C | "
            f"max {result['max_gordina']:.1f} °C | "
            f"tartea {result['tarte_gordina']:.1f} °C | "
            f"susmagarriak %{result['susmagarri_portzentajea']:.1f} | "
            f"{reasons}"
        )


if __name__ == "__main__":
    main()
