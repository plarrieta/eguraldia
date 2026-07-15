#!/usr/bin/env python3
from __future__ import annotations

import csv, json, math, re, statistics, time
import urllib.error, urllib.request
import xml.etree.ElementTree as ET
from collections import defaultdict
from datetime import datetime
from pathlib import Path

STATIONS = {
    "Alegia": {"code": "C0E9", "alt": 90},
    "Belauntza": {"code": "C0EA", "alt": 105},
    "Zizurkil": {"code": "C029", "alt": 149},
    "Araxes": {"code": "C0E8", "alt": 141},
}

INPUT_CSV = Path("tmp/tenperatura_anomaliak.csv")
OUTPUT_JSON = Path("tmp/lau_estazioen_konparazioa.json")
OUTPUT_CSV = Path("tmp/lau_estazioen_konparazioa.csv")
TIMEOUT = 45
USER_AGENT = "Alegia-Lau-Estazio-Diagnostikoa/1.0"
TEMP_TAG = "Tem.Aire._a_100cm"
LARGE_DIFFERENCE = 8.0
MIN_REFERENCE_STATIONS = 2


def fetch_bytes(url):
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.read()
    except (urllib.error.HTTPError, urllib.error.URLError, TimeoutError):
        return None


def direct_url(year, month, code):
    return (
        "https://opendata.euskadi.eus/contenidos/ds_meteorologicos/"
        f"met_stations_ds_{year}/opendata/{year}/{code}/{code}_{year}_{month}.xml"
    )


def local_name(tag):
    return tag.split("}", 1)[-1]


def normalized_name(tag):
    return "".join(ch for ch in local_name(tag).lower() if ch.isalnum())


def parse_number(raw):
    if raw is None:
        return None
    try:
        value = float(raw.strip().replace(",", "."))
        return value if math.isfinite(value) else None
    except ValueError:
        return None


def normalize_date(raw):
    raw = raw.strip().replace("/", "-")
    try:
        y, m, d = map(int, raw.split("-"))
        return f"{y:04d}-{m:02d}-{d:02d}"
    except Exception:
        return None


def find_temperature(meteoros):
    for child in list(meteoros):
        if local_name(child.tag) == TEMP_TAG:
            return parse_number(child.text)
    for child in list(meteoros):
        name = normalized_name(child.tag)
        if "temaire" in name or "temperaturaaire" in name:
            return parse_number(child.text)
    return None


def parse_month(raw):
    root = ET.fromstring(raw)
    result = {}

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

        hourly = {}
        for hour_element in list(day_element):
            if local_name(hour_element.tag).lower() != "hora":
                continue

            hour = (
                hour_element.attrib.get("Hora")
                or hour_element.attrib.get("hora")
                or ""
            ).strip()

            meteoros = next(
                (c for c in list(hour_element) if local_name(c.tag).lower() == "meteoros"),
                None,
            )
            if meteoros is None:
                continue

            value = find_temperature(meteoros)
            if value is not None:
                hourly[hour] = value

        result[day] = hourly

    return result


def load_suspicious_days():
    if not INPUT_CSV.exists():
        raise FileNotFoundError(f"Ez da aurkitu: {INPUT_CSV}")

    days = []
    with INPUT_CSV.open("r", encoding="utf-8-sig", newline="") as handle:
        reader = csv.DictReader(handle, delimiter=";")
        for row in reader:
            day = (row.get("DATA") or "").strip()
            if day:
                days.append(day)

    return sorted(set(days))


def classify_hour(alegia, refs):
    valid_refs = [v for v in refs.values() if v is not None]

    if alegia is None:
        return "ALEGIA_DATURIK_EZ", None, len(valid_refs)
    if len(valid_refs) < MIN_REFERENCE_STATIONS:
        return "EZIN_EGIAZTATU", None, len(valid_refs)

    ref_median = statistics.median(valid_refs)
    difference = alegia - ref_median

    if abs(difference) >= LARGE_DIFFERENCE:
        return "ARGI_AKASTUNA", round(difference, 2), len(valid_refs)
    if abs(difference) >= 4.0:
        return "SUSMAGARRIA", round(difference, 2), len(valid_refs)
    return "BAT_DATOR", round(difference, 2), len(valid_refs)


def compare_day(day, station_data):
    all_hours = sorted({
        hour
        for hourly in station_data.values()
        for hour in hourly
    })

    counters = defaultdict(int)
    hourly_results = []

    for hour in all_hours:
        alegia = station_data.get("Alegia", {}).get(hour)
        refs = {
            name: station_data.get(name, {}).get(hour)
            for name in ("Belauntza", "Zizurkil", "Araxes")
        }
        status, difference, ref_count = classify_hour(alegia, refs)
        counters[status] += 1

        valid_refs = [v for v in refs.values() if v is not None]
        ref_median = round(statistics.median(valid_refs), 2) if valid_refs else None

        hourly_results.append({
            "ordua": hour,
            "Alegia": alegia,
            "Belauntza": refs["Belauntza"],
            "Zizurkil": refs["Zizurkil"],
            "Araxes": refs["Araxes"],
            "erreferentzia_mediana": ref_median,
            "Alegia_minus_mediana": difference,
            "egoera": status,
            "erreferentzia_kopurua": ref_count,
        })

    comparable = (
        counters["BAT_DATOR"]
        + counters["SUSMAGARRIA"]
        + counters["ARGI_AKASTUNA"]
    )
    bad_ratio = counters["ARGI_AKASTUNA"] / comparable if comparable else 0.0
    suspicious_ratio = (
        counters["ARGI_AKASTUNA"] + counters["SUSMAGARRIA"]
    ) / comparable if comparable else 0.0

    if comparable < 12:
        day_status = "EZIN_EGIAZTATU"
    elif bad_ratio >= 0.20:
        day_status = "EGUN_AKASTUNA"
    elif suspicious_ratio >= 0.20:
        day_status = "EGUN_SUSMAGARRIA"
    else:
        day_status = "EGUN_FIDAGARRIA"

    return {
        "data": day,
        "egunaren_egoera": day_status,
        "ordu_konparagarriak": comparable,
        "argi_akastunak": counters["ARGI_AKASTUNA"],
        "susmagarriak": counters["SUSMAGARRIA"],
        "bat_datozenak": counters["BAT_DATOR"],
        "ezin_egiaztatu": counters["EZIN_EGIAZTATU"],
        "argi_akastunen_portzentajea": round(bad_ratio * 100, 2),
        "susmagarri_guztien_portzentajea": round(suspicious_ratio * 100, 2),
        "orduak": hourly_results,
    }


def write_outputs(results):
    OUTPUT_JSON.parent.mkdir(parents=True, exist_ok=True)

    OUTPUT_JSON.write_text(
        json.dumps(
            {
                "meta": {
                    "estazioak": STATIONS,
                    "iturria": "Euskalmet / Euskadi Open Data",
                    "sortua_utc": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "aztertutako_egunak": len(results),
                    "alde_handia_C": LARGE_DIFFERENCE,
                },
                "egunak": results,
            },
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    with OUTPUT_CSV.open("w", encoding="utf-8-sig", newline="") as handle:
        writer = csv.writer(handle, delimiter=";")
        writer.writerow([
            "DATA", "EGOERA", "ORDU_KONPARAGARRIAK",
            "ARGI_AKASTUNAK", "SUSMAGARRIAK", "BAT_DATOZENAK",
            "EZIN_EGIAZTATU", "ARGI_AKASTUNEN_PORTZENTAIA",
            "SUSMAGARRI_GUZTIEN_PORTZENTAIA",
        ])
        for r in results:
            writer.writerow([
                r["data"], r["egunaren_egoera"], r["ordu_konparagarriak"],
                r["argi_akastunak"], r["susmagarriak"], r["bat_datozenak"],
                r["ezin_egiaztatu"], r["argi_akastunen_portzentajea"],
                r["susmagarri_guztien_portzentajea"],
            ])


def main():
    suspicious_days = load_suspicious_days()
    print(f"Aztertzeko egun susmagarriak: {len(suspicious_days)}")

    months = sorted({
        (int(day[:4]), int(day[5:7]))
        for day in suspicious_days
    })

    cache = {}
    for station_name, info in STATIONS.items():
        code = info["code"]
        for year, month in months:
            print(f"{station_name}: {year}-{month:02d}")
            raw = fetch_bytes(direct_url(year, month, code))
            if raw is None:
                cache[(station_name, year, month)] = {}
                continue
            try:
                cache[(station_name, year, month)] = parse_month(raw)
            except ET.ParseError:
                cache[(station_name, year, month)] = {}
            time.sleep(0.05)

    results = []
    for day in suspicious_days:
        year, month = int(day[:4]), int(day[5:7])
        station_data = {
            name: cache.get((name, year, month), {}).get(day, {})
            for name in STATIONS
        }
        results.append(compare_day(day, station_data))

    order = {
        "EGUN_AKASTUNA": 0,
        "EGUN_SUSMAGARRIA": 1,
        "EZIN_EGIAZTATU": 2,
        "EGUN_FIDAGARRIA": 3,
    }
    results.sort(
        key=lambda r: (
            order.get(r["egunaren_egoera"], 9),
            -r["argi_akastunen_portzentajea"],
            r["data"],
        )
    )

    write_outputs(results)

    counts = defaultdict(int)
    for r in results:
        counts[r["egunaren_egoera"]] += 1

    print("\nLAU ESTAZIOEN KONPARAZIOAREN EMAITZA")
    print(f"EGUN_AKASTUNA: {counts['EGUN_AKASTUNA']}")
    print(f"EGUN_SUSMAGARRIA: {counts['EGUN_SUSMAGARRIA']}")
    print(f"EGUN_FIDAGARRIA: {counts['EGUN_FIDAGARRIA']}")
    print(f"EZIN_EGIAZTATU: {counts['EZIN_EGIAZTATU']}")
    print(f"JSON: {OUTPUT_JSON}")
    print(f"CSV: {OUTPUT_CSV}")

    print("\n20 EGUN GARRANTZITSUENAK")
    for r in results[:20]:
        print(
            f"{r['data']} | {r['egunaren_egoera']} | "
            f"argi akastunak %{r['argi_akastunen_portzentajea']:.1f} | "
            f"susmagarri guztiak %{r['susmagarri_guztien_portzentajea']:.1f} | "
            f"ordu konparagarriak {r['ordu_konparagarriak']}"
        )


if __name__ == "__main__":
    main()
