"""Tests for QuiltCoordinator."""

from __future__ import annotations

import asyncio
from datetime import timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.update_coordinator import UpdateFailed
from homeassistant.util import dt as dt_util
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    async_fire_time_changed,
)
from quilt_hp._proto import quilt_hds_pb2
from quilt_hp.exceptions import QuiltAuthError, QuiltError
from quilt_hp.models.controller import Controller
from quilt_hp.models.enums import IndoorUnitTestMode, RemoteSensorControlMode
from quilt_hp.models.system import SystemSnapshot

from custom_components.quilt_hp.const import DOMAIN
from custom_components.quilt_hp.coordinator import SELF_TEST_PENDING_S, QuiltCoordinator

from .conftest import (
    get_stream_callback,
    make_comfort_setting,
    make_controller,
    make_entry_mock,
    make_idu,
    make_snapshot,
    make_space,
    register_device,
)

# ── Setup / shutdown ──────────────────────────────────────────────────────────


async def test_async_setup_fetches_snapshot_and_starts_stream(
    hass: HomeAssistant, mock_client
) -> None:
    """async_setup should login, fetch snapshot, build indexes, start stream."""
    client, stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    client.login.assert_awaited_once()
    client.get_snapshot.assert_awaited_once()
    stream.start.assert_awaited_once()
    assert coordinator.data is not None
    assert coordinator.client is client
    assert "space-001" in coordinator.spaces_by_id
    assert "idu-001" in coordinator.idu_by_id
    assert coordinator.first_idu_id_by_space_id["space-001"] == "idu-001"


async def test_async_setup_registers_all_stream_handlers(
    hass: HomeAssistant, mock_client
) -> None:
    """Every per-entity handler plus on_error/on_connected must be registered."""
    _client, stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    for name in (
        "on_space_update",
        "on_indoor_unit_update",
        "on_outdoor_unit_update",
        "on_controller_update",
        "on_qsm_update",
        "on_remote_sensor_update",
        "on_controller_remote_sensor_update",
        "on_error",
        "on_connected",
    ):
        assert getattr(stream, name).call_count == 1, name


async def test_async_setup_closes_client_on_failure(
    hass: HomeAssistant, mock_client
) -> None:
    """A failing setup step must close the gRPC client."""
    client, _stream = mock_client
    client.get_snapshot = AsyncMock(side_effect=Exception("snapshot failed"))

    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    with pytest.raises(Exception, match="snapshot failed"):
        await coordinator.async_setup()

    client.__aexit__.assert_awaited()


async def test_async_setup_closes_client_on_login_failure(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    client.login = AsyncMock(side_effect=QuiltError("login failed"))

    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    with pytest.raises(QuiltError):
        await coordinator.async_setup()

    client.__aexit__.assert_awaited()


async def test_async_setup_closes_client_on_cancellation(
    hass: HomeAssistant, mock_client
) -> None:
    """CancelledError (setup timeout) must still close the client."""
    client, stream = mock_client
    stream.start = AsyncMock(side_effect=asyncio.CancelledError)

    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    with pytest.raises(asyncio.CancelledError):
        await coordinator.async_setup()

    client.__aexit__.assert_awaited()


async def test_async_shutdown_stops_stream_and_closes_client(
    hass: HomeAssistant, mock_client
) -> None:
    client, stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    with patch(
        "custom_components.quilt_hp.coordinator.async_delete_issue"
    ) as mock_delete:
        await coordinator.async_shutdown()

    stream.stop.assert_awaited_once()
    client.__aexit__.assert_awaited()
    mock_delete.assert_called_once()


# ── Properties and indexes ────────────────────────────────────────────────────


async def test_is_streaming(hass: HomeAssistant, mock_client) -> None:
    _client, stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    assert not coordinator.is_streaming
    await coordinator.async_setup()
    assert coordinator.is_streaming
    stream.is_connected = False
    assert not coordinator.is_streaming


async def test_comfort_settings_indexed(hass: HomeAssistant, mock_client) -> None:
    client, _stream = mock_client
    cs = make_comfort_setting()
    client.get_snapshot = AsyncMock(return_value=make_snapshot(comfort_settings=[cs]))
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    assert coordinator.cs_by_id[cs.id] is cs
    assert cs in coordinator.cs_by_space_id[cs.space_id]


# ── Stream push handling ──────────────────────────────────────────────────────


async def test_stream_space_update_applies_to_snapshot(
    hass: HomeAssistant, mock_client
) -> None:
    """A pushed Space must be merged into the snapshot and re-indexed."""
    _client, stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    handler = get_stream_callback(stream, "on_space_update")
    handler(make_space(ambient_temp_c=25.0))

    assert coordinator.spaces_by_id["space-001"].state.ambient_temperature_c == 25.0


async def test_stream_idu_update_applies_to_snapshot(
    hass: HomeAssistant, mock_client
) -> None:
    from quilt_hp.models.enums import FanSpeed

    _client, stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    handler = get_stream_callback(stream, "on_indoor_unit_update")
    handler(make_idu(fan_speed=FanSpeed.MEDIUM))

    assert coordinator.idu_by_id["idu-001"].controls.fan_speed == FanSpeed.MEDIUM


async def test_stream_push_notifies_listeners(hass: HomeAssistant, mock_client) -> None:
    _client, stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    calls: list[bool] = []
    unsub = coordinator.async_add_listener(lambda: calls.append(True))

    handler = get_stream_callback(stream, "on_space_update")
    handler(make_space())
    unsub()

    assert calls


async def test_stream_delete_removes_object_and_reindexes(
    hass: HomeAssistant, mock_client
) -> None:
    """A deleted object leaves the snapshot and the indexes, and stays gone."""
    from quilt_hp.models.enums import FanSpeed

    client, stream = mock_client
    client.get_snapshot = AsyncMock(
        return_value=make_snapshot(
            indoor_units=[make_idu(), make_idu(idu_id="idu-002")]
        )
    )
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    calls: list[bool] = []
    unsub = coordinator.async_add_listener(lambda: calls.append(True))
    get_stream_callback(stream, "on_delete")("indoor_unit", "idu-002")
    unsub()

    assert calls
    assert "idu-002" not in coordinator.idu_by_id
    assert "idu-001" in coordinator.idu_by_id

    # An update already in flight for the deleted unit must not re-add it.
    handler = get_stream_callback(stream, "on_indoor_unit_update")
    handler(make_idu(idu_id="idu-002", fan_speed=FanSpeed.MEDIUM))
    assert "idu-002" not in coordinator.idu_by_id


async def test_stream_delete_of_unknown_object_does_not_notify(
    hass: HomeAssistant, mock_client
) -> None:
    _client, stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    calls: list[bool] = []
    unsub = coordinator.async_add_listener(lambda: calls.append(True))
    get_stream_callback(stream, "on_delete")("controller", "ctrl-missing")
    unsub()

    assert not calls


# ── Stream error / reconnect handling ─────────────────────────────────────────


async def test_stream_error_creates_issue_and_schedules_restart(
    hass: HomeAssistant, mock_client
) -> None:
    """Stream death must raise a repair issue immediately and schedule a restart."""
    _client, stream = mock_client
    entry = make_entry_mock()
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    on_error = get_stream_callback(stream, "on_error")
    with patch(
        "custom_components.quilt_hp.coordinator.async_create_issue"
    ) as mock_create:
        on_error(Exception("boom"))

    assert coordinator.stream_death_count == 1
    mock_create.assert_called_once()
    assert mock_create.call_args.kwargs["translation_placeholders"] == {"interval": "5"}
    assert "quilt_hp-stream-restart" in entry.created_task_names


async def test_stream_connected_resets_death_count_and_deletes_issue(
    hass: HomeAssistant, mock_client
) -> None:
    _client, stream = mock_client
    entry = make_entry_mock()
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    on_error = get_stream_callback(stream, "on_error")
    on_connected = get_stream_callback(stream, "on_connected")

    with patch("custom_components.quilt_hp.coordinator.async_create_issue"):
        on_error(Exception("boom"))
    assert coordinator.stream_death_count == 1

    with patch(
        "custom_components.quilt_hp.coordinator.async_delete_issue"
    ) as mock_delete:
        on_connected()

    assert coordinator.stream_death_count == 0
    mock_delete.assert_called_once()


async def test_reconnect_schedules_full_refresh_but_first_connect_does_not(
    hass: HomeAssistant, mock_client
) -> None:
    _client, stream = mock_client
    entry = make_entry_mock()
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    on_connected = get_stream_callback(stream, "on_connected")

    on_connected()  # first connect — no gap to close
    assert "quilt_hp-reconnect-refresh" not in entry.created_task_names

    on_connected()  # reconnect — events may have been lost
    assert "quilt_hp-reconnect-refresh" in entry.created_task_names


async def test_reconnect_full_refresh_fetches_snapshot(
    hass: HomeAssistant, mock_client
) -> None:
    """The reconnect refresh must actually re-fetch the snapshot."""
    client, stream = mock_client
    entry = make_entry_mock(hass)  # actually run background tasks
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    on_connected = get_stream_callback(stream, "on_connected")
    client.get_snapshot.reset_mock()

    on_connected()
    on_connected()
    await hass.async_block_till_done()

    client.get_snapshot.assert_awaited_once()


async def test_full_refresh_inflight_guard(hass: HomeAssistant, mock_client) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    client.get_snapshot.reset_mock()
    coordinator._full_refresh_inflight = True
    await coordinator._async_full_refresh()
    client.get_snapshot.assert_not_awaited()


async def test_restart_stream_with_backoff_restarts(
    hass: HomeAssistant, mock_client
) -> None:
    _client, stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    stream.start.reset_mock()

    with patch(
        "custom_components.quilt_hp.coordinator._STREAM_RESTART_INITIAL_DELAY_S", 0
    ):
        await coordinator._restart_stream_with_backoff()

    stream.stop.assert_awaited_once()
    stream.start.assert_awaited_once()


async def test_restart_stream_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    entry = make_entry_mock()
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    client.stream.side_effect = QuiltAuthError("token rejected")
    with patch(
        "custom_components.quilt_hp.coordinator._STREAM_RESTART_INITIAL_DELAY_S", 0
    ):
        await coordinator._restart_stream_with_backoff()

    entry.async_start_reauth.assert_called_once_with(hass)


# ── Auth retry ────────────────────────────────────────────────────────────────


async def test_set_indoor_unit_applies_result_to_snapshot(
    hass: HomeAssistant, mock_client
) -> None:
    """The write result is merged into the snapshot so entities update at once."""
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    updated = make_idu(led_color_code=0x11223344)
    client.set_indoor_unit = AsyncMock(return_value=updated)

    await coordinator.async_set_indoor_unit(make_idu(), led_brightness=1.0)

    assert coordinator.idu_by_id["idu-001"].controls.led_color_code == 0x11223344


async def test_set_space_applies_result_to_snapshot(
    hass: HomeAssistant, mock_client
) -> None:
    """The write result is merged into the snapshot so entities update at once."""
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    updated = make_space(ambient_temp_c=25.0)
    client.set_space = AsyncMock(return_value=updated)

    await coordinator.async_set_space(make_space(), mode=None)

    assert coordinator.spaces_by_id["space-001"].state.ambient_temperature_c == 25.0


async def test_auth_retry_success_after_relogin(
    hass: HomeAssistant, mock_client
) -> None:
    """First op raises QuiltAuthError, re-login succeeds, retry returns value."""
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    idu = make_idu()
    client.login.reset_mock()
    client.set_indoor_unit = AsyncMock(side_effect=[QuiltAuthError("expired"), idu])

    result = await coordinator.async_set_indoor_unit(idu, led_brightness=1.0)

    assert result is idu
    assert client.set_indoor_unit.await_count == 2
    client.login.assert_awaited_once()


async def test_auth_retry_login_failure_raises_config_entry_auth_failed(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    client.set_indoor_unit = AsyncMock(side_effect=QuiltAuthError("expired"))
    client.login = AsyncMock(side_effect=QuiltError("re-login failed"))

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.async_set_indoor_unit(make_idu(), led_brightness=1.0)


async def test_auth_retry_second_auth_error_raises_config_entry_auth_failed(
    hass: HomeAssistant, mock_client
) -> None:
    """If the retried op is still unauthenticated, reauth must be triggered."""
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    client.set_indoor_unit = AsyncMock(side_effect=QuiltAuthError("still expired"))

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator.async_set_indoor_unit(make_idu(), led_brightness=1.0)

    assert client.set_indoor_unit.await_count == 2


async def test_auth_retry_does_not_string_match_other_quilt_errors(
    hass: HomeAssistant, mock_client
) -> None:
    """A non-auth QuiltError must not trigger a re-login."""
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    client.login.reset_mock()
    client.set_space = AsyncMock(side_effect=QuiltError("Jwt is expired"))

    with pytest.raises(HomeAssistantError):
        await coordinator.async_set_space(make_space(), mode=None)

    client.login.assert_not_awaited()


# ── Writes ────────────────────────────────────────────────────────────────────


async def test_write_wraps_quilt_error_in_home_assistant_error(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    space = make_space()
    client.set_space = AsyncMock(side_effect=QuiltError("bad request"))

    with pytest.raises(HomeAssistantError, match="Quilt command failed") as excinfo:
        await coordinator.async_set_space(space, mode=space.controls.hvac_mode)
    assert not isinstance(excinfo.value, ConfigEntryAuthFailed)


async def test_set_schedule_execution_calls_client(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    await coordinator.async_set_schedule_execution(paused=True)
    client.set_schedule_execution.assert_awaited_once_with(paused=True)


# ── Polling fallback ──────────────────────────────────────────────────────────


async def test_poll_rebuilds_indexes(hass: HomeAssistant, mock_client) -> None:
    """Regression: the poll path must rebuild the indexed lookups.

    HA assigns coordinator.data directly from _async_update_data's return
    value, bypassing async_set_updated_data — if the indexes are not rebuilt
    there, entities keep rendering the pre-poll snapshot forever.
    """
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    new_space = make_space(space_id="space-002", ambient_temp_c=30.0)
    new_idu = make_idu(idu_id="idu-002", space_id="space-002")
    client.get_snapshot.return_value = make_snapshot(
        spaces=[new_space], indoor_units=[new_idu]
    )

    result = await coordinator._async_update_data()

    client.invalidate_snapshot.assert_called()
    assert result is client.get_snapshot.return_value
    assert "space-002" in coordinator.spaces_by_id
    assert "space-001" not in coordinator.spaces_by_id
    assert "idu-002" in coordinator.idu_by_id
    assert coordinator.first_idu_id_by_space_id == {"space-002": "idu-002"}


async def test_poll_failure_raises_update_failed(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    assert coordinator._was_available

    client.get_snapshot.side_effect = Exception("network error")
    with pytest.raises(UpdateFailed):
        await coordinator._async_update_data()
    assert not coordinator._was_available


async def test_poll_recovery_marks_available_again(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    coordinator._was_available = False
    new_snapshot = make_snapshot()
    client.get_snapshot.return_value = new_snapshot

    result = await coordinator._async_update_data()
    assert result is new_snapshot
    assert coordinator._was_available


async def test_poll_auth_failure_propagates_config_entry_auth_failed(
    hass: HomeAssistant, mock_client
) -> None:
    """ConfigEntryAuthFailed must NOT be wrapped in UpdateFailed."""
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    client.get_snapshot.side_effect = QuiltAuthError("expired")
    client.login = AsyncMock(side_effect=QuiltError("re-login failed"))

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_data()


async def test_poll_detects_dead_stream_and_schedules_restart(
    hass: HomeAssistant, mock_client
) -> None:
    _client, stream = mock_client
    entry = make_entry_mock()
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    stream.stream_state = "stopped"
    await coordinator._async_update_data()

    assert "quilt_hp-stream-restart" in entry.created_task_names


async def test_poll_updates_last_full_fetch(hass: HomeAssistant, mock_client) -> None:
    _client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    before = coordinator._last_full_fetch
    await coordinator._async_update_data()
    assert coordinator._last_full_fetch is not None
    assert coordinator._last_full_fetch >= before


# ── Energy ────────────────────────────────────────────────────────────────────


async def test_energy_fetch_success_uses_start_of_local_day(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    coordinator._energy_last_attempt = None
    metric = SimpleNamespace(space_id="space-001", total_kwh=1.5)
    client.get_energy = AsyncMock(return_value=[metric])

    await coordinator._async_update_energy()

    expected_start = dt_util.start_of_local_day()
    assert coordinator.energy_by_space_id["space-001"] == 1.5
    assert coordinator.energy_last_reset == expected_start
    assert client.get_energy.call_args[0][0] == expected_start


async def test_energy_rate_limited_on_recent_attempt(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    coordinator._energy_last_attempt = dt_util.utcnow()
    client.get_energy.reset_mock()

    await coordinator._async_update_energy()
    client.get_energy.assert_not_called()


async def test_energy_inflight_guard_prevents_concurrent_fetch(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    coordinator._energy_last_attempt = None
    coordinator._energy_fetch_inflight = True
    client.get_energy.reset_mock()

    await coordinator._async_update_energy()
    client.get_energy.assert_not_called()


async def test_energy_failure_advances_attempt_time(
    hass: HomeAssistant, mock_client
) -> None:
    """A failing energy endpoint must not be hammered on every push."""
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    coordinator._energy_last_attempt = None
    client.get_energy = AsyncMock(side_effect=Exception("energy down"))

    await coordinator._async_update_energy()  # swallowed, logged
    assert coordinator._energy_last_attempt is not None
    assert not coordinator._energy_fetch_inflight

    await coordinator._async_update_energy()  # rate-limited now
    assert client.get_energy.await_count == 1


async def test_energy_auth_failure_raises_and_clears_inflight(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    coordinator._energy_last_attempt = None
    client.get_energy = AsyncMock(side_effect=QuiltAuthError("expired"))
    client.login = AsyncMock(side_effect=QuiltError("re-login failed"))

    with pytest.raises(ConfigEntryAuthFailed):
        await coordinator._async_update_energy()
    assert not coordinator._energy_fetch_inflight


async def test_stream_push_triggers_energy_refresh_when_due(
    hass: HomeAssistant, mock_client
) -> None:
    client, stream = mock_client
    entry = make_entry_mock(hass)  # actually run background tasks
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    coordinator._energy_last_attempt = dt_util.utcnow() - timedelta(hours=1)
    metric = SimpleNamespace(space_id="space-001", total_kwh=2.5)
    client.get_energy = AsyncMock(return_value=[metric])

    handler = get_stream_callback(stream, "on_space_update")
    handler(make_space())
    await hass.async_block_till_done()

    client.get_energy.assert_awaited_once()
    assert coordinator.energy_by_space_id["space-001"] == 2.5


async def test_stream_push_skips_energy_when_not_due(
    hass: HomeAssistant, mock_client
) -> None:
    client, stream = mock_client
    entry = make_entry_mock(hass)
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    client.get_energy.reset_mock()

    handler = get_stream_callback(stream, "on_space_update")
    handler(make_space())
    await hass.async_block_till_done()

    client.get_energy.assert_not_awaited()


async def test_stream_push_schedules_refresh_for_stale_snapshot(
    hass: HomeAssistant, mock_client
) -> None:
    _client, stream = mock_client
    entry = make_entry_mock()
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    coordinator._last_full_fetch = dt_util.utcnow() - timedelta(minutes=30)

    handler = get_stream_callback(stream, "on_space_update")
    handler(make_space())

    assert "quilt_hp-stale-snapshot-refresh" in entry.created_task_names


async def test_energy_notify_auth_failure_starts_reauth(
    hass: HomeAssistant, mock_client
) -> None:
    _client, _stream = mock_client
    entry = make_entry_mock()
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()

    coordinator._async_update_energy = AsyncMock(side_effect=ConfigEntryAuthFailed)
    await coordinator._update_energy_and_notify()

    entry.async_start_reauth.assert_called_once_with(hass)


async def test_set_controller_applies_result_to_snapshot(
    hass: HomeAssistant, mock_client
) -> None:
    """The Dial write result is merged, keeping hardware fields it lacks."""
    from quilt_hp.models.enums import RemoteSensorControlMode

    client, _stream = mock_client
    ctrl = make_controller()
    ctrl.serial_number = "QD1-0001"
    client.get_snapshot = AsyncMock(return_value=make_snapshot(controllers=[ctrl]))
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    updated = make_controller()
    updated.remote_sensor_mode = RemoteSensorControlMode.DISABLED
    client.set_controller = AsyncMock(return_value=updated)

    await coordinator.async_set_controller(ctrl, uses_dial_temperature=False)

    client.set_controller.assert_awaited_once_with(ctrl, uses_dial_temperature=False)
    merged = coordinator.ctrl_by_id["ctrl-001"]
    assert merged.uses_dial_temperature is False
    assert merged.serial_number == "QD1-0001"


async def test_self_test_start_and_cancel_call_client(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    client.start_self_test = AsyncMock()
    client.cancel_self_test = AsyncMock()
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    idu = coordinator.idu_by_id["idu-001"]

    await coordinator.async_start_self_test(idu)
    await coordinator.async_cancel_self_test(idu)

    client.start_self_test.assert_awaited_once_with(idu)
    client.cancel_self_test.assert_awaited_once_with(idu)


async def test_self_test_failure_raises_home_assistant_error(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    client.start_self_test = AsyncMock(side_effect=QuiltError("precondition"))
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    with pytest.raises(HomeAssistantError, match="Quilt command failed"):
        await coordinator.async_start_self_test(coordinator.idu_by_id["idu-001"])


# ── Deletions ─────────────────────────────────────────────────────────────────


def _registered_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Return a real config entry, so deletions can reach the device registry."""
    entry = MockConfigEntry(domain=DOMAIN, title="Quilt")
    entry.add_to_hass(hass)
    return entry


def _two_dials() -> SystemSnapshot:
    return make_snapshot(
        controllers=[make_controller(), make_controller(ctrl_id="ctrl-002")]
    )


async def test_confirmed_deletion_removes_device(
    hass: HomeAssistant, mock_client
) -> None:
    """A deletion the next fetch confirms removes the object's device."""
    client, stream = mock_client
    client.get_snapshot = AsyncMock(return_value=_two_dials())
    entry = _registered_entry(hass)
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()
    deleted_id = register_device(hass, entry, "c_ctrl-002")
    kept_id = register_device(hass, entry, "c_ctrl-001")

    client.get_snapshot = AsyncMock(
        return_value=make_snapshot(controllers=[make_controller()])
    )
    get_stream_callback(stream, "on_delete")("controller", "ctrl-002")
    assert "ctrl-002" not in coordinator.ctrl_by_id
    await hass.async_block_till_done()

    client.get_snapshot.assert_awaited_once()
    registry = dr.async_get(hass)
    assert registry.async_get(deleted_id) is None
    assert registry.async_get(kept_id) is not None


async def test_deleted_object_entities_go_unavailable(
    hass: HomeAssistant, mock_client
) -> None:
    from custom_components.quilt_hp.sensor import (
        CONTROLLER_SENSOR_DESCRIPTIONS,
        QuiltControllerSensor,
    )

    client, stream = mock_client
    client.get_snapshot = AsyncMock(return_value=_two_dials())
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    sensor = QuiltControllerSensor(
        coordinator, "ctrl-002", CONTROLLER_SENSOR_DESCRIPTIONS[0]
    )
    assert sensor.available is True

    get_stream_callback(stream, "on_delete")("controller", "ctrl-002")

    assert sensor.available is False


async def test_deletion_of_moved_object_is_undone_by_fetch(
    hass: HomeAssistant, mock_client
) -> None:
    """A Dial deleted from one room and created in another comes back."""
    client, stream = mock_client
    client.get_snapshot = AsyncMock(return_value=_two_dials())
    entry = _registered_entry(hass)
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()
    device_id = register_device(hass, entry, "c_ctrl-002")

    moved = make_controller(ctrl_id="ctrl-002", space_id="space-002")
    client.get_snapshot = AsyncMock(
        return_value=make_snapshot(controllers=[make_controller(), moved])
    )
    get_stream_callback(stream, "on_delete")("controller", "ctrl-002")
    get_stream_callback(stream, "on_controller_update")(moved)
    await hass.async_block_till_done()

    # The library's tombstone would swallow the re-creation; the confirming
    # fetch restores the Dial instead.
    assert coordinator.ctrl_by_id["ctrl-002"].space_id == "space-002"
    assert dr.async_get(hass).async_get(device_id) is not None


async def test_deletion_during_fetch_is_reapplied(
    hass: HomeAssistant, mock_client
) -> None:
    """A fetch that predates a deletion must not bring the object back."""
    client, stream = mock_client
    client.get_snapshot = AsyncMock(return_value=_two_dials())
    entry = _registered_entry(hass)
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()
    device_id = register_device(hass, entry, "c_ctrl-002")
    on_delete = get_stream_callback(stream, "on_delete")

    fetches = 0

    async def _get_snapshot(**_kwargs: object) -> SystemSnapshot:
        nonlocal fetches
        fetches += 1
        if fetches == 1:
            on_delete("controller", "ctrl-002")  # arrives mid-fetch
            return _two_dials()  # built before the deletion
        return make_snapshot(controllers=[make_controller()])

    client.get_snapshot = AsyncMock(side_effect=_get_snapshot)
    await coordinator._async_full_refresh()
    await hass.async_block_till_done()

    # The deletion's own refresh ran after the stale one.
    assert fetches == 2
    assert "ctrl-002" not in coordinator.ctrl_by_id
    assert dr.async_get(hass).async_get(device_id) is None


async def test_deleted_room_removes_its_entities(
    hass: HomeAssistant, mock_client
) -> None:
    """Room entities sit on the indoor unit's device, so they're removed alone."""
    client, stream = mock_client
    client.get_snapshot = AsyncMock(
        return_value=make_snapshot(
            spaces=[make_space(), make_space(space_id="space-002", name="Den")]
        )
    )
    entry = _registered_entry(hass)
    coordinator = QuiltCoordinator(hass, entry, "user@example.com")
    await coordinator.async_setup()
    registry = er.async_get(hass)
    for unique_id in ("quilt_space_climate_space-002", "quilt_space_climate_space-001"):
        _ = registry.async_get_or_create(
            "climate", DOMAIN, unique_id, config_entry=entry
        )

    client.get_snapshot = AsyncMock(return_value=make_snapshot())
    get_stream_callback(stream, "on_delete")("space", "space-002")
    await hass.async_block_till_done()

    assert (
        registry.async_get_entity_id("climate", DOMAIN, "quilt_space_climate_space-002")
        is None
    )
    assert registry.async_get_entity_id(
        "climate", DOMAIN, "quilt_space_climate_space-001"
    )


async def test_full_refresh_requested_while_running_runs_again(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    client.get_snapshot.reset_mock()

    async def _get_snapshot(**_kwargs: object) -> SystemSnapshot:
        if client.get_snapshot.await_count == 1:
            await coordinator._async_full_refresh()  # queued, returns at once
        return make_snapshot()

    client.get_snapshot.side_effect = _get_snapshot
    await coordinator._async_full_refresh()

    assert client.get_snapshot.await_count == 2


# ── Stream subscriptions ──────────────────────────────────────────────────────


async def test_refresh_subscribes_stream_to_new_objects(
    hass: HomeAssistant, mock_client
) -> None:
    """A Dial added after setup must be streamed, not only polled."""
    client, stream = mock_client
    stream.subscribe = AsyncMock()
    coordinator = QuiltCoordinator(hass, make_entry_mock(hass), "user@example.com")
    await coordinator.async_setup()

    client.get_snapshot = AsyncMock(
        return_value=make_snapshot(controllers=[make_controller()])
    )
    await coordinator.async_refresh()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    stream.subscribe.assert_awaited_once_with(["hds/controller/ctrl-001"])


async def test_streamed_new_object_is_subscribed(
    hass: HomeAssistant, mock_client
) -> None:
    _client, stream = mock_client
    stream.subscribe = AsyncMock()
    coordinator = QuiltCoordinator(hass, make_entry_mock(hass), "user@example.com")
    await coordinator.async_setup()

    get_stream_callback(stream, "on_controller_update")(make_controller())
    await hass.async_block_till_done()

    stream.subscribe.assert_awaited_once_with(["hds/controller/ctrl-001"])
    assert "ctrl-001" in coordinator.ctrl_by_id


async def test_failed_subscribe_is_retried(hass: HomeAssistant, mock_client) -> None:
    client, stream = mock_client
    stream.subscribe = AsyncMock(side_effect=[QuiltError("busy"), None])
    coordinator = QuiltCoordinator(hass, make_entry_mock(hass), "user@example.com")
    await coordinator.async_setup()
    client.get_snapshot = AsyncMock(
        return_value=make_snapshot(controllers=[make_controller()])
    )

    await coordinator.async_refresh()
    await hass.async_block_till_done()
    await coordinator.async_refresh()
    await hass.async_block_till_done()

    assert stream.subscribe.await_count == 2


# ── Dial state ────────────────────────────────────────────────────────────────


def _dial_update(*, with_state: bool) -> Controller:
    """Build a Dial stream update as the library parses it from the wire."""
    proto = quilt_hds_pb2.Controller()
    proto.header.object_id = "ctrl-001"
    if with_state:
        proto.state.SetInParent()  # present but empty: the offline Dial
    return Controller.from_proto(proto)


async def test_stream_empty_dial_state_marks_dial_offline(
    hass: HomeAssistant, mock_client
) -> None:
    """An empty state means offline, not a 0 °C reading."""
    client, stream = mock_client
    client.get_snapshot = AsyncMock(
        return_value=make_snapshot(controllers=[make_controller()])
    )
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    get_stream_callback(stream, "on_controller_update")(_dial_update(with_state=True))

    ctrl = coordinator.ctrl_by_id["ctrl-001"]
    assert ctrl.is_online is False
    assert ctrl.ambient_temperature_c is None
    assert ctrl.ambient_light_lux is None
    assert ctrl.presence_detected is None
    assert ctrl.name == "Quilt Dial"  # fields outside state are kept


async def test_stream_dial_diff_without_state_keeps_readings(
    hass: HomeAssistant, mock_client
) -> None:
    client, stream = mock_client
    client.get_snapshot = AsyncMock(
        return_value=make_snapshot(controllers=[make_controller()])
    )
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    get_stream_callback(stream, "on_controller_update")(_dial_update(with_state=False))

    ctrl = coordinator.ctrl_by_id["ctrl-001"]
    assert ctrl.is_online is True
    assert ctrl.ambient_temperature_c == 22.0


async def test_set_controller_merges_sparse_reply(
    hass: HomeAssistant, mock_client
) -> None:
    """The server's reply has controls only; everything else is kept."""
    client, _stream = mock_client
    ctrl = make_controller()
    ctrl.serial_number = "QD1-0001"
    client.get_snapshot = AsyncMock(return_value=make_snapshot(controllers=[ctrl]))
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()

    proto = quilt_hds_pb2.Controller()
    proto.header.object_id = "ctrl-001"
    proto.controls.remote_sensor_control_mode = RemoteSensorControlMode.DISABLED
    client.set_controller = AsyncMock(return_value=Controller.from_proto(proto))

    await coordinator.async_set_controller(ctrl, uses_dial_temperature=False)

    merged = coordinator.ctrl_by_id["ctrl-001"]
    assert merged.uses_dial_temperature is False
    assert merged.serial_number == "QD1-0001"
    assert merged.name == "Quilt Dial"
    assert merged.ambient_temperature_c == 22.0
    assert merged.is_online is True


# ── Self-test ─────────────────────────────────────────────────────────────────


async def test_self_test_active_until_unit_reports(
    hass: HomeAssistant, mock_client, freezer
) -> None:
    """A requested test counts as running until the unit could report it."""
    client, _stream = mock_client
    client.start_self_test = AsyncMock()
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    idu = coordinator.idu_by_id["idu-001"]
    assert coordinator.self_test_active(idu) is False

    await coordinator.async_start_self_test(idu)
    assert coordinator.self_test_active(idu) is True

    freezer.tick(timedelta(seconds=SELF_TEST_PENDING_S + 1))
    assert coordinator.self_test_active(idu) is False
    idu.state.test_mode = IndoorUnitTestMode.HEALTH_CHECK
    assert coordinator.self_test_active(idu) is True


async def test_self_test_cancel_clears_request(
    hass: HomeAssistant, mock_client
) -> None:
    client, _stream = mock_client
    client.start_self_test = AsyncMock()
    client.cancel_self_test = AsyncMock()
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    idu = coordinator.idu_by_id["idu-001"]

    await coordinator.async_start_self_test(idu)
    await coordinator.async_cancel_self_test(idu)

    assert coordinator.self_test_active(idu) is False


async def test_self_test_without_stream_polls_when_unit_should_report(
    hass: HomeAssistant, mock_client, freezer
) -> None:
    client, stream = mock_client
    client.start_self_test = AsyncMock()
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    stream.is_connected = False
    client.get_snapshot.reset_mock()

    await coordinator.async_start_self_test(coordinator.idu_by_id["idu-001"])
    await hass.async_block_till_done()
    client.get_snapshot.assert_not_awaited()

    freezer.tick(timedelta(seconds=SELF_TEST_PENDING_S))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()
    client.get_snapshot.assert_awaited_once()


async def test_shutdown_cancels_pending_self_test_poll(
    hass: HomeAssistant, mock_client, freezer
) -> None:
    client, stream = mock_client
    client.start_self_test = AsyncMock()
    coordinator = QuiltCoordinator(hass, make_entry_mock(), "user@example.com")
    await coordinator.async_setup()
    stream.is_connected = False
    await coordinator.async_start_self_test(coordinator.idu_by_id["idu-001"])

    await coordinator.async_shutdown()
    client.get_snapshot.reset_mock()
    freezer.tick(timedelta(seconds=SELF_TEST_PENDING_S))
    async_fire_time_changed(hass)
    await hass.async_block_till_done()

    client.get_snapshot.assert_not_awaited()
