"""Lifecycle of the AWS IoT MQTT client.

Port of `IotClientLifecycle` from the homebridge plugin: create the client,
retry failed creation forever with exponential backoff, replace the client
with fresh credentials when it has been disconnected too long for paho's own
reconnect to be trusted (its retries reuse the original signed URL, whose
Cognito credentials expire), and proactively rotate credentials on an
interval.

The client is abstracted behind a small protocol so this state machine can be
unit tested with fake clients and without a network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
import logging
from typing import Protocol

_LOGGER = logging.getLogger(__name__)


class LifecycleClient(Protocol):
    """What the lifecycle needs from a client."""

    on_connect: Callable[[], None] | None
    on_disconnect: Callable[[], None] | None
    on_error: Callable[[str], None] | None

    def start(self) -> None: ...

    async def end(self) -> None: ...


class IotClientLifecycle[C: LifecycleClient]:
    """Owns creation, recovery and credential rotation of a single client."""

    def __init__(
        self,
        create_client: Callable[[], Awaitable[C]],
        *,
        initial_retry_delay: float = 30,
        max_retry_delay: float = 5 * 60,
        dead_connection_timeout: float = 10 * 60,
        refresh_period: float = 50 * 60,  # Cognito credentials expire after ~1 hour
    ) -> None:
        self._create_client = create_client
        self._initial_retry_delay = initial_retry_delay
        self._max_retry_delay = max_retry_delay
        self._dead_connection_timeout = dead_connection_timeout
        self._refresh_period = refresh_period

        self._current: C | None = None
        self._listeners: list[Callable[[C], None]] = []
        self._recreating = False
        self._disposed = False
        self._retry_delay = initial_retry_delay
        self._retry_handle: asyncio.TimerHandle | None = None
        self._dead_handle: asyncio.TimerHandle | None = None
        self._refresh_handle: asyncio.TimerHandle | None = None
        self._tasks: set[asyncio.Task[None]] = set()
        self._changed: asyncio.Event = asyncio.Event()

    @property
    def current(self) -> C | None:
        """The current client, once one has been created."""
        return self._current

    def add_listener(self, listener: Callable[[C], None]) -> Callable[[], None]:
        """Call `listener` whenever a new client replaces the current one."""
        self._listeners.append(listener)
        return lambda: self._listeners.remove(listener)

    async def wait_for_change(self) -> None:
        """Wait until a new client is published (or the lifecycle stops)."""
        await self._changed.wait()

    async def start(self) -> C:
        """Create the first client and begin credential rotation.

        Unlike the homebridge plugin, a failure to create the *first* client
        is raised so Home Assistant can retry setup (ConfigEntryNotReady).
        Once running, failures are retried forever.
        """
        client = await self._create_and_attach()
        self._publish(client)
        self._schedule_refresh()
        return client

    async def stop(self) -> None:
        """Abandon the lifecycle: cancel timers and tasks, end the client."""
        self._disposed = True
        for handle in (self._retry_handle, self._dead_handle, self._refresh_handle):
            if handle:
                handle.cancel()
        self._retry_handle = self._dead_handle = self._refresh_handle = None
        for task in list(self._tasks):
            task.cancel()
        self._changed.set()
        if self._current is not None:
            await self._end_client(self._current)

    # -- internals ---------------------------------------------------------

    def _spawn_recreate(self, *, floor_delay: bool = False) -> None:
        if self._disposed or self._recreating:
            return
        task = asyncio.get_running_loop().create_task(self._recreate(floor_delay=floor_delay))
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    def _schedule_refresh(self) -> None:
        if self._disposed:
            return

        def refresh() -> None:
            self._refresh_handle = None
            _LOGGER.debug("Rotating MQTT credentials")
            self._spawn_recreate()
            # A plain interval, so one failed refresh cannot end the cycle
            self._schedule_refresh()

        self._refresh_handle = asyncio.get_running_loop().call_later(self._refresh_period, refresh)

    def _is_current(self, client: C) -> bool:
        # Before the first publish, the client being created is the current one
        return self._current is None or self._current is client

    def _clear_dead_timer(self) -> None:
        if self._dead_handle:
            self._dead_handle.cancel()
            self._dead_handle = None

    def _arm_dead_timer(self, client: C) -> None:
        if self._disposed or not self._is_current(client) or self._dead_handle:
            return

        def expired() -> None:
            self._dead_handle = None
            _LOGGER.warning(
                "MQTT connection still down after %ds, recreating with fresh credentials",
                round(self._dead_connection_timeout),
            )
            self._spawn_recreate()

        self._dead_handle = asyncio.get_running_loop().call_later(
            self._dead_connection_timeout, expired
        )

    def _attach(self, client: C) -> None:
        def on_connect() -> None:
            if self._disposed or not self._is_current(client):
                return
            self._clear_dead_timer()
            self._retry_delay = self._initial_retry_delay

        def on_error(reason: str) -> None:
            _LOGGER.warning("MQTT error: %s", reason)
            if not self._disposed and self._is_current(client):
                # floor delay so a persistent 403 cannot tight-loop token fetches
                self._spawn_recreate(floor_delay=True)

        client.on_connect = on_connect
        client.on_disconnect = lambda: self._arm_dead_timer(client)
        client.on_error = on_error

    async def _end_client(self, client: C) -> None:
        try:
            await client.end()
        except Exception:
            _LOGGER.exception("Failed to end previous MQTT client")

    async def _create_and_attach(self) -> C:
        _LOGGER.debug("Creating new MQTT client")
        client = await self._create_client()
        self._attach(client)
        client.start()
        return client

    def _publish(self, client: C) -> None:
        self._current = client
        self._changed.set()
        self._changed = asyncio.Event()
        for listener in list(self._listeners):
            listener(client)

    async def _recreate(self, *, floor_delay: bool = False) -> None:
        if self._disposed or self._recreating:
            return
        self._recreating = True
        if self._retry_handle:
            self._retry_handle.cancel()
            self._retry_handle = None
        self._clear_dead_timer()

        try:
            if floor_delay:
                await asyncio.sleep(self._initial_retry_delay)
                if self._disposed:
                    return

            try:
                # Create the replacement BEFORE ending the previous client: if
                # creation fails mid-outage, the old client keeps paho's own
                # reconnect loop alive instead of leaving no client at all.
                client = await self._create_and_attach()
            except Exception as err:
                if self._disposed:
                    return
                _LOGGER.warning(
                    "Failed to create MQTT client (%r), retrying in %ds",
                    err,
                    round(self._retry_delay),
                )
                self._retry_handle = asyncio.get_running_loop().call_later(
                    self._retry_delay, self._spawn_recreate
                )
                self._retry_delay = min(self._retry_delay * 2, self._max_retry_delay)
                return

            if self._disposed:
                await self._end_client(client)
                return

            previous = self._current
            self._retry_delay = self._initial_retry_delay
            self._publish(client)
            if previous is not None:
                await self._end_client(previous)
            _LOGGER.debug("Created new MQTT client")
        finally:
            self._recreating = False
