#!/usr/bin/env python3
from __future__ import annotations

import json
import math
import re
import statistics
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import date, datetime, timezone
from pathlib import Path

STATION_CODE = "C0E9"
STATION_NAME = "Alegia"
FIRST_YEAR = 2012
LAST_YEAR = date.today().year

OUTPUT_DIR = Path("data")
DATA_FILE = OUTPUT_DIR / "datuak.json"
ANOMALY_FILE = OUTPUT_DIR / "anomaliak.json"

TIMEOUT = 45
USER_AGENT = "Alegia-Eguraldi-Historikoa/2.3"

# C0E9 XMLko aldagai zehatzak.
# Ez dugu "temaire" bezalako bilaketa zabala erabiliko.
EXACT_TAGS = {
    "temperature": "temairea100cm",
    "rain": "precipa140cm",
    "humidity": "humedada100cm",
}

# Euskalmeteko falta/errore-kode ohikoak eta muga fisikoak.
SENTINELS = {
    -9999.0, -999.9, -999.0, -99.9,
    99.9, 999.0, 999.9, 9999.0,
}

ABS_TEMP_MIN, ABS_TEMP_MAX = -20.0, 45.0
ABS_HUM_MIN, ABS_HUM_MAX = 0.0, 100.0
ABS_RAIN_MIN, ABS_RAIN_MAX_INTERVAL = 0.0, 100.0

# Egun bereko neurketen medianarekiko iragazkia.
TEMP_MEDIAN_MAX_DEVIATION = 12.0

# Gutxieneko neurketa kopurua egun bat onartzeko.
MIN_DAILY_TEMPERATURE_READINGS = 36


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
    root = (
        "https://opendata.euskadi.eus/contenidos/ds_meteorologicos/"
        f"met_stations_ds_{year}/opendata"
    )
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
                match = re.search(rf"{year}_(\d+)\.xml$", u, re.I)
                return int(match.group(1)) if match else 99

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
    except ValueError:
        return None

    if not math.isfinite(value):
        return None

    # Sentinel exactuak eta 799.9/-799.9 gisako kodeak baztertu.
    if value in SENTINELS or abs(value) >= 90 and abs(value) % 100 >= 90:
        return None

    return value


def find_exact_value(meteoros, kind):
    wanted = EXACT_TAGS[kind]
    for child in list(meteoros):
        if normalized_name(child.tag) == wanted:
            return number(child.text)
    return None


def normalize_date(raw):
    raw = raw.strip()
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%Y/%m/%d"):
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def add_anomaly(anomalies, day, variable, value, reason):
    anomalies.append({
        "data": day,
        "aldagaia": variable,
        "balioa": value,
        "arrazoia": reason,
    })


def seasonal_temperature_limits(day):
    """Muga fisiko oso zabalak: muturreko sentsore-akats nabarmenak soilik kentzeko."""
    return ABS_TEMP_MIN, ABS_TEMP_MAX


def hampel_filter(values, day, anomalies, window=3):
    """
    10 minutuko seriean jauzi isolatu eta ezinezkoak kentzen ditu.
    Balio bat baztertzen da inguruko neurketek beste maila koherente bat erakusten dutenean.
    """
    if len(values) < 5:
        return list(values)

    cleaned = []
    for i, value in enumerate(values):
        left = max(0, i - window)
        right = min(len(values), i + window + 1)
        neighbors = [values[j] for j in range(left, right) if j != i]

        if len(neighbors) < 3:
            cleaned.append(value)
            continue

        local_median = statistics.median(neighbors)
        deviations = [abs(v - local_median) for v in neighbors]
        mad = statistics.median(deviations)
        robust_sigma = 1.4826 * mad

        # Gutxienez 6 ºC-ko alde nabarmena eskatzen dugu;
        # serie egonkorretan MAD txikia izateak ez du balio normalik baztertuko.
        threshold = max(6.0, 5.0 * robust_sigma)

        # Aurreko eta hurrengo neurketek elkarrekin koherentzia badute,
        # baina uneko balioa urrun badago, piko isolatua da.
        isolated_spike = False
        if 0 < i < len(values) - 1:
            prev_v, next_v = values[i - 1], values[i + 1]
            isolated_spike = (
                abs(prev_v - next_v) <= 3.0
                and abs(value - prev_v) > 8.0
                and abs(value - next_v) > 8.0
            )

        if abs(value - local_median) > threshold or isolated_spike:
            add_anomaly(
                anomalies,
                day,
                "tenperatura",
                value,
                f"10 minutuko seriearekiko jauzi isolatua "
                f"(inguruko mediana {local_median:.1f} °C)",
            )
        else:
            cleaned.append(value)

    return cleaned


def clean_temperatures(values, day, anomalies):
    seasonal_min, seasonal_max = seasonal_temperature_limits(day)
    physically_valid = []

    for value in values:
        if seasonal_min <= value <= seasonal_max:
            physically_valid.append(value)
        else:
            add_anomaly(
                anomalies,
                day,
                "tenperatura",
                value,
                f"urtaroko muga zabaletatik kanpo "
                f"({seasonal_min}–{seasonal_max} °C)",
            )

    if len(physically_valid) < 3:
        add_anomaly(
            anomalies,
            day,
            "eguna",
            len(physically_valid),
            "3 tenperatura-neurketa baliodun baino gutxiago",
        )
        return []

    cleaned = hampel_filter(physically_valid, day, anomalies)

    if len(cleaned) < 3:
        add_anomaly(
            anomalies,
            day,
            "eguna",
            len(cleaned),
            "jauzi anomaloak kendu ondoren 3 neurketa baino gutxiago",
        )
        return []

    return cleaned


def robust_daily_extremes(values):
    """
    Neurketa bakarreko piko batek errekorra ez aldatzeko,
    hiru balio baxuenen eta hiru altuenen mediana erabiltzen da.
    """
    ordered = sorted(values)
    sample_size = 3 if len(ordered) >= 3 else len(ordered)
    minimum = statistics.median(ordered[:sample_size])
    maximum = statistics.median(ordered[-sample_size:])
    return round(maximum, 1), round(minimum, 1)


def parse_month(raw, anomalies):
    try:
        root = ET.fromstring(raw)
    except ET.ParseError as exc:
        add_anomaly(anomalies, None, "XML", None, str(exc))
        return []

    rows = []

    for day_element in [
        element
        for element in root.iter()
        if local_name(element.tag).lower() == "dia"
    ]:
        day = normalize_date(
            day_element.attrib.get("Dia")
            or day_element.attrib.get("dia")
            or ""
        )
        if not day:
            continue

        temperatures = []
        rains = []
        humidities = []

        for hour in [
            element
            for element in list(day_element)
            if local_name(element.tag).lower() == "hora"
        ]:
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

            temp = find_exact_value(meteoros, "temperature")
            rain = find_exact_value(meteoros, "rain")
            humidity = find_exact_value(meteoros, "humidity")

            if temp is not None:
                temperatures.append(temp)

            if rain is not None:
                if ABS_RAIN_MIN <= rain <= ABS_RAIN_MAX_INTERVAL:
                    rains.append(rain)
                else:
                    add_anomaly(
                        anomalies,
                        day,
                        "prezipitazioa",
                        rain,
                        "tarteko prezipitazio-mugatik kanpo",
                    )

            if humidity is not None:
                if ABS_HUM_MIN <= humidity <= ABS_HUM_MAX:
                    humidities.append(humidity)
                else:
                    add_anomaly(
                        anomalies,
                        day,
                        "hezetasuna",
                        humidity,
                        "0–100 % tartetik kanpo",
                    )

        temperatures = clean_temperatures(temperatures, day, anomalies)
        if not temperatures:
            continue

        max_temp, min_temp = robust_daily_extremes(temperatures)

        if min_temp > max_temp:
            add_anomaly(
                anomalies,
                day,
                "eguna",
                [min_temp, max_temp],
                "minimoa maximoa baino handiagoa",
            )
            continue

        rows.append({
            "data": day,
            "estazioa": STATION_NAME,
            "kodea": STATION_CODE,
            "max": max_temp,
            "min": min_temp,
            "euria": round(sum(rains), 1) if rains else 0.0,
            "hezetasuna": (
                round(statistics.mean(humidities), 1)
                if humidities
                else None
            ),
        })

    return rows


def main():
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    by_date = {}
    anomalies = []

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

    rows = [by_date[day] for day in sorted(by_date)]
    if not rows:
        raise RuntimeError("Ez da daturik aurkitu.")

    meta = {
        "iturria": "Euskalmet / Euskadi Open Data",
        "estazioa": STATION_NAME,
        "kodea": STATION_CODE,
        "sortua_utc": datetime.now(timezone.utc)
        .replace(microsecond=0)
        .isoformat()
        .replace("+00:00", "Z"),
        "lehen_data": rows[0]["data"],
        "azken_data": rows[-1]["data"],
        "egun_kopurua": len(rows),
        "anomalia_kopurua": len(anomalies),
        "script_bertsioa": "2.3",
        "erabilitako_eremuak": EXACT_TAGS,
        "tenperatura_garbiketa": "muga fisiko zabalak + jauzi isolatuen iragazkia + 3 neurketako mutur sendoak",
    }

    DATA_FILE.write_text(
        json.dumps(
            {"meta": meta, "datuak": rows},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    ANOMALY_FILE.write_text(
        json.dumps(
            {"meta": meta, "anomaliak": anomalies},
            ensure_ascii=False,
            indent=2,
        ) + "\n",
        encoding="utf-8",
    )

    print(f"Sortuta: {DATA_FILE} ({len(rows)} egun)")
    print(f"Sortuta: {ANOMALY_FILE} ({len(anomalies)} anomalia)")


if __name__ == "__main__":
    main()
