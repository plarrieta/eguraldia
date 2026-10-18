
#!/usr/bin/env python3
import base64
import json
import os
import time
import urllib.error
import urllib.request

from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding


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
        raise RuntimeError(
            "Euskalmeten gako pribatua edo fingerprint-a falta da."
        )

    private_key = serialization.load_pem_private_key(
        private_pem.encode("utf-8"),
        password=None
    )

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

    def encode(obj):
        raw = json.dumps(
            obj,
            separators=(",", ":")
        ).encode("utf-8")
        return b64url(raw)

    unsigned = encode(header) + "." + encode(payload)

    signature = private_key.sign(
        unsigned.encode("ascii"),
        padding.PKCS1v15(),
        hashes.SHA256()
    )

    token = unsigned + "." + b64url(signature)

    url = "https://api.euskadi.eus/euskalmet/stations/C0E9"

    request = urllib.request.Request(
        url,
        headers={
            "Authorization": "Bearer " + token,
            "Accept": "application/json"
        }
    )

    try:
        with urllib.request.urlopen(
            request, timeout=30
        ) as response:
            print("HTTP:", response.status)
            print("Alegiako estazioaren erantzuna:")
            print(
                response.read().decode("utf-8")[:4000]
            )

    except urllib.error.HTTPError as exc:
        print("HTTP:", exc.code)
        print("APIaren errore-kodea:")
        print(exc.read().decode("utf-8")[:1000])
        raise SystemExit(1)


if __name__ == "__main__":
    main()
