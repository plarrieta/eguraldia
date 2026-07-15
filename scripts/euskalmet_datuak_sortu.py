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
from datetime import date, datetime, timezone
from pathlib import Path


# ============================================================
# KONFIGURAZIOA
# ============================================================

STATION_CODE = "C0E9"
STATION_NAME = "Alegia"

FIRST_YEAR = 2012
LAST_YEAR = date.today().year

OUTPUT_DIR = Path("data")
DATA_FILE = OUTPUT_DIR / "datuak.json"
ANOMALY_FILE = OUTPUT_DIR / "anomaliak.json"

# Lau estazioen diagnostikoak sortutako txostena.
# EGUN_AKASTUNA eta EGUN_SUSMAGARRIA diren egunak ez dira datuak.json-en sartuko.
STATION_COMPARISON_FILE = Path("tmp/lau_estazioen_konparazioa.csv")
EXCLUDED_COMPARISON_STATUSES = {"EGUN_AKASTUNA", "EGUN_SUSMAGARRIA"}

TIMEOUT = 45
USER_AGENT = "Alegia-Eguraldi-Historikoa/3.1"


# ============================================================
# FIDAGARRITASUN-IRIZPIDEAK
# ============================================================

# Muga fisiko zabalak.
# Ez dira muga klimatologiko estuak: benetako muturreko balioak
# ez ezabatzeko nahita zabal utzi dira.

ABS_TEMP_MIN = -25.0
ABS_TEMP_MAX = 50.0

ABS_HUM_MIN = 0.0
ABS_HUM_MAX = 100.0

ABS_RAIN_MIN = 0.0
ABS_RAIN_MAX_HOURLY = 300.0


# Eguneko medianarekiko gehiegizko desbideratzea
TEMP_MEDIAN_MAX_DEVIATION = 15.0


# Bi neurketa jarraituen arteko gehienezko jauzia
# XMLak 10 minutuko neurketak baditu, 15 °C-ko jauzia
# oso susmagarria da.
TEMP_MAX_CONSECUTIVE_JUMP = 15.0


# Egun batean tenperatura-neurketen %25 baino gehiago
# susmagarriak badira, egun osoa baztertuko da.
MAX_BAD_TEMP_RATIO = 0.25


# Egun batean gutxienez zenbat neurketa behar diren
MIN_TEMP_MEASUREMENTS_PER_DAY = 12


# ============================================================
# DESKARGA
# ============================================================

def fetch_bytes(url):
    req = urllib.request.Request(
        url,
        headers={"User-Agent": USER_AGENT}
    )

    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as response:
            return response.read()

    except (
        urllib.error.HTTPError,
        urllib.error.URLError,
        TimeoutError
    ):
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


# ============================================================
# XML HELBIDEAK AURKITU
# ============================================================

def index_urls(year):
    root = (
        "https://opendata.euskadi.eus/contenidos/"
        f"ds_meteorologicos/met_stations_ds_{year}/opendata"
    )

    return [
        f"{root}/url.txt",
        f"{root}/urls.txt",
        f"{root}/URL.txt",
    ]


def direct_url(year, month):
    return (
        "https://opendata.euskadi.eus/contenidos/"
        f"ds_meteorologicos/met_stations_ds_{year}/opendata/"
        f"{year}/{STATION_CODE}/"
        f"{STATION_CODE}_{year}_{month}.xml"
    )


def discover_urls(year):

    for url in index_urls(year):

        content = fetch_text(url)

        if not content:
            continue

        found = {
            u.rstrip(",;)")
            for u in re.findall(
                r"https?://[^\s\"'<>]+",
                content
            )
            if STATION_CODE.lower() in u.lower()
            and u.lower().endswith(".xml")
            and not u.lower().endswith(".xsd")
        }

        if found:

            def key(u):
                match = re.search(
                    rf"{year}_(\d+)\.xml$",
                    u,
                    re.I
                )

                return int(match.group(1)) if match else 99

            return sorted(found, key=key)

    found = []

    for month in range(1, 13):

        url = direct_url(year, month)

        if fetch_bytes(url) is not None:
            found.append(url)

        time.sleep(0.05)

    return found


# ============================================================
# XML LAGUNTZAILEAK
# ============================================================

def local_name(tag):
    return tag.split("}", 1)[-1]


def normalized_name(tag):
    return re.sub(
        r"[^a-z0-9]",
        "",
        local_name(tag).lower()
    )


def number(raw):

    if raw is None:
        return None

    try:
        value = float(
            raw.strip().replace(",", ".")
        )

        return value if math.isfinite(value) else None

    except ValueError:
        return None


def find_value(meteoros, kind):

    patterns = {
        "temperature": (
            "temaire",
            "temperaturaaire",
            "airtemperature"
        ),

        "rain": (
            "precip",
            "precipitacion",
            "prezipitazio"
        ),

        "humidity": (
            "humedad",
            "hezetasun",
            "humidity"
        ),
    }

    for child in list(meteoros):

        if any(
            pattern in normalized_name(child.tag)
            for pattern in patterns[kind]
        ):
            return number(child.text)

    return None


def normalize_date(raw):

    for fmt in (
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%Y/%m/%d"
    ):

        try:
            return datetime.strptime(
                raw.strip(),
                fmt
            ).strftime("%Y-%m-%d")

        except ValueError:
            pass

    return None


# ============================================================
# TENPERATURA-DATUEN FIDAGARRITASUNA
# ============================================================

def analyze_temperatures(values, day, anomalies):

    if not values:
        return None

    total = len(values)
    bad_indexes = set()

    # --------------------------------------------------------
    # 1. Muga absolutuetatik kanpoko balioak
    # --------------------------------------------------------

    for i, value in enumerate(values):

        if not (
            ABS_TEMP_MIN <= value <= ABS_TEMP_MAX
        ):
            bad_indexes.add(i)

            anomalies.append({
                "data": day,
                "aldagaia": "tenperatura",
                "balioa": value,
                "arrazoia": "muga absolutuetatik kanpo"
            })

    # --------------------------------------------------------
    # 2. Eguneko medianarekiko desbideratze handiak
    # --------------------------------------------------------

    physically_valid = [
        value
        for value in values
        if ABS_TEMP_MIN <= value <= ABS_TEMP_MAX
    ]

    if len(physically_valid) >= 3:

        median = statistics.median(
            physically_valid
        )

        for i, value in enumerate(values):

            if i in bad_indexes:
                continue

            if abs(value - median) > TEMP_MEDIAN_MAX_DEVIATION:

                bad_indexes.add(i)

                anomalies.append({
                    "data": day,
                    "aldagaia": "tenperatura",
                    "balioa": value,
                    "arrazoia":
                        "eguneko medianatik gehiegi aldenduta"
                })

    # --------------------------------------------------------
    # 3. Neurketa jarraituen arteko jauzi bortitzak
    # --------------------------------------------------------

    for i in range(1, len(values)):

        previous = values[i - 1]
        current = values[i]

        if abs(current - previous) > TEMP_MAX_CONSECUTIVE_JUMP:

            # Ez dugu automatikoki bi balioetako bat aukeratzen.
            # Jauzia egunaren fidagarritasuna neurtzeko erabiltzen da.

            anomalies.append({
                "data": day,
                "aldagaia": "tenperatura",
                "balioa": current,
                "arrazoia":
                    f"aurreko neurketarekiko jauzi bortitza: "
                    f"{previous} -> {current}"
            })

            bad_indexes.add(i)

    # --------------------------------------------------------
    # 4. Egunaren fidagarritasuna
    # --------------------------------------------------------

    bad_count = len(bad_indexes)
    bad_ratio = bad_count / total

    if total < MIN_TEMP_MEASUREMENTS_PER_DAY:

        anomalies.append({
            "data": day,
            "aldagaia": "eguna",
            "balioa": total,
            "arrazoia":
                "tenperatura-neurketa gutxiegi"
        })

        return None

    if bad_ratio > MAX_BAD_TEMP_RATIO:

        anomalies.append({
            "data": day,
            "aldagaia": "eguna",
            "balioa": round(bad_ratio * 100, 1),
            "arrazoia":
                "tenperatura-neurketa susmagarri gehiegi; "
                "egun osoa baztertuta"
        })

        return None

    # --------------------------------------------------------
    # 5. Balio fidagarriak bakarrik itzuli
    # --------------------------------------------------------

    cleaned = [
        value
        for i, value in enumerate(values)
        if i not in bad_indexes
    ]

    if not cleaned:
        return None

    return cleaned


# ============================================================
# XML HILABETEA PROZESATU
# ============================================================

def parse_month(raw, anomalies):

    try:
        root = ET.fromstring(raw)

    except ET.ParseError as exc:

        anomalies.append({
            "data": None,
            "aldagaia": "XML",
            "balioa": None,
            "arrazoia": str(exc)
        })

        return []

    rows = []

    day_elements = [
        element
        for element in root.iter()
        if local_name(element.tag).lower() == "dia"
    ]

    for day_element in day_elements:

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

        hour_elements = [
            element
            for element in list(day_element)
            if local_name(element.tag).lower() == "hora"
        ]

        for hour in hour_elements:

            meteoros = next(
                (
                    element
                    for element in list(hour)
                    if local_name(element.tag).lower()
                    == "meteoros"
                ),
                None
            )

            if meteoros is None:
                continue

            temp = find_value(
                meteoros,
                "temperature"
            )

            rain = find_value(
                meteoros,
                "rain"
            )

            hum = find_value(
                meteoros,
                "humidity"
            )

            if temp is not None:
                temperatures.append(temp)

            if rain is not None:

                if (
                    ABS_RAIN_MIN
                    <= rain
                    <= ABS_RAIN_MAX_HOURLY
                ):
                    rains.append(rain)

                else:
                    anomalies.append({
                        "data": day,
                        "aldagaia": "prezipitazioa",
                        "balioa": rain,
                        "arrazoia": "mugatik kanpo"
                    })

            if hum is not None:

                if (
                    ABS_HUM_MIN
                    <= hum
                    <= ABS_HUM_MAX
                ):
                    humidities.append(hum)

                else:
                    anomalies.append({
                        "data": day,
                        "aldagaia": "hezetasuna",
                        "balioa": hum,
                        "arrazoia":
                            "0–100 % tartetik kanpo"
                    })

        temperatures = analyze_temperatures(
            temperatures,
            day,
            anomalies
        )

        # Egunaren tenperatura-datuak ez badira fidagarriak,
        # egun osoa ez da datuak.json fitxategian sartuko.

        if not temperatures:
            continue

        rows.append({
            "data": day,
            "estazioa": STATION_NAME,
            "kodea": STATION_CODE,
            "max": round(
                max(temperatures),
                1
            ),
            "min": round(
                min(temperatures),
                1
            ),
            "euria": (
                round(sum(rains), 1)
                if rains
                else 0.0
            ),
            "hezetasuna": (
                round(
                    statistics.mean(humidities),
                    1
                )
                if humidities
                else None
            ),
        })

    return rows


# ============================================================
# LAU ESTAZIOEN KALITATE-KONTROLA
# ============================================================

def load_station_comparison():
    """
    Lau estazioen konparazio-txostena irakurtzen du.

    Itzultzen du:
      - baztertutako daten hiztegia
      - egoera bakoitzeko kopurua
    """
    excluded_dates = {}
    status_counts = {}

    if not STATION_COMPARISON_FILE.exists():
        print(
            f"OHARRA: ez da aurkitu {STATION_COMPARISON_FILE}. "
            "Lau estazioen kanpo-iragazkia ez da aplikatuko."
        )
        return excluded_dates, status_counts

    with STATION_COMPARISON_FILE.open(
        "r",
        encoding="utf-8-sig",
        newline=""
    ) as handle:
        reader = csv.DictReader(handle, delimiter=";")

        required = {"DATA", "EGOERA"}
        if not required.issubset(set(reader.fieldnames or [])):
            raise RuntimeError(
                f"{STATION_COMPARISON_FILE} fitxategiak "
                "DATA eta EGOERA zutabeak behar ditu."
            )

        for row in reader:
            day = (row.get("DATA") or "").strip()
            status = (row.get("EGOERA") or "").strip()

            if not day or not status:
                continue

            status_counts[status] = status_counts.get(status, 0) + 1

            if status in EXCLUDED_COMPARISON_STATUSES:
                excluded_dates[day] = {
                    "egoera": status,
                    "argi_akastunen_portzentajea": (
                        row.get("ARGI_AKASTUNEN_PORTZENTAIA") or ""
                    ).strip(),
                    "susmagarri_guztien_portzentajea": (
                        row.get("SUSMAGARRI_GUZTIEN_PORTZENTAIA") or ""
                    ).strip(),
                }

    print(
        "Lau estazioen txostena: "
        f"{len(excluded_dates)} egun baztertuko dira "
        f"({', '.join(sorted(EXCLUDED_COMPARISON_STATUSES))})."
    )

    return excluded_dates, status_counts


# ============================================================
# PROGRAMA NAGUSIA
# ============================================================

def main():

    OUTPUT_DIR.mkdir(
        parents=True,
        exist_ok=True
    )

    by_date = {}
    anomalies = []

    excluded_dates, comparison_status_counts = load_station_comparison()

    for year in range(
        FIRST_YEAR,
        LAST_YEAR + 1
    ):

        urls = discover_urls(year)

        print(
            f"{year}: {len(urls)} XML"
        )

        for url in urls:

            raw = fetch_bytes(url)

            if raw is None:
                continue

            for row in parse_month(
                raw,
                anomalies
            ):
                day = row["data"]

                if day in excluded_dates:
                    comparison = excluded_dates[day]

                    anomalies.append({
                        "data": day,
                        "aldagaia": "eguna",
                        "balioa": None,
                        "arrazoia":
                            "lau estazioen konparazioaren arabera baztertuta",
                        "lau_estazioen_egoera":
                            comparison["egoera"],
                        "argi_akastunen_portzentajea":
                            comparison["argi_akastunen_portzentajea"],
                        "susmagarri_guztien_portzentajea":
                            comparison["susmagarri_guztien_portzentajea"],
                    })

                    continue

                by_date[day] = row

            time.sleep(0.08)

    rows = [
        by_date[day]
        for day in sorted(by_date)
    ]

    if not rows:
        raise RuntimeError(
            "Ez da daturik aurkitu."
        )

    meta = {
        "iturria":
            "Euskalmet / Euskadi Open Data",

        "estazioa":
            STATION_NAME,

        "kodea":
            STATION_CODE,

        "sortua_utc":
            datetime.now(timezone.utc)
            .replace(microsecond=0)
            .isoformat()
            .replace("+00:00", "Z"),

        "lehen_data":
            rows[0]["data"],

        "azken_data":
            rows[-1]["data"],

        "egun_kopurua":
            len(rows),

        "anomalia_kopurua":
            len(anomalies),

        "script_bertsioa":
            "3.1",

        "lau_estazioen_txostena":
            str(STATION_COMPARISON_FILE),

        "lau_estazioen_egoera_kopuruak":
            comparison_status_counts,

        "lau_estazioen_arabera_baztertutako_egunak":
            len(excluded_dates),
    }

    DATA_FILE.write_text(
        json.dumps(
            {
                "meta": meta,
                "datuak": rows
            },
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    ANOMALY_FILE.write_text(
        json.dumps(
            {
                "meta": meta,
                "anomaliak": anomalies
            },
            ensure_ascii=False,
            indent=2
        ) + "\n",
        encoding="utf-8"
    )

    print(
        f"Sortuta: {DATA_FILE} "
        f"({len(rows)} egun)"
    )

    print(
        f"Sortuta: {ANOMALY_FILE} "
        f"({len(anomalies)} anomalia)"
    )

    print(
        "Lau estazioen arabera baztertutako egunak: "
        f"{len(excluded_dates)}"
    )


if __name__ == "__main__":
    main()
