"""Exercise retry, reconnect, and unload with a controlled WebSocket."""

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import aiohttp
import pytest
from homeassistant.config_entries import SOURCE_REAUTH
from homeassistant.const import EVENT_HOMEASSISTANT_STOP
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.remote_homeassistant import RemoteConnection, STATE_AUTH_INVALID
from custom_components.remote_homeassistant.rest_api import InvalidAuth


UUID = "1234567890abcdef1234567890abcdef"


def make_remote(hass, options=None):
    entry = MockConfigEntry(
        domain="remote_homeassistant",
        unique_id=UUID,
        data={"host": "remote.invalid", "port": 8123, "access_token": "token"},
        options=options or {},
    )
    entry.add_to_hass(hass)
    remote = RemoteConnection(hass, entry)
    remote.proxy_services.unload = AsyncMock()
    return remote


class Socket:
    def __init__(self):
        self.closed = False
        self.messages = asyncio.Queue()

    async def receive(self):
        return await self.messages.get()

    async def send_json(self, message):
        pass

    async def close(self):
        if not self.closed:
            self.closed = True
            self.messages.put_nowait(SimpleNamespace(type=aiohttp.WSMsgType.CLOSED))


@pytest.mark.asyncio
async def test_stop_cancels_offline_retry(hass, monkeypatch):
    """An offline connection cannot retry after its config entry is unloaded."""
    remote = make_remote(hass)
    lookup_started = asyncio.Event()
    lookup_count = 0

    async def get_info(*args):
        nonlocal lookup_count
        lookup_count += 1
        lookup_started.set()
        return None

    monkeypatch.setattr("custom_components.remote_homeassistant.async_get_discovery_info", get_info)
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_clientsession",
        lambda *args: SimpleNamespace(ws_connect=AsyncMock()),
    )
    connect = asyncio.create_task(remote.async_connect())
    await lookup_started.wait()
    await remote.async_stop()

    assert connect.cancelled()
    assert remote._connect_task is None
    assert lookup_count == 1
    assert remote._connection is None


@pytest.mark.asyncio
async def test_stop_closes_socket_and_tasks(hass, monkeypatch, device_registry):
    """Unload waits for the receiver and heartbeat and removes the stop listener."""
    remote = make_remote(hass)
    socket = Socket()
    get_info = AsyncMock(return_value={"uuid": UUID, "location_name": "Remote"})
    monkeypatch.setattr("custom_components.remote_homeassistant.async_get_discovery_info", get_info)
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_clientsession",
        lambda *args: SimpleNamespace(ws_connect=AsyncMock(return_value=socket)),
    )

    await remote.async_connect()
    receive_task = remote._receive_task
    heartbeat_task = remote._heartbeat_task
    assert remote._stop_listener is not None

    # HA's bootstrap wait must finish while the persistent connection is active.
    await asyncio.wait_for(hass.async_block_till_done(), 1)
    assert not receive_task.done()
    assert not heartbeat_task.done()

    await remote.async_stop()

    assert socket.closed
    assert receive_task.done()
    assert heartbeat_task.done()
    assert remote._stop_listener is None
    assert remote._connection is None
    get_info.assert_awaited_once()


@pytest.mark.asyncio
async def test_home_assistant_stop_removes_listener_once(
    hass, monkeypatch, device_registry, caplog
):
    """A stop event closes the socket and unregisters its listener once."""
    remote = make_remote(hass)
    socket = Socket()
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_discovery_info",
        AsyncMock(return_value={"uuid": UUID, "location_name": "Remote"}),
    )
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_clientsession",
        lambda *args: SimpleNamespace(ws_connect=AsyncMock(return_value=socket)),
    )

    await remote.async_connect()
    with caplog.at_level(logging.ERROR, logger="homeassistant.core"):
        hass.bus.async_fire(EVENT_HOMEASSISTANT_STOP)
        await asyncio.wait_for(hass.async_block_till_done(), 1)

    assert socket.closed
    assert remote._stop_listener is None
    assert not any("Unable to remove unknown job listener" in r.message for r in caplog.records)


@pytest.mark.asyncio
async def test_disconnect_retries_once_and_stop_cancels_it(
    hass, monkeypatch, device_registry
):
    """A dropped WebSocket schedules one retry which unload can cancel."""
    remote = make_remote(hass)
    socket = Socket()
    retry_started = asyncio.Event()
    attempts = 0

    async def get_info(*args):
        nonlocal attempts
        attempts += 1
        if attempts == 2:
            retry_started.set()
            await asyncio.Event().wait()
        return {"uuid": UUID, "location_name": "Remote"}

    monkeypatch.setattr("custom_components.remote_homeassistant.async_get_discovery_info", get_info)
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_clientsession",
        lambda *args: SimpleNamespace(ws_connect=AsyncMock(return_value=socket)),
    )

    await remote.async_connect()
    remote._handlers[42] = lambda message: None
    await socket.close()
    await retry_started.wait()

    assert attempts == 2
    assert remote._connection is None
    assert remote._handlers == {}
    assert remote._handler_timeouts == {}
    assert remote._stop_listener is None
    await remote._disconnected(socket)
    assert attempts == 2

    # An offline reconnect can wait indefinitely without delaying HA startup.
    await asyncio.wait_for(hass.async_block_till_done(), 1)

    retry_task = remote._connect_task
    assert not retry_task.done()
    await remote.async_stop()
    assert retry_task.cancelled()
    assert attempts == 2


@pytest.mark.asyncio
async def test_response_handlers_expire_but_subscriptions_stay(
    hass, monkeypatch, device_registry
):
    """Only subscriptions retain their callback after a successful response."""
    remote = make_remote(hass)
    socket = Socket()
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_discovery_info",
        AsyncMock(return_value={"uuid": UUID, "location_name": "Remote"}),
    )
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_clientsession",
        lambda *args: SimpleNamespace(ws_connect=AsyncMock(return_value=socket)),
    )
    await remote.async_connect()
    response_received = asyncio.Event()
    subscription_events = []
    event_received = asyncio.Event()

    def subscribed(message):
        subscription_events.append(message)
        if len(subscription_events) == 2:
            event_received.set()

    await remote.call(lambda message: response_received.set(), "get_states")
    await remote.call(subscribed, "subscribe_events", event_type="state_changed")
    one_shot_id, subscription_id = remote._handlers

    def deliver(message):
        socket.messages.put_nowait(SimpleNamespace(
            type=aiohttp.WSMsgType.TEXT, json=lambda: message
        ))

    deliver({"type": "result", "id": one_shot_id, "success": True, "result": []})
    deliver({"type": "result", "id": subscription_id, "success": True})
    deliver({"type": "event", "id": subscription_id, "event": {}})
    await asyncio.wait_for(response_received.wait(), 1)
    await asyncio.wait_for(event_received.wait(), 1)

    assert one_shot_id not in remote._handlers
    assert subscription_id in remote._handlers
    assert remote._handler_timeouts == {}
    await remote.async_stop()
    assert remote._handlers == {}


@pytest.mark.asyncio
async def test_unanswered_requests_expire_and_failed_subscriptions_are_removed(
    hass, monkeypatch, device_registry
):
    """Silent servers and rejected subscriptions cannot retain callbacks."""
    remote = make_remote(hass)
    socket = Socket()
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_discovery_info",
        AsyncMock(return_value={"uuid": UUID, "location_name": "Remote"}),
    )
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_clientsession",
        lambda *args: SimpleNamespace(ws_connect=AsyncMock(return_value=socket)),
    )
    monkeypatch.setattr("custom_components.remote_homeassistant.RESPONSE_TIMEOUT", 0.01)
    await remote.async_connect()
    received = []
    failure_received = asyncio.Event()

    def on_subscription_response(message):
        received.append(message)
        failure_received.set()

    request_id = await remote.call(received.append, "get_states")
    expired_subscription = await remote.call(
        received.append, "subscribe_events", event_type="state_changed"
    )
    await asyncio.sleep(0.03)
    assert remote._handlers == {}
    assert remote._handler_timeouts == {}
    assert remote._subscription_ids == set()

    socket.messages.put_nowait(SimpleNamespace(
        type=aiohttp.WSMsgType.TEXT,
        json=lambda: {"type": "result", "id": request_id, "success": True},
    ))
    socket.messages.put_nowait(SimpleNamespace(
        type=aiohttp.WSMsgType.TEXT,
        json=lambda: {"type": "result", "id": expired_subscription, "success": True},
    ))
    failed_subscription = await remote.call(
        on_subscription_response, "subscribe_events", event_type="state_changed"
    )
    socket.messages.put_nowait(SimpleNamespace(
        type=aiohttp.WSMsgType.TEXT,
        json=lambda: {"type": "result", "id": failed_subscription, "success": False},
    ))
    await asyncio.wait_for(failure_received.wait(), 1)
    assert len(received) == 1
    assert received[0]["success"] is False
    assert failed_subscription not in remote._handlers
    assert remote._handler_timeouts == {}
    assert remote._subscription_ids == set()
    await remote.async_stop()


@pytest.mark.asyncio
async def test_proxy_service_timeout_removes_response_handler(hass, monkeypatch):
    """A timed-out service call releases its callback immediately."""
    remote = make_remote(hass, {"service_prefix": "remote_"})
    remote._connection = Socket()
    monkeypatch.setattr("custom_components.remote_homeassistant.proxy_services.SERVICE_CALL_LIMIT", 0.01)
    event = SimpleNamespace(domain="light", service="remote_turn_on", data={})

    with pytest.raises(asyncio.TimeoutError):
        await remote.proxy_services._async_handle_service_call(event)

    assert remote._handlers == {}
    assert remote._handler_timeouts == {}
    await remote.async_stop()


@pytest.mark.asyncio
async def test_invalid_auth_keeps_error_state_without_retry(
    hass, monkeypatch, device_registry, enable_custom_integrations
):
    """A rejected token remains visible and cannot create a reconnect loop."""
    remote = make_remote(hass)
    socket = Socket()
    states = []
    remote.set_connection_state = states.append
    get_info = AsyncMock(return_value={"uuid": UUID, "location_name": "Remote"})
    monkeypatch.setattr("custom_components.remote_homeassistant.async_get_discovery_info", get_info)
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_clientsession",
        lambda *args: SimpleNamespace(ws_connect=AsyncMock(return_value=socket)),
    )
    await remote.async_connect()
    receiver = remote._receive_task
    socket.messages.put_nowait(SimpleNamespace(
        type=aiohttp.WSMsgType.TEXT,
        json=lambda: {"type": "auth_invalid"},
    ))
    await receiver
    await hass.async_block_till_done()

    assert states[-1] == STATE_AUTH_INVALID
    assert remote._connection is None
    assert remote._connect_task is None
    get_info.assert_awaited_once()
    assert len(list(remote._entry.async_get_active_flows(hass, {SOURCE_REAUTH}))) == 1
    await remote.async_stop()


@pytest.mark.asyncio
async def test_rest_auth_failure_starts_one_reauth_flow(
    hass, monkeypatch, enable_custom_integrations
):
    """A revoked token detected before WebSocket login opens the same UI flow."""
    remote = make_remote(hass)
    states = []
    remote.set_connection_state = states.append
    get_info = AsyncMock(side_effect=InvalidAuth())
    session = SimpleNamespace(ws_connect=AsyncMock())
    monkeypatch.setattr("custom_components.remote_homeassistant.async_get_discovery_info", get_info)
    monkeypatch.setattr(
        "custom_components.remote_homeassistant.async_get_clientsession",
        lambda *args: session,
    )

    await remote.async_connect()
    await hass.async_block_till_done()
    remote._async_request_reauth(STATE_AUTH_INVALID)
    await hass.async_block_till_done()

    assert states[-1] == STATE_AUTH_INVALID
    assert remote._connection is None
    session.ws_connect.assert_not_awaited()
    get_info.assert_awaited_once()
    assert len(list(remote._entry.async_get_active_flows(hass, {SOURCE_REAUTH}))) == 1
    await remote.async_stop()
