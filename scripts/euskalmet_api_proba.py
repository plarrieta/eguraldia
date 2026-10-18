
#!/usr/bin/env python3

import base64
import json
import os
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


# ==========================================
# EUSKALMET API - KONEXIO PROBA
# Alegia (C0E9) - 2026/09/03
# ==========================================

API_URL = (
    "https://api.euskadi.eus/euskalmet/"
    "readings/aggregated/summarized/byDay/"
    "forStation/C0E9/at/2026/09/03"
)


def b64url(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


def encode_json(obj):
    raw = json.dumps(
        obj,
        separators=(",", ":")
    ).encode("utf-8")

    return b64url(raw)


def main():

    # GitHub Secrets irakurri
    private_pem = os.environ.get(
        "EUSKALMET_PRIVATE_KEY", ""
    ).replace("\\n", "\n")

    fingerprint = os.environ.get(
        "EUSKALMET_FINGERPRINT", ""
    ).strip()

    if not private_pem or not fingerprint:
        raise RuntimeError(
            "Euskalmeten gako pribatua edo fingerprint-a falta da."
        )

    # Gako pribatua kargatu
    private_key = serialization.load_pem_private_key(
        private_pem.encode("utf-8"),
        password=None
    )

    # JWT tokena sortu
    now = int(time.time())

    header = {
        "alg": "RS256",
        "typ": "JWT"
    }

    payload = {
        "aud": "met01.apikey",
        "iss": "eguraldia",
        "iat": now,
        "exp": now + 300,
        "version": "1.0.0",
        "loginId": fingerprint
    }

    unsigned = (
        encode_json(header)
        + "."
        + encode_json(payload)
    )

    signature = private_key.sign(
        unsigned.encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256()
    )

    token = unsigned + "." + b64url(signature)

    # Euskalmeten APIari eskaera egin
    request = urllib.request.Request(
        API_URL,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json"
        }
    )

    print("Estazioa: Alegia (C0E9)")
    print("Eskatutako eguna: 2026-09-03")

    try:
        with urllib.request.urlopen(
            request,
            timeout=30
        ) as response:

            print("HTTP:", response.status)
            print("APIaren erantzuna:")

            result = response.read().decode("utf-8")

            try:
                parsed = json.loads(result)
                print(json.dumps(
                    parsed,
                    ensure_ascii=False,
                    indent=2
                )[:6000])
            except json.JSONDecodeError:
                print(result[:2000])

    except urllib.error.HTTPError as exc:

        print("HTTP:", exc.code)
        print("APIaren errorea:")
        print(exc.read().decode("utf-8")[:2000])

        raise SystemExit(1)

    except urllib.error.URLError as exc:

        print("Konexio-errorea:", exc.reason)
        raise SystemExit(1)


if __name__ == "__main__":
    main()
