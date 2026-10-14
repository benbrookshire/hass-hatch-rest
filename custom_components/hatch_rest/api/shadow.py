"""AWS IoT device shadow access over a single MQTT-over-websocket connection.

Replaces the `thingShadow` class from aws-iot-device-sdk-js used by the
homebridge plugin. paho-mqtt runs its network loop in its own thread; every
paho callback is marshalled onto the asyncio loop, so all state below is only
touched from the loop.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import json
import logging
import ssl
from typing import Any
import uuid

import paho.mqtt.client as mqtt

from .errors import HatchConnectionError

_LOGGER = logging.getLogger(__name__)

KEEPALIVE = 300  # matches aws-iot-device-sdk-js default
OPERATION_TIMEOUT = 30

# (thing_name, is_full_document, state) where state is {"reported":..., "desired":...}
StateCallback = Callable[[str, bool, dict[str, Any]], None]


class ShadowClientClosed(HatchConnectionError):
    """The client was ended (usually because it was replaced)."""


def _topic(thing: str, suffix: str) -> str:
    return f"$aws/things/{thing}/shadow/{suffix}"


class ShadowClient:
    """One MQTT connection carrying the shadows of all of an account's devices."""

    def __init__(
        self,
        *,
        loop: asyncio.AbstractEventLoop,
        host: str,
        ws_path: str,
        ssl_context: ssl.SSLContext,
        thing_names: list[str],
        on_state: StateCallback,
    ) -> None:
        self._loop = loop
        self._host = host
        self._things = list(thing_names)
        self._on_state = on_state

        self._connected = False
        self._ever_connected = False
        self._closed = False
        self._ready_things: set[str] = set()
        self._sub_mids: dict[int, str] = {}
        self._pending: dict[str, asyncio.Future[dict[str, Any]]] = {}
        self._waiters: list[asyncio.Future[None]] = []
        self._token_seq = 0
        self._client_id = f"hatch-ha-{uuid.uuid4().hex}"

        self.on_connect: Callable[[], None] | None = None
        self.on_disconnect: Callable[[], None] | None = None
        self.on_error: Callable[[str], None] | None = None

        self._mqtt = mqtt.Client(
            mqtt.CallbackAPIVersion.VERSION2,
            client_id=self._client_id,
            transport="websockets",
            protocol=mqtt.MQTTv311,
        )
        self._mqtt.ws_set_options(path=ws_path)
        self._mqtt.tls_set_context(ssl_context)
        self._mqtt.reconnect_delay_set(min_delay=1, max_delay=128)
        self._mqtt.on_connect = self._threadsafe(self._handle_connect)
        self._mqtt.on_connect_fail = self._threadsafe(self._handle_connect_fail)
        self._mqtt.on_disconnect = self._threadsafe(self._handle_disconnect)
        self._mqtt.on_subscribe = self._threadsafe(self._handle_subscribe)
        self._mqtt.on_message = self._threadsafe(self._handle_message)

    def _threadsafe(self, handler: Callable[..., None]) -> Callable[..., None]:
        def call(*args: Any) -> None:
            if not self._loop.is_closed():
                self._loop.call_soon_threadsafe(handler, *args)

        return call

    @property
    def is_connected(self) -> bool:
        """Whether the MQTT connection is currently up."""
        return self._connected

    @property
    def closed(self) -> bool:
        """Whether end() has been called."""
        return self._closed

    def start(self) -> None:
        """Begin connecting. paho reconnects on its own with backoff."""
        self._mqtt.connect_async(self._host, 443, keepalive=KEEPALIVE)
        self._mqtt.loop_start()

    async def end(self) -> None:
        """Disconnect and stop the network thread."""
        if self._closed:
            return
        self._closed = True
        self._connected = False
        self._ready_things.clear()
        for fut in self._pending.values():
            if not fut.done():
                fut.set_exception(ShadowClientClosed("MQTT client ended"))
        self._pending.clear()
        self._notify()

        def _stop() -> None:
            try:
                self._mqtt.disconnect()
            finally:
                self._mqtt.loop_stop()

        await self._loop.run_in_executor(None, _stop)

    # -- waiting -----------------------------------------------------------

    def _notify(self) -> None:
        waiters, self._waiters = self._waiters, []
        for fut in waiters:
            if not fut.done():
                fut.set_result(None)

    async def wait_ready(self, thing: str) -> None:
        """Wait until `thing`'s shadow topics are subscribed on this connection."""
        while True:
            if self._closed:
                raise ShadowClientClosed("MQTT client ended")
            if thing in self._ready_things:
                return
            fut: asyncio.Future[None] = self._loop.create_future()
            self._waiters.append(fut)
            await fut

    # -- operations --------------------------------------------------------

    def _next_token(self) -> str:
        self._token_seq += 1
        return f"{self._client_id[-40:]}-{self._token_seq}"

    async def _operation(self, thing: str, op: str, payload: dict[str, Any]) -> dict[str, Any]:
        await self.wait_ready(thing)
        token = self._next_token()
        fut: asyncio.Future[dict[str, Any]] = self._loop.create_future()
        self._pending[token] = fut
        info = self._mqtt.publish(
            _topic(thing, op), json.dumps({**payload, "clientToken": token}), qos=0
        )
        if info.rc != mqtt.MQTT_ERR_SUCCESS:
            self._pending.pop(token, None)
            raise HatchConnectionError(f"Failed to publish {op} for {thing}: {info.rc}")
        try:
            async with asyncio.timeout(OPERATION_TIMEOUT):
                return await fut
        finally:
            self._pending.pop(token, None)

    async def update(self, thing: str, desired: dict[str, Any]) -> dict[str, Any]:
        """Publish a desired-state update and wait for it to be accepted."""
        return await self._operation(thing, "update", {"state": {"desired": desired}})

    async def get(self, thing: str) -> dict[str, Any]:
        """Fetch the full shadow document."""
        return await self._operation(thing, "get", {})

    def add_thing(self, thing: str) -> None:
        """Track an additional thing (used for one-off diagnostics fetches)."""
        if thing not in self._things:
            self._things.append(thing)
            if self._connected:
                self._subscribe(thing)

    # -- paho callbacks (run on the event loop) ----------------------------

    def _subscribe(self, thing: str) -> None:
        topics = [
            (_topic(thing, f"{op}/{status}"), 0)
            for op in ("get", "update")
            for status in ("accepted", "rejected")
        ]
        rc, mid = self._mqtt.subscribe(topics)
        if rc == mqtt.MQTT_ERR_SUCCESS and mid is not None:
            self._sub_mids[mid] = thing
        else:
            _LOGGER.error("Failed to subscribe to shadow topics for %s: %s", thing, rc)

    def _handle_connect(
        self, _client: Any, _userdata: Any, _flags: Any, reason_code: Any, _props: Any
    ) -> None:
        if self._closed:
            return
        if reason_code.is_failure:
            _LOGGER.error("MQTT connection refused: %s", reason_code)
            if self.on_error:
                self.on_error(str(reason_code))
            return

        _LOGGER.debug("MQTT connected")
        self._connected = True
        self._ever_connected = True
        # clean session: subscriptions do not survive reconnects, and the
        # shadow may have changed while we were away, so subscribe and re-fetch
        self._ready_things.clear()
        self._sub_mids.clear()
        for thing in self._things:
            self._subscribe(thing)
        if self.on_connect:
            self.on_connect()

    def _handle_connect_fail(self, _client: Any, _userdata: Any) -> None:
        if self._closed:
            return
        _LOGGER.debug("MQTT connection attempt failed")
        if not self._ever_connected and self.on_error:
            # A client that has never connected is most likely holding a bad
            # or expired signed URL (AWS answers the websocket upgrade with a
            # 403). Let the lifecycle replace it with fresh credentials.
            self.on_error("connection failed before first connect")
        elif self.on_disconnect:
            self.on_disconnect()

    def _handle_disconnect(
        self, _client: Any, _userdata: Any, _flags: Any, reason_code: Any, _props: Any
    ) -> None:
        was_connected = self._connected
        self._connected = False
        self._ready_things.clear()
        if self._closed:
            return
        _LOGGER.debug("MQTT disconnected: %s", reason_code)
        if was_connected and self.on_disconnect:
            self.on_disconnect()

    def _handle_subscribe(
        self, _client: Any, _userdata: Any, mid: int, reason_codes: list[Any], _props: Any
    ) -> None:
        thing = self._sub_mids.pop(mid, None)
        if thing is None or self._closed:
            return
        if any(rc.is_failure for rc in reason_codes):
            _LOGGER.error("AWS IoT refused shadow subscription for %s", thing)
            return
        self._ready_things.add(thing)
        self._notify()
        self._loop.create_task(self._initial_get(thing))

    async def _initial_get(self, thing: str) -> None:
        try:
            await self.get(thing)
        except (HatchConnectionError, TimeoutError) as err:
            _LOGGER.debug("Initial shadow fetch for %s failed: %r", thing, err)

    def _handle_message(self, _client: Any, _userdata: Any, msg: mqtt.MQTTMessage) -> None:
        # $aws/things/{thing}/shadow/{op}/{status}
        parts = msg.topic.split("/")
        if len(parts) != 6:
            return
        thing, op, status = parts[2], parts[4], parts[5]
        try:
            document = json.loads(msg.payload or b"{}")
        except ValueError:
            _LOGGER.warning("Ignoring malformed shadow message on %s", msg.topic)
            return

        token = document.get("clientToken")
        fut = self._pending.get(token) if token else None

        if status == "accepted":
            state = document.get("state") or {}
            self._on_state(thing, op == "get", state)
            if fut and not fut.done():
                fut.set_result(document)
        elif status == "rejected":
            _LOGGER.warning("Shadow %s rejected for %s: %s", op, thing, document)
            if fut and not fut.done():
                fut.set_exception(
                    HatchConnectionError(f"Shadow {op} rejected: {document.get('message')}")
                )
