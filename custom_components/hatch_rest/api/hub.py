"""Entry point tying together the REST API, AWS IoT client and devices."""

from __future__ import annotations

import asyncio
import logging
import ssl
from typing import Any

import aiohttp

from .const import IGNORED_PRODUCTS, KNOWN_PRODUCTS
from .devices import DEVICE_CLASSES, HatchDevice
from .errors import HatchConnectionError
from .lifecycle import IotClientLifecycle
from .rest import HatchRestClient
from .shadow import ShadowClient, ShadowClientClosed
from .sigv4 import presign_websocket_path

_LOGGER = logging.getLogger(__name__)

# How long a command waits for a connected MQTT client before failing
READY_TIMEOUT = 60


class HatchHub:
    """A Hatch account: its devices and the shared AWS IoT connection."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        email: str,
        password: str,
        ssl_context: ssl.SSLContext,
    ) -> None:
        self.rest = HatchRestClient(session, email, password)
        self._ssl_context = ssl_context
        self.devices: list[HatchDevice] = []
        self.member: dict[str, Any] = {}
        self.unsupported_products: list[str] = []
        self._by_thing: dict[str, HatchDevice] = {}
        self._stopped = False
        self._lifecycle: IotClientLifecycle[ShadowClient] = IotClientLifecycle(self._create_client)

    async def async_start(self) -> None:
        """Log in, discover devices and connect to AWS IoT.

        Raises HatchAuthError / HatchConnectionError so the caller can map
        them onto ConfigEntryAuthFailed / ConfigEntryNotReady.
        """
        await self.rest.login()
        member_response = await self.rest.get_member()
        self.member = member_response.get("member") or {}
        products: list[str] = member_response.get("products") or []

        self.unsupported_products = [
            p for p in products if p not in KNOWN_PRODUCTS and p not in IGNORED_PRODUCTS
        ]
        for product in self.unsupported_products:
            _LOGGER.info(
                "Unsupported Hatch product found: %s. Download diagnostics for this "
                "integration and open an issue so support can be added",
                product,
            )

        infos = await self.rest.get_iot_devices(KNOWN_PRODUCTS)
        for info in infos:
            device_class = DEVICE_CLASSES.get(info.get("product"))
            if device_class is None:
                continue
            device = device_class(info, self)
            self.devices.append(device)
            self._by_thing[device.thing_name] = device

        if self.devices:
            await self._lifecycle.start()

    async def async_stop(self) -> None:
        """Disconnect from AWS IoT."""
        self._stopped = True
        await self._lifecycle.stop()

    async def _create_client(self) -> ShadowClient:
        token = await self.rest.get_iot_token()
        credentials = await self.rest.get_aws_credentials(token)
        host = token["endpoint"].removeprefix("https://")
        ws_path = presign_websocket_path(
            host,
            token["region"],
            credentials["AccessKeyId"],
            credentials["SecretKey"],
            credentials.get("SessionToken"),
        )
        return ShadowClient(
            loop=asyncio.get_running_loop(),
            host=host,
            ws_path=ws_path,
            ssl_context=self._ssl_context,
            thing_names=list(self._by_thing),
            on_state=self._handle_state,
        )

    def _handle_state(self, thing: str, full: bool, state: dict[str, Any]) -> None:
        if device := self._by_thing.get(thing):
            device.handle_shadow(full, state)

    async def _ready_client(self, thing: str) -> ShadowClient:
        """Wait for a connected client with `thing` subscribed.

        A client can be replaced (credential rotation, dead connection) while
        we wait; its wait raises ShadowClientClosed and we move on to the
        replacement rather than dropping the command.
        """
        async with asyncio.timeout(READY_TIMEOUT):
            while True:
                if self._stopped:
                    raise HatchConnectionError("Hatch integration is stopping")
                client = self._lifecycle.current
                if client is None or client.closed:
                    await self._lifecycle.wait_for_change()
                    continue
                try:
                    await client.wait_ready(thing)
                except ShadowClientClosed:
                    continue
                return client

    async def async_shadow_update(self, thing: str, desired: dict[str, Any]) -> None:
        """Publish a desired-state update for `thing`."""
        try:
            client = await self._ready_client(thing)
        except TimeoutError as err:
            raise HatchConnectionError("Not connected to Hatch cloud") from err

        try:
            await client.update(thing, desired)
        except TimeoutError:
            # The device may still apply it; the homebridge plugin also just
            # stops waiting after 30 seconds
            _LOGGER.warning("Timed out waiting for Hatch to accept update for %s", thing)

    async def async_debug_info(self) -> list[dict[str, Any]]:
        """Info and shadow state for unsupported devices, for diagnostics."""
        if not self.unsupported_products:
            return []
        infos = await self.rest.get_iot_devices(self.unsupported_products)
        results: list[dict[str, Any]] = []
        client = self._lifecycle.current
        for info in infos:
            entry: dict[str, Any] = {"info": info, "state": None}
            if client is not None and not client.closed and info.get("thingName"):
                try:
                    client.add_thing(info["thingName"])
                    async with asyncio.timeout(15):
                        await client.wait_ready(info["thingName"])
                    document = await client.get(info["thingName"])
                    entry["state"] = document.get("state")
                except (HatchConnectionError, TimeoutError) as err:
                    entry["state_error"] = repr(err)
            results.append(entry)
        return results
