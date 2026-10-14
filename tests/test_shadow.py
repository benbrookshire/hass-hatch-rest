"""ShadowClient protocol handling, with paho's network calls stubbed out."""

import asyncio
import json
import ssl
from types import SimpleNamespace
from unittest.mock import MagicMock

import paho.mqtt.client as mqtt
import pytest

from custom_components.hatch_rest.api.shadow import ShadowClient, ShadowClientClosed

OK = SimpleNamespace(is_failure=False)
BAD = SimpleNamespace(is_failure=True)


@pytest.fixture
async def client():
    states = []
    shadow = ShadowClient(
        loop=asyncio.get_running_loop(),
        host="example.iot.us-east-1.amazonaws.com",
        ws_path="/mqtt?x",
        ssl_context=ssl.create_default_context(),
        thing_names=["thing-1"],
        on_state=lambda *args: states.append(args),
    )
    shadow.states = states
    fake = MagicMock()
    fake.subscribe.side_effect = lambda topics: (mqtt.MQTT_ERR_SUCCESS, 7)
    fake.publish.return_value = SimpleNamespace(rc=mqtt.MQTT_ERR_SUCCESS)
    shadow._mqtt = fake
    yield shadow
    shadow._closed = True


def message(topic: str, payload: dict) -> SimpleNamespace:
    return SimpleNamespace(topic=topic, payload=json.dumps(payload).encode())


def published(shadow) -> list[tuple[str, dict]]:
    return [(c.args[0], json.loads(c.args[1])) for c in shadow._mqtt.publish.call_args_list]


async def connect(shadow) -> None:
    shadow._handle_connect(None, None, None, OK, None)
    shadow._handle_subscribe(None, None, 7, [OK, OK, OK, OK], None)
    await asyncio.sleep(0)


async def test_connect_subscribes_then_fetches_shadow(client):
    connected = []
    client.on_connect = lambda: connected.append(1)
    await connect(client)

    topics = [t for t, _ in client._mqtt.subscribe.call_args.args[0]]
    assert "$aws/things/thing-1/shadow/update/accepted" in topics
    assert "$aws/things/thing-1/shadow/get/accepted" in topics
    assert connected == [1]
    assert published(client)[0][0] == "$aws/things/thing-1/shadow/get"


async def test_get_accepted_delivers_full_state(client):
    await connect(client)
    token = published(client)[0][1]["clientToken"]
    client._handle_message(
        None,
        None,
        message(
            "$aws/things/thing-1/shadow/get/accepted",
            {"clientToken": token, "state": {"reported": {"isPowered": True}}},
        ),
    )
    assert client.states == [("thing-1", True, {"reported": {"isPowered": True}})]


async def test_foreign_update_accepted_is_partial(client):
    await connect(client)
    client._handle_message(
        None,
        None,
        message("$aws/things/thing-1/shadow/update/accepted", {"state": {"desired": {"a": 1}}}),
    )
    assert client.states == [("thing-1", False, {"desired": {"a": 1}})]


async def test_update_resolves_on_matching_token(client):
    await connect(client)
    task = asyncio.create_task(client.update("thing-1", {"isPowered": False}))
    await asyncio.sleep(0)
    topic, payload = published(client)[-1]
    assert topic == "$aws/things/thing-1/shadow/update"
    assert payload["state"] == {"desired": {"isPowered": False}}

    client._handle_message(
        None,
        None,
        message(
            "$aws/things/thing-1/shadow/update/accepted",
            {"clientToken": payload["clientToken"], "state": {"desired": {"isPowered": False}}},
        ),
    )
    assert (await task)["clientToken"] == payload["clientToken"]


async def test_update_waits_for_subscription(client):
    task = asyncio.create_task(client.update("thing-1", {"x": 1}))
    await asyncio.sleep(0)
    assert published(client) == []
    await connect(client)
    await asyncio.sleep(0)
    # the queued update goes out alongside the initial get
    assert any(p.get("state") == {"desired": {"x": 1}} for _, p in published(client))
    task.cancel()


async def test_disconnect_requires_resubscribe(client):
    disconnected = []
    client.on_disconnect = lambda: disconnected.append(1)
    await connect(client)
    client._handle_disconnect(None, None, None, OK, None)
    assert disconnected == [1]
    assert "thing-1" not in client._ready_things


async def test_end_fails_waiters(client):
    waiter = asyncio.create_task(client.wait_ready("thing-1"))
    await asyncio.sleep(0)
    await client.end()
    with pytest.raises(ShadowClientClosed):
        await waiter


async def test_refused_connection_and_first_connect_failure_report_errors(client):
    errors = []
    client.on_error = errors.append
    client._handle_connect(None, None, None, BAD, None)
    client._handle_connect_fail(None, None)
    assert len(errors) == 2
