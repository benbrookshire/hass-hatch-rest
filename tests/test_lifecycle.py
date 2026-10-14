"""IotClientLifecycle state machine, driven by fake clients and short timers."""

import asyncio

import pytest

from custom_components.hatch_rest.api.lifecycle import IotClientLifecycle

T = 0.02  # base time unit for timers in these tests


class FakeClient:
    instances: list["FakeClient"] = []

    def __init__(self) -> None:
        self.on_connect = None
        self.on_disconnect = None
        self.on_error = None
        self.started = False
        self.ended = False
        FakeClient.instances.append(self)

    def start(self) -> None:
        self.started = True

    async def end(self) -> None:
        self.ended = True


class Factory:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = 0  # number of upcoming calls that should fail
        self.gate: asyncio.Event | None = None

    async def __call__(self) -> FakeClient:
        self.calls += 1
        if self.gate:
            await self.gate.wait()
        if self.fail:
            self.fail -= 1
            raise ConnectionError("offline")
        return FakeClient()


def lifecycle(factory, **overrides) -> IotClientLifecycle:
    options = {
        "initial_retry_delay": T,
        "max_retry_delay": 4 * T,
        "dead_connection_timeout": 3 * T,
        "refresh_period": 1000,
    } | overrides
    return IotClientLifecycle(factory, **options)


async def settle(units: float = 1) -> None:
    await asyncio.sleep(units * T)


@pytest.fixture(autouse=True)
def _reset():
    FakeClient.instances = []


async def test_start_creates_and_starts_client():
    factory = Factory()
    lc = lifecycle(factory)
    client = await lc.start()
    assert client.started and lc.current is client
    await lc.stop()
    assert client.ended


async def test_start_raises_when_first_creation_fails():
    factory = Factory()
    factory.fail = 1
    with pytest.raises(ConnectionError):
        await lifecycle(factory).start()


async def test_dead_connection_recreates_and_ends_previous():
    factory = Factory()
    lc = lifecycle(factory)
    seen = []
    lc.add_listener(seen.append)
    first = await lc.start()

    first.on_disconnect()
    await settle(5)

    assert factory.calls == 2
    assert lc.current is not first
    assert seen == [first, lc.current]
    assert first.ended
    await lc.stop()


async def test_reconnect_before_timeout_cancels_recreate():
    factory = Factory()
    lc = lifecycle(factory)
    first = await lc.start()
    first.on_disconnect()
    await settle(1)
    first.on_connect()
    await settle(5)
    assert factory.calls == 1 and lc.current is first
    await lc.stop()


async def test_events_from_replaced_client_are_ignored():
    factory = Factory()
    lc = lifecycle(factory)
    first = await lc.start()
    first.on_disconnect()
    await settle(5)
    assert factory.calls == 2

    first.on_disconnect()
    first.on_error("late")
    await settle(6)
    assert factory.calls == 2
    await lc.stop()


async def test_failed_recreate_keeps_old_client_and_retries_with_backoff():
    factory = Factory()
    lc = lifecycle(factory)
    first = await lc.start()

    factory.fail = 2
    first.on_disconnect()
    # dead timer (3T) -> fail -> retry after T -> fail -> retry after 2T -> ok
    await settle(4)
    assert factory.calls == 2 and lc.current is first and not first.ended
    await settle(8)
    assert factory.calls == 4
    assert lc.current is not first and first.ended
    await lc.stop()


async def test_error_waits_floor_delay_before_recreating():
    factory = Factory()
    lc = lifecycle(factory, initial_retry_delay=3 * T)
    first = await lc.start()
    first.on_error("403")
    first.on_error("403 again")  # coalesced while a recreate is pending
    await settle(1)
    assert factory.calls == 1
    await settle(4)
    assert factory.calls == 2
    await lc.stop()


async def test_refresh_period_rotates_client():
    factory = Factory()
    lc = lifecycle(factory, refresh_period=2 * T)
    first = await lc.start()
    await settle(3)
    assert factory.calls >= 2 and first.ended
    await lc.stop()


async def test_stop_during_inflight_create_ends_the_new_client():
    factory = Factory()
    lc = lifecycle(factory)
    first = await lc.start()

    factory.gate = asyncio.Event()
    first.on_disconnect()
    await settle(4)  # dead timer fired, create is blocked on the gate
    assert factory.calls == 2

    stop = asyncio.create_task(lc.stop())
    factory.gate.set()
    await stop
    await settle(1)
    assert first.ended
    # the create task was cancelled, so no new client was published
    assert lc.current is first
