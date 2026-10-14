"""SigV4 presigning for AWS IoT MQTT-over-websocket connections.

Port of `prepareWebSocketUrl` from aws-iot-device-sdk-js, which the homebridge
plugin relies on. The session token is appended after signing, unsigned, as
AWS IoT expects.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import hmac
from urllib.parse import quote

SERVICE = "iotdevicegateway"
ALGORITHM = "AWS4-HMAC-SHA256"
WS_PATH = "/mqtt"


def _hmac(key: bytes, msg: str) -> bytes:
    return hmac.new(key, msg.encode(), hashlib.sha256).digest()


def signing_key(secret_key: str, date_stamp: str, region: str, service: str) -> bytes:
    """Derive the SigV4 signing key."""
    k_date = _hmac(("AWS4" + secret_key).encode(), date_stamp)
    k_region = _hmac(k_date, region)
    k_service = _hmac(k_region, service)
    return _hmac(k_service, "aws4_request")


def presign_websocket_path(
    host: str,
    region: str,
    access_key: str,
    secret_key: str,
    session_token: str | None,
    now: datetime | None = None,
) -> str:
    """Return the signed `/mqtt?...` request path for an AWS IoT websocket."""
    now = now or datetime.now(UTC)
    amz_date = now.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = amz_date[:8]
    scope = f"{date_stamp}/{region}/{SERVICE}/aws4_request"

    query = (
        f"X-Amz-Algorithm={ALGORITHM}"
        f"&X-Amz-Credential={access_key}%2F{scope.replace('/', '%2F')}"
        f"&X-Amz-Date={amz_date}"
        "&X-Amz-SignedHeaders=host"
    )
    canonical_request = "\n".join(
        [
            "GET",
            WS_PATH,
            query,
            f"host:{host.lower()}\n",
            "host",
            hashlib.sha256(b"").hexdigest(),
        ]
    )
    string_to_sign = "\n".join(
        [
            ALGORITHM,
            amz_date,
            scope,
            hashlib.sha256(canonical_request.encode()).hexdigest(),
        ]
    )
    signature = hmac.new(
        signing_key(secret_key, date_stamp, region, SERVICE),
        string_to_sign.encode(),
        hashlib.sha256,
    ).hexdigest()

    query += f"&X-Amz-Signature={signature}"
    if session_token:
        # encodeURIComponent equivalent
        query += "&X-Amz-Security-Token=" + quote(session_token, safe="-_.!~*'()")
    return f"{WS_PATH}?{query}"
