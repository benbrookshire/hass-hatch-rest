"""HTTP client for the Hatch Sleep REST API and AWS Cognito."""

from __future__ import annotations

import asyncio
import logging
from typing import Any
from urllib.parse import quote

import aiohttp

from .const import API_BASE_URL
from .errors import HatchAuthError, HatchConnectionError

_LOGGER = logging.getLogger(__name__)

REQUEST_TIMEOUT = aiohttp.ClientTimeout(total=30)
DEFAULT_HEADERS = {
    # Matches the header the Hatch app/homebridge plugin send (yes, literally
    # a header named USER_AGENT)
    "USER_AGENT": "hatch_rest_api",
    "content-type": "application/json",
}


def api_path(path: str) -> str:
    """Return the absolute URL for a Hatch API path."""
    return API_BASE_URL + path


class HatchRestClient:
    """Authenticated client for the Hatch Sleep REST API.

    Unlike the homebridge plugin, requests are not retried forever: Home
    Assistant owns retries (ConfigEntryNotReady at setup, and the IoT client
    lifecycle's backoff at runtime), so failures are raised promptly.
    """

    def __init__(self, session: aiohttp.ClientSession, email: str, password: str) -> None:
        self._session = session
        self._email = email
        self._password = password
        self._token: str | None = None
        self._member: dict[str, Any] | None = None
        self._login_lock = asyncio.Lock()

    @property
    def member(self) -> dict[str, Any] | None:
        """The member payload from the most recent login."""
        return self._member

    async def _raw_request(
        self,
        method: str,
        url: str,
        *,
        json: Any = None,
        headers: dict[str, str] | None = None,
    ) -> tuple[int, Any]:
        try:
            async with self._session.request(
                method,
                url,
                json=json,
                headers={**DEFAULT_HEADERS, **(headers or {})},
                timeout=REQUEST_TIMEOUT,
            ) as resp:
                if resp.status >= 400:
                    body = await resp.text()
                    return resp.status, body
                # Cognito answers with application/x-amz-json-1.1
                return resp.status, await resp.json(content_type=None)
        except (aiohttp.ClientError, TimeoutError) as err:
            raise HatchConnectionError(f"Failed to reach {url}: {err!r}") from err

    async def login(self) -> dict[str, Any]:
        """Log in and return the member payload."""
        async with self._login_lock:
            status, body = await self._raw_request(
                "POST",
                api_path("public/v1/login"),
                json={"email": self._email, "password": self._password},
            )
            if status in (400, 401, 403):
                raise HatchAuthError("Invalid Hatch email or password")
            if status >= 400:
                raise HatchConnectionError(f"Login failed with status {status}: {body}")
            if not isinstance(body, dict):
                raise HatchConnectionError("Unexpected login response")
            if body.get("status") == "failure":
                raise HatchAuthError(body.get("message") or "Invalid Hatch email or password")

            self._token = body["token"]
            self._member = body.get("payload") or {}
            return self._member

    async def request(self, path: str, method: str = "GET", json: Any = None) -> Any:
        """Make an authenticated request and return the response `payload`."""
        if self._token is None:
            await self.login()

        url = api_path(path)
        for attempt in range(2):
            status, body = await self._raw_request(
                method, url, json=json, headers={"X-HatchBaby-Auth": self._token or ""}
            )
            if status == 401 and attempt == 0:
                _LOGGER.debug("Hatch auth token expired, logging in again")
                await self.login()
                continue
            if status == 401:
                raise HatchAuthError("Hatch rejected credentials")
            if status >= 400:
                raise HatchConnectionError(f"Request to {url} failed with status {status}: {body}")
            return body.get("payload") if isinstance(body, dict) else None

        raise HatchConnectionError(f"Request to {url} failed")  # pragma: no cover

    async def get_member(self) -> dict[str, Any]:
        """Fetch the member, including the list of owned products."""
        return await self.request("service/app/v2/member")

    async def get_iot_devices(self, products: list[str] | tuple[str, ...]) -> list[dict[str, Any]]:
        """Fetch IoT device info for the given products."""
        if not products:
            return []
        query = "&".join(f"iotProducts={quote(p)}" for p in products)
        return await self.request(f"service/app/iotDevice/v2/fetch?{query}") or []

    async def get_routines(self, mac_address: str) -> list[dict[str, Any]]:
        """Fetch routines configured for a Rest IoT device."""
        return (
            await self.request(
                f"service/app/routine/v2/fetch?macAddress={quote(mac_address, safe='')}"
            )
            or []
        )

    async def get_iot_token(self) -> dict[str, Any]:
        """Fetch the Cognito identity token used to access AWS IoT."""
        return await self.request("service/app/restPlus/token/v1/fetch")

    async def get_aws_credentials(self, token: dict[str, Any]) -> dict[str, Any]:
        """Exchange a Cognito identity token for temporary AWS credentials."""
        status, body = await self._raw_request(
            "POST",
            f"https://cognito-identity.{token['region']}.amazonaws.com",
            json={
                "IdentityId": token["identityId"],
                "Logins": {"cognito-identity.amazonaws.com": token["token"]},
            },
            headers={
                "content-type": "application/x-amz-json-1.1",
                "X-Amz-Target": "AWSCognitoIdentityService.GetCredentialsForIdentity",
            },
        )
        if status >= 400:
            raise HatchConnectionError(f"Cognito credential exchange failed ({status}): {body}")
        return body["Credentials"]
