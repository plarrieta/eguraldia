
#!/usr/bin/env python3

import base64
import json
import os
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


API_URL = (
    "https://api.euskadi.eus/euskalmet/"
    "readings/aggregated/summarized/byDay/"
    "forStation/C0E9/at/2026/09/03"
)


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def main():
    private_pem = os.environ.get(
        "EUSKALMET_PRIVATE_KEY", ""
    ).replace("\\n", "\n")

    fingerprint = os.environ.get(
        "EUSKALMET_FINGERPRINT", ""
    ).strip()

    if not private_pem or not fingerprint:
        raise RuntimeError("GitHub Secrets falta dira.")

    private_key = serialization.load_pem_private_key(
        private_pem.encode("utf-8"),
        password=None
    )

    now = int(time.time())

    header = {"alg": "RS256", "typ": "JWT"}

    payload = {
        "aud": "met01.apikey",
        "iss": "eguraldia",
        "iat": now,
        "exp": now + 300,
        "version": "1.0.0",
        "loginId": fingerprint
    }

    def encode(obj):
        raw = json.dumps(
            obj, separators=(",", ":")
        ).encode("utf-8")
        return b64url(raw)

    unsigned = encode(header) + "." + encode(payload)

    signature = private_key.sign(
        unsigned.encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256()
    )

    token = unsigned + "." + b64url(signature)

    request = urllib.request.Request(
        API_URL,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(
            request, timeout=30
        ) as response:
            data = json.load(response)
            print("HTTP:", response.status)

    except urllib.error.HTTPError as exc:
        print("HTTP:", exc.code)
        print(exc.read().decode("utf-8")[:1000])
        raise SystemExit(1)

    print("ESTAZIOA:", data.get("station"))
    print("DATA:", data.get("date"))
    print()
    print("NEURKETA MOTA GUZTIAK:")
    print("------------------------")

    items = data.get("items", [])

    for item in items:
        print(
            "MOTA:", item.get("measureType"),
            "| NEURKETA:", item.get("measureId"),
            "| SENTSOREA:", item.get("sensorId")
        )

    print("------------------------")
    print("GUZTIRA:", len(items), "neurketa")


if __name__ == "__main__":
    main()
