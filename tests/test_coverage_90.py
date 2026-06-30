"""Comprehensive tests to bring coverage to 90%+.

Covers missing lines in: coordinator, config_flow, climate, fan, sensor,
select, light, switch, binary_sensor.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed
from homeassistant.helpers.update_coordinator import UpdateFailed
import pytest
from quilt_hp.exceptions import QuiltError
from quilt_hp.models.enums import (
    ComfortSettingType,
    FanSpeed,
    HVACMode as QHVACMode,
    LedAnimation,
    LouverMode,
)
from quilt_hp.models.qsm import QsmSensors, QuiltSmartModule
from quilt_hp.models.system import ComfortSetting

from custom_components.quilt_hp.binary_sensor import (
    CONTROLLER_BINARY_SENSOR_DESCRIPTIONS,
    IDU_BINARY_SENSOR_DESCRIPTIONS,
    QuiltControllerBinarySensor,
    QuiltIDUBinarySensor,
)
from custom_components.quilt_hp.climate import QuiltClimateEntity
from custom_components.quilt_hp.coordinator import QuiltCoordinator
from custom_components.quilt_hp.fan import QuiltFanEntity, _pct_to_fan_speed
from custom_components.quilt_hp.light import QuiltLightEntity
from custom_components.quilt_hp.select import (
    QuiltLouverAngleSelect,
    QuiltLouverModeSelect,
)
from custom_components.quilt_hp.sensor import (
    CONTROLLER_REMOTE_SENSOR_DESCRIPTIONS,
    CONTROLLER_SENSOR_DESCRIPTIONS,
    IDU_SENSOR_DESCRIPTIONS,
    ODU_SENSOR_DESCRIPTIONS,
    QSM_SENSOR_DESCRIPTIONS,
    REMOTE_SENSOR_DESCRIPTIONS,
    SPACE_SENSOR_DESCRIPTIONS,
    QuiltControllerRemoteSensor,
    QuiltControllerSensor,
    QuiltEnergySensor,
    QuiltIDUSensor,
    QuiltODUSensor,
    QuiltQSMSensor,
    QuiltRemoteSensor,
    QuiltSpaceSensor,
)
from custom_components.quilt_hp.switch import QuiltScheduleSwitch

from .conftest import (
    make_controller,
    make_ctrl_remote_sensor,
    make_idu,
    make_mock_coordinator,
    make_odu,
    make_remote_sensor,
    make_snapshot,
    make_space,
)

# ── Helpers ───────────────────────────────────────────────────────────────────


def _make_qsm(
    qsm_id: str = "qsm-001",
    system_id: str = "sys-001",
) -> QuiltSmartModule:
    return QuiltSmartModule(
        id=qsm_id,
        system_id=system_id,
        led_color_code=0xFF0000FF,
        sensors=QsmSensors(
            phase_detected_raw=0.5,
            target_detected_raw=0.3,
            als_illuminance_raw=200,
            als_ir_raw=50,
            als_both_raw=250,
            accel_x_raw=0,
            accel_y_raw=0,
            accel_z_raw=1000,
        ),
        hosted_wifi=None,
        ap_wifi=None,
        p2p_wifi=None,
    )


def _make_comfort_setting(
    cs_id: str = "cs-001",
    space_id: str = "space-001",
    name: str = "Cozy",
    cs_type: ComfortSettingType = ComfortSettingType.ACTIVE,
) -> ComfortSetting:
    return ComfortSetting(
        id=cs_id,
        system_id="sys-001",
        space_id=space_id,
        name=name,
        type=cs_type,
        hvac_mode=QHVACMode.HEAT,
        heating_setpoint_c=21.0,
        cooling_setpoint_c=25.0,
        fan_speed=FanSpeed.AUTO,
        louver_mode=LouverMode.AUTO,
    )


def _entry_mock() -> MagicMock:
    entry = MagicMock()
    entry.options = {}
    return entry


@pytest.fixture
def mock_client(monkeypatch):
    """Patch QuiltClient inside coordinator module."""
    client = MagicMock()
    client.__aenter__ = AsyncMock(return_value=client)
    client.__aexit__ = AsyncMock(return_value=False)
    client.login = AsyncMock()
    client.get_snapshot = AsyncMock(return_value=make_snapshot())
    client.invalidate_snapshot = MagicMock()
    client.get_energy = AsyncMock(return_value=[])

    stream = MagicMock()
    stream.on_space_update = MagicMock()
    stream.on_indoor_unit_update = MagicMock()
    stream.on_outdoor_unit_update = MagicMock()
    stream.on_controller_update = MagicMock()
    stream.on_qsm_update = MagicMock()
    stream.on_remote_sensor_update = MagicMock()
    stream.on_controller_remote_sensor_update = MagicMock()
    stream.on_error = MagicMock()
    stream.start = AsyncMock()
    stream.stop = AsyncMock()
    client.stream.return_value = stream

    with (
        patch(
            "custom_components.quilt_hp.coordinator.QuiltClient", return_value=client
        ),
        patch("custom_components.quilt_hp.coordinator.HATokenStore"),
    ):
        yield client, stream


# ═══════════════════════════════════════════════════════════════════════════════
# COORDINATOR TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestCoordinatorStreamCallbacks:
    """Test stream update callbacks for ODU, controller, QSM, remote sensors."""

    async def test_odu_update_callback(self, hass: HomeAssistant, mock_client) -> None:
        _client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        odu = make_odu()
        coordinator.data.apply_outdoor_unit = MagicMock(side_effect=lambda u: u)
        coordinator._on_odu_update(odu)
        coordinator.data.apply_outdoor_unit.assert_called_once_with(odu)
        assert odu.id in coordinator.odu_by_id

    async def test_ctrl_update_callback(self, hass: HomeAssistant, mock_client) -> None:
        client, _stream = mock_client
        ctrl = make_controller()
        snapshot = make_snapshot(controllers=[ctrl])
        client.get_snapshot = AsyncMock(return_value=snapshot)
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        new_ctrl = make_controller(ctrl_id="ctrl-001")
        coordinator.data.apply_controller = MagicMock(side_effect=lambda c: c)
        coordinator._on_ctrl_update(new_ctrl)
        coordinator.data.apply_controller.assert_called_once_with(new_ctrl)

    async def test_qsm_update_callback(self, hass: HomeAssistant, mock_client) -> None:
        _client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        qsm = _make_qsm()
        coordinator.data.apply_qsm = MagicMock(side_effect=lambda q: q)
        coordinator._on_qsm_update(qsm)
        coordinator.data.apply_qsm.assert_called_once_with(qsm)

    async def test_remote_sensor_update_callback(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        _client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        rs = make_remote_sensor()
        coordinator._on_remote_sensor_update(rs)
        coordinator.data.apply_remote_sensor.assert_called_with(rs)

    async def test_ctrl_remote_sensor_update_callback(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        _client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        crs = make_ctrl_remote_sensor()
        coordinator._on_ctrl_remote_sensor_update(crs)
        coordinator.data.apply_controller_remote_sensor.assert_called_with(crs)


class TestCoordinatorStreamErrors:
    """Test stream error/reconnect handling."""

    async def test_stream_error_increments_count(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        _client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        coordinator._on_stream_error("connection lost")
        assert coordinator.stream_error_count == 1

    async def test_stream_error_creates_issue_at_threshold(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        _client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        with patch(
            "custom_components.quilt_hp.coordinator.async_create_issue"
        ) as mock_create:
            for i in range(5):
                coordinator._on_stream_error(f"error {i}")
            mock_create.assert_called_once()

    async def test_stream_reconnect_clears_errors(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        _client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        coordinator._on_stream_error("err")
        assert coordinator.stream_error_count == 1

        with patch(
            "custom_components.quilt_hp.coordinator.async_delete_issue"
        ) as mock_delete:
            coordinator._on_stream_reconnect()
            assert coordinator.stream_error_count == 0
            mock_delete.assert_called_once()

    async def test_stream_reconnect_logs_restoration(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        _client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        # Simulate stream error (sets _was_available = False)
        coordinator._on_stream_error("err")
        assert not coordinator._was_available

        with patch("custom_components.quilt_hp.coordinator.async_delete_issue"):
            coordinator._on_stream_reconnect()
            assert coordinator._was_available


class TestCoordinatorProperties:
    """Test coordinator property access."""

    async def test_client_property(self, hass: HomeAssistant, mock_client) -> None:
        client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()
        assert coordinator.client is client

    async def test_is_streaming(self, hass: HomeAssistant, mock_client) -> None:
        _client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        assert not coordinator.is_streaming
        await coordinator.async_setup()
        assert coordinator.is_streaming

    async def test_comfort_settings_indexed(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        client, _stream = mock_client
        cs = _make_comfort_setting()
        snapshot = make_snapshot()
        snapshot.comfort_settings = [cs]
        client.get_snapshot = AsyncMock(return_value=snapshot)
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()
        assert coordinator.cs_by_id[cs.id] is cs
        assert cs in coordinator.cs_by_space_id[cs.space_id]


class TestCoordinatorPolling:
    """Test polling fallback edge cases."""

    async def test_poll_failure_raises_update_failed(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        client.get_snapshot.side_effect = Exception("network error")
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_poll_logs_connection_lost_once(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()
        assert coordinator._was_available

        client.get_snapshot.side_effect = Exception("down")
        with pytest.raises(UpdateFailed):
            await coordinator._async_update_data()
        assert not coordinator._was_available

    async def test_poll_logs_connection_restored(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        # Simulate was_available = False (connection was lost)
        coordinator._was_available = False
        new_snapshot = make_snapshot()
        client.get_snapshot.return_value = new_snapshot

        result = await coordinator._async_update_data()
        assert result is new_snapshot
        assert coordinator._was_available


class TestCoordinatorEnergy:
    """Test energy update rate-limiting."""

    async def test_energy_throttled(self, hass: HomeAssistant, mock_client) -> None:
        client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        # First call already happened in async_setup, set a recent timestamp
        coordinator._energy_last_fetch = datetime.now(UTC)
        client.get_energy.reset_mock()

        await coordinator._async_update_energy()
        client.get_energy.assert_not_called()

    async def test_energy_fetch_success(self, hass: HomeAssistant, mock_client) -> None:
        client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        # Force stale timestamp
        coordinator._energy_last_fetch = datetime.now(UTC) - timedelta(hours=1)
        metric = SimpleNamespace(space_id="space-001", total_kwh=1.5)
        client.get_energy = AsyncMock(return_value=[metric])

        await coordinator._async_update_energy()
        assert coordinator.energy_by_space_id["space-001"] == 1.5
        assert coordinator.energy_last_reset is not None

    async def test_stream_push_refreshes_energy(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        """A stream push should trigger a (rate-limited) energy refresh."""
        client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        # Force stale so the refresh actually fetches, then simulate a push.
        coordinator._energy_last_fetch = datetime.now(UTC) - timedelta(hours=1)
        metric = SimpleNamespace(space_id="space-001", total_kwh=2.5)
        client.get_energy = AsyncMock(return_value=[metric])

        coordinator._on_stream_energy_refresh(make_space())
        await hass.async_block_till_done()

        client.get_energy.assert_awaited_once()
        assert coordinator.energy_by_space_id["space-001"] == 2.5


class TestCoordinatorAuthRetry:
    """Test auth retry failure path."""

    async def test_auth_retry_failure_raises_config_entry_auth_failed(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        idu = make_idu()
        client.set_indoor_unit = AsyncMock(side_effect=QuiltError("Jwt is expired"))
        client.login = AsyncMock(side_effect=QuiltError("re-login failed"))

        with pytest.raises(ConfigEntryAuthFailed):
            await coordinator.async_set_indoor_unit(idu, led_brightness=1.0)

    async def test_schedule_execution_uses_auth_retry(
        self, hass: HomeAssistant, mock_client
    ) -> None:
        client, _stream = mock_client
        coordinator = QuiltCoordinator(hass, _entry_mock(), "u@e.com")
        await coordinator.async_setup()

        client.set_schedule_execution = AsyncMock()
        await coordinator.async_set_schedule_execution(paused=True)
        client.set_schedule_execution.assert_awaited_once()


# ═══════════════════════════════════════════════════════════════════════════════
# CLIMATE TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestClimateDeviceInfo:
    def test_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        info = entity.device_info
        assert info is not None


class TestClimatePresets:
    def test_preset_modes_with_comfort_settings(self, hass: HomeAssistant) -> None:
        cs = _make_comfort_setting()
        snapshot = make_snapshot()
        snapshot.comfort_settings = [cs]
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.cs_by_id = {cs.id: cs}
        coordinator.cs_by_space_id = {cs.space_id: [cs]}
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        modes = entity.preset_modes
        assert "none" in modes or "Cozy" in modes
        assert "Cozy" in modes

    def test_preset_mode_returns_name_when_active(self, hass: HomeAssistant) -> None:
        cs = _make_comfort_setting()
        snapshot = make_snapshot()
        snapshot.comfort_settings = [cs]
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.cs_by_id = {cs.id: cs}
        coordinator.cs_by_space_id = {cs.space_id: [cs]}
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        # space.controls.comfort_setting_id == "cs-001" from make_space
        assert entity.preset_mode == "Cozy"

    def test_preset_mode_returns_none_when_no_cs_id(self, hass: HomeAssistant) -> None:
        space = make_space()
        space.controls.comfort_setting_id = ""
        coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        assert entity.preset_mode == "none"

    async def test_set_preset_mode_none_is_noop(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        await entity.async_set_preset_mode("none")
        coordinator.async_set_space.assert_not_awaited()

    async def test_set_preset_mode_applies(self, hass: HomeAssistant) -> None:
        cs = _make_comfort_setting()
        snapshot = make_snapshot()
        snapshot.comfort_settings = [cs]
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.cs_by_id = {cs.id: cs}
        coordinator.cs_by_space_id = {cs.space_id: [cs]}
        coordinator.is_streaming = True
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        await entity.async_set_preset_mode("Cozy")
        coordinator.async_set_space.assert_awaited_once()

    async def test_set_preset_mode_not_found(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        coordinator.cs_by_space_id = {}
        coordinator.is_streaming = True
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        # Should not raise, just log warning
        await entity.async_set_preset_mode("Nonexistent")
        coordinator.async_set_space.assert_not_awaited()


class TestClimateSetpoints:
    def test_target_temperature_cool_mode(self, hass: HomeAssistant) -> None:
        space = make_space(hvac_mode=QHVACMode.COOL, cool_setpoint_c=24.0)
        coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        assert entity.target_temperature == 24.0

    def test_target_temperature_auto_returns_none(self, hass: HomeAssistant) -> None:
        space = make_space(hvac_mode=QHVACMode.AUTO)
        coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        # AUTO mode: target_temperature should be None (uses range instead)
        assert entity.target_temperature is None

    def test_comfort_setting_returns_none_when_id_is_none(
        self, hass: HomeAssistant
    ) -> None:
        space = make_space()
        space.controls.comfort_setting_id = None
        coordinator = make_mock_coordinator(hass, make_snapshot(spaces=[space]))
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        assert entity._active_comfort_setting is None

    def test_sentinel_setpoint_uses_state_cool(self, hass: HomeAssistant) -> None:
        """When sentinel setpoints exist in COOL mode, use state setpoint."""
        space = make_space(
            hvac_mode=QHVACMode.COOL,
            heat_setpoint_c=8.0,
            cool_setpoint_c=40.0,
        )
        # Use a wrapper to add the sentinel attribute
        controls = SimpleNamespace(
            **{
                k: getattr(space.controls, k)
                for k in [
                    "hvac_mode",
                    "temperature_setpoint_c",
                    "cooling_setpoint_c",
                    "heating_setpoint_c",
                    "comfort_setting_id",
                    "comfort_setting_override",
                    "boost_mode",
                ]
            },
            has_standby_sentinel_setpoints=True,
            comfort_setting_id_or_none=None,
        )
        controls.comfort_setting_id = None
        space = SimpleNamespace(
            id=space.id,
            system_id=space.system_id,
            name=space.name,
            parent_space_id=space.parent_space_id,
            is_room=space.is_room,
            controls=controls,
            state=SimpleNamespace(
                hvac_state=space.state.hvac_state,
                ambient_temperature_c=space.state.ambient_temperature_c,
                setpoint_c=24.5,
                comfort_setting_id="",
                has_missing_setpoint=False,
                has_missing_ambient_temperature=False,
            ),
            settings=space.settings,
        )
        snapshot = make_snapshot(spaces=[space])
        snapshot.comfort_settings = []
        coordinator = make_mock_coordinator(hass, snapshot)
        entity = QuiltClimateEntity(coordinator, space.id, "idu-001")
        _, cool = entity._effective_setpoints
        assert cool == 24.5

    def test_comfort_settings_exclude_unspecified(self, hass: HomeAssistant) -> None:
        cs_unspec = _make_comfort_setting(
            cs_id="cs-u", cs_type=ComfortSettingType.UNSPECIFIED
        )
        cs_active = _make_comfort_setting(
            cs_id="cs-a", cs_type=ComfortSettingType.ACTIVE
        )
        snapshot = make_snapshot()
        snapshot.comfort_settings = [cs_unspec, cs_active]
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.cs_by_space_id = {"space-001": [cs_unspec, cs_active]}
        entity = QuiltClimateEntity(coordinator, "space-001", "idu-001")
        settings = entity._comfort_settings_for_space()
        assert len(settings) == 1
        assert settings[0].id == "cs-a"


# ═══════════════════════════════════════════════════════════════════════════════
# FAN TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestFan:
    def test_pct_to_fan_speed_blast(self) -> None:
        assert _pct_to_fan_speed(100) == FanSpeed.BLAST
        assert _pct_to_fan_speed(101) == FanSpeed.BLAST

    def test_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltFanEntity(coordinator, "idu-001")
        info = entity.device_info
        assert info is not None

    def test_speed_count(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltFanEntity(coordinator, "idu-001")
        assert entity.speed_count == 5  # 6 entries minus AUTO

    async def test_turn_on_restores_last_speed(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        coordinator.is_streaming = True
        entity = QuiltFanEntity(coordinator, "idu-001")
        entity._last_explicit_speed = FanSpeed.HIGH
        await entity.async_turn_on()
        call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
        assert call_kwargs["fan_speed"] == FanSpeed.HIGH

    async def test_turn_on_with_percentage_auto_falls_back(
        self, hass: HomeAssistant
    ) -> None:
        coordinator = make_mock_coordinator(hass)
        coordinator.is_streaming = True
        entity = QuiltFanEntity(coordinator, "idu-001")
        entity._last_explicit_speed = FanSpeed.MEDIUM
        # Percentage 0 maps to AUTO, should fall back to last explicit
        await entity.async_turn_on(percentage=0)
        call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
        assert call_kwargs["fan_speed"] == FanSpeed.MEDIUM

    async def test_turn_on_with_preset(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        coordinator.is_streaming = True
        entity = QuiltFanEntity(coordinator, "idu-001")
        await entity.async_turn_on(preset_mode="blast")
        call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
        assert call_kwargs["fan_speed"] == FanSpeed.BLAST


# ═══════════════════════════════════════════════════════════════════════════════
# SENSOR TESTS — device_info and entity construction
# ═══════════════════════════════════════════════════════════════════════════════


class TestSpaceSensor:
    def test_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        desc = SPACE_SENSOR_DESCRIPTIONS[0]
        entity = QuiltSpaceSensor(coordinator, "space-001", "idu-001", desc)
        info = entity.device_info
        assert info is not None


class TestIDUSensor:
    def test_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        desc = IDU_SENSOR_DESCRIPTIONS[0]
        entity = QuiltIDUSensor(coordinator, "idu-001", desc)
        info = entity.device_info
        assert info is not None

    def test_device_info_no_space(self, hass: HomeAssistant) -> None:
        idu = make_idu(space_id="")
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        desc = IDU_SENSOR_DESCRIPTIONS[0]
        entity = QuiltIDUSensor(coordinator, idu.id, desc)
        info = entity.device_info
        assert info is not None


class TestODUSensor:
    def test_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        desc = ODU_SENSOR_DESCRIPTIONS[0]
        entity = QuiltODUSensor(coordinator, "odu-001", "idu-001", desc)
        info = entity.device_info
        assert info is not None

    def test_available(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        desc = ODU_SENSOR_DESCRIPTIONS[0]
        entity = QuiltODUSensor(coordinator, "odu-001", "idu-001", desc)
        assert entity.available


class TestControllerSensor:
    def test_device_info(self, hass: HomeAssistant) -> None:
        ctrl = make_controller()
        snapshot = make_snapshot(controllers=[ctrl])
        coordinator = make_mock_coordinator(hass, snapshot)
        desc = CONTROLLER_SENSOR_DESCRIPTIONS[0]
        entity = QuiltControllerSensor(coordinator, "ctrl-001", desc)
        info = entity.device_info
        assert info is not None

    def test_device_info_no_space(self, hass: HomeAssistant) -> None:
        ctrl = make_controller()
        ctrl.space_id = ""
        snapshot = make_snapshot(controllers=[ctrl])
        coordinator = make_mock_coordinator(hass, snapshot)
        desc = CONTROLLER_SENSOR_DESCRIPTIONS[0]
        entity = QuiltControllerSensor(coordinator, ctrl.id, desc)
        info = entity.device_info
        assert info is not None

    def test_available(self, hass: HomeAssistant) -> None:
        ctrl = make_controller()
        snapshot = make_snapshot(controllers=[ctrl])
        coordinator = make_mock_coordinator(hass, snapshot)
        desc = CONTROLLER_SENSOR_DESCRIPTIONS[0]
        entity = QuiltControllerSensor(coordinator, ctrl.id, desc)
        assert entity.available


class TestQSMSensor:
    def test_init_and_properties(self, hass: HomeAssistant) -> None:
        qsm = _make_qsm()
        idu = make_idu(idu_id="idu-001")
        idu.qsm_id = "qsm-001"
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.qsm_by_id = {qsm.id: qsm}
        desc = QSM_SENSOR_DESCRIPTIONS[0]
        entity = QuiltQSMSensor(coordinator, "idu-001", desc)
        assert entity._idu is idu
        assert entity._qsm is qsm

    def test_device_info(self, hass: HomeAssistant) -> None:
        qsm = _make_qsm()
        idu = make_idu()
        idu.qsm_id = "qsm-001"
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.qsm_by_id = {qsm.id: qsm}
        desc = QSM_SENSOR_DESCRIPTIONS[0]
        entity = QuiltQSMSensor(coordinator, "idu-001", desc)
        info = entity.device_info
        assert info is not None

    def test_available_with_qsm(self, hass: HomeAssistant) -> None:
        qsm = _make_qsm()
        idu = make_idu()
        idu.qsm_id = "qsm-001"
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.qsm_by_id = {qsm.id: qsm}
        desc = QSM_SENSOR_DESCRIPTIONS[0]
        entity = QuiltQSMSensor(coordinator, "idu-001", desc)
        assert entity.available

    def test_available_without_qsm(self, hass: HomeAssistant) -> None:
        idu = make_idu()
        idu.qsm_id = None
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.qsm_by_id = {}
        desc = QSM_SENSOR_DESCRIPTIONS[0]
        entity = QuiltQSMSensor(coordinator, "idu-001", desc)
        assert not entity.available

    def test_native_value_with_qsm(self, hass: HomeAssistant) -> None:
        qsm = _make_qsm()
        idu = make_idu()
        idu.qsm_id = "qsm-001"
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.qsm_by_id = {qsm.id: qsm}
        desc = QSM_SENSOR_DESCRIPTIONS[0]
        entity = QuiltQSMSensor(coordinator, "idu-001", desc)
        val = entity.native_value
        assert val is not None

    def test_native_value_without_qsm(self, hass: HomeAssistant) -> None:
        idu = make_idu()
        idu.qsm_id = None
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.qsm_by_id = {}
        desc = QSM_SENSOR_DESCRIPTIONS[0]
        entity = QuiltQSMSensor(coordinator, "idu-001", desc)
        assert entity.native_value is None

    def test_device_info_no_space(self, hass: HomeAssistant) -> None:
        qsm = _make_qsm()
        idu = make_idu(space_id="")
        idu.qsm_id = "qsm-001"
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.qsm_by_id = {qsm.id: qsm}
        desc = QSM_SENSOR_DESCRIPTIONS[0]
        entity = QuiltQSMSensor(coordinator, "idu-001", desc)
        info = entity.device_info
        assert info is not None


class TestRemoteSensor:
    def test_device_info(self, hass: HomeAssistant) -> None:
        rs = make_remote_sensor()
        snapshot = make_snapshot(remote_sensors=[rs])
        coordinator = make_mock_coordinator(hass, snapshot)
        desc = REMOTE_SENSOR_DESCRIPTIONS[0]
        entity = QuiltRemoteSensor(coordinator, "rs-001", desc)
        info = entity.device_info
        assert info is not None


class TestControllerRemoteSensor:
    def test_device_info(self, hass: HomeAssistant) -> None:
        crs = make_ctrl_remote_sensor()
        ctrl = make_controller()
        snapshot = make_snapshot(controllers=[ctrl], controller_remote_sensors=[crs])
        coordinator = make_mock_coordinator(hass, snapshot)
        desc = CONTROLLER_REMOTE_SENSOR_DESCRIPTIONS[0]
        entity = QuiltControllerRemoteSensor(coordinator, "crs-001", desc)
        info = entity.device_info
        assert info is not None


class TestEnergySensor:
    def test_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltEnergySensor(coordinator, "space-001", "idu-001")
        info = entity.device_info
        assert info is not None


# ═══════════════════════════════════════════════════════════════════════════════
# SELECT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSelectDeviceInfo:
    def test_louver_mode_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltLouverModeSelect(coordinator, "idu-001")
        info = entity.device_info
        assert info is not None

    def test_louver_angle_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltLouverAngleSelect(coordinator, "idu-001")
        info = entity.device_info
        assert info is not None

    def test_louver_mode_available(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltLouverModeSelect(coordinator, "idu-001")
        assert entity.available

    def test_louver_angle_available(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltLouverAngleSelect(coordinator, "idu-001")
        assert entity.available


# ═══════════════════════════════════════════════════════════════════════════════
# LIGHT TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestLight:
    def test_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltLightEntity(coordinator, "idu-001")
        info = entity.device_info
        assert info is not None

    def test_is_on(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltLightEntity(coordinator, "idu-001")
        assert entity.is_on is True

    def test_rgbw_color(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltLightEntity(coordinator, "idu-001")
        rgbw = entity.rgbw_color
        assert rgbw is not None
        assert len(rgbw) == 4

    def test_effect_list(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltLightEntity(coordinator, "idu-001")
        effects = entity.effect_list
        assert "none" in effects
        assert "sparkle_fade" in effects

    def test_effect(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltLightEntity(coordinator, "idu-001")
        effect = entity.effect
        assert effect == "none"  # LedAnimation.NONE

    async def test_turn_on_with_brightness_from_zero(self, hass: HomeAssistant) -> None:
        idu = make_idu(led_brightness=0.0)
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        coordinator.is_streaming = True
        entity = QuiltLightEntity(coordinator, "idu-001")
        await entity.async_turn_on()
        call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
        assert call_kwargs["led_brightness"] == 1.0

    async def test_turn_on_with_rgbw(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        coordinator.is_streaming = True
        entity = QuiltLightEntity(coordinator, "idu-001")
        await entity.async_turn_on(rgbw_color=(255, 0, 128, 64))
        call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
        assert call_kwargs["led_color_code"] is not None

    async def test_turn_on_with_effect(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        coordinator.is_streaming = True
        entity = QuiltLightEntity(coordinator, "idu-001")
        await entity.async_turn_on(effect="sparkle_fade")
        call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
        assert call_kwargs["led_animation"] == LedAnimation.SPARKLE_FADE


# ═══════════════════════════════════════════════════════════════════════════════
# SWITCH TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestSwitch:
    def test_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        entity = QuiltScheduleSwitch(coordinator, "loc-001")
        info = entity.device_info
        assert info is not None


# ═══════════════════════════════════════════════════════════════════════════════
# BINARY SENSOR TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestBinarySensorDeviceInfo:
    def test_idu_device_info(self, hass: HomeAssistant) -> None:
        coordinator = make_mock_coordinator(hass)
        desc = IDU_BINARY_SENSOR_DESCRIPTIONS[0]
        entity = QuiltIDUBinarySensor(coordinator, "idu-001", desc)
        info = entity.device_info
        assert info is not None

    def test_idu_device_info_no_space(self, hass: HomeAssistant) -> None:
        idu = make_idu(space_id="")
        snapshot = make_snapshot(indoor_units=[idu])
        coordinator = make_mock_coordinator(hass, snapshot)
        desc = IDU_BINARY_SENSOR_DESCRIPTIONS[0]
        entity = QuiltIDUBinarySensor(coordinator, idu.id, desc)
        info = entity.device_info
        assert info is not None

    def test_controller_device_info(self, hass: HomeAssistant) -> None:
        ctrl = make_controller()
        snapshot = make_snapshot(controllers=[ctrl])
        coordinator = make_mock_coordinator(hass, snapshot)
        desc = CONTROLLER_BINARY_SENSOR_DESCRIPTIONS[0]
        entity = QuiltControllerBinarySensor(coordinator, "ctrl-001", desc)
        info = entity.device_info
        assert info is not None

    def test_controller_device_info_no_space(self, hass: HomeAssistant) -> None:
        ctrl = make_controller()
        ctrl.space_id = ""
        snapshot = make_snapshot(controllers=[ctrl])
        coordinator = make_mock_coordinator(hass, snapshot)
        desc = CONTROLLER_BINARY_SENSOR_DESCRIPTIONS[0]
        entity = QuiltControllerBinarySensor(coordinator, ctrl.id, desc)
        info = entity.device_info
        assert info is not None


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIG FLOW TESTS
# ═══════════════════════════════════════════════════════════════════════════════


class TestConfigFlowCachedToken:
    """Test login with cached token (no OTP needed)."""

    pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

    async def test_login_with_cached_token(self, hass: HomeAssistant) -> None:
        """Login completes immediately without OTP → route_after_login."""
        from custom_components.quilt_hp.const import CONF_EMAIL, DOMAIN

        with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            # Login completes immediately (no OTP callback called)
            client.login = AsyncMock()
            sys_obj = SimpleNamespace(id="sys-001", name="Home")
            client.list_systems = AsyncMock(return_value=[sys_obj])
            mock_cls.return_value = client

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
            )
            assert result["type"].value == "create_entry"

    async def test_login_with_cached_token_no_otp_returned_false_none(
        self, hass: HomeAssistant
    ) -> None:
        """_initiate_login returns (False, None) when login succeeds immediately."""
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow._email = "test@example.com"

        with (
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.login = AsyncMock()
            mock_cls.return_value = client

            otp_needed, error_key = await flow._initiate_login()
            assert otp_needed is False
            assert error_key is None


class TestConfigFlowUnexpectedError:
    """Test unexpected error during login."""

    async def test_unexpected_error_returns_unknown(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow._email = "test@example.com"

        with (
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.login = AsyncMock(side_effect=RuntimeError("unexpected"))
            mock_cls.return_value = client

            otp_needed, error_key = await flow._initiate_login()
            assert otp_needed is False
            assert error_key == "unknown"


class TestConfigFlowOTPEdgeCases:
    """OTP step edge cases."""

    pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

    async def test_otp_with_no_future(self, hass: HomeAssistant) -> None:
        """OTP step should show form with error if future/task is missing."""
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow._email = "test@example.com"
        flow._otp_future = None
        flow._login_task = None
        flow.context = {}

        result = await flow.async_step_otp({"otp": "123456"})
        assert result["type"].value == "form"
        assert result["errors"]["base"] == "unknown"

    @pytest.mark.parametrize("expected_lingering_tasks", [True])
    async def test_otp_unexpected_error(self, hass: HomeAssistant) -> None:
        """Unexpected error during OTP completion."""
        from custom_components.quilt_hp.const import CONF_EMAIL, DOMAIN

        with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)

            call_count = 0

            async def mock_login(otp_callback=None):
                nonlocal call_count
                call_count += 1
                if otp_callback:
                    await otp_callback("send otp")
                    if call_count == 1:
                        raise RuntimeError("boom")

            client.login = AsyncMock(side_effect=mock_login)
            mock_cls.return_value = client

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_EMAIL: "test@example.com"}
            )
            # Submit OTP → unexpected error → should show form with error
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={"otp": "123456"}
            )
            assert result["type"].value == "form"

    @pytest.mark.parametrize("expected_lingering_tasks", [True])
    async def test_otp_retry_initiate_login_error(self, hass: HomeAssistant) -> None:
        """After OTP failure, if re-initiate also fails, show that error."""
        from quilt_hp.exceptions import QuiltAuthError

        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow._email = "test@example.com"
        flow.context = {}

        otp_future = asyncio.get_running_loop().create_future()
        _ = otp_future  # created to match real flow state setup

        async def fail_login(otp_callback=None):
            if otp_callback:
                await otp_callback("send")
                raise QuiltAuthError("bad otp")

        with (
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.login = AsyncMock(side_effect=fail_login)
            mock_cls.return_value = client

            # Manually set up state as if we came from user step
            otp_needed, _ = await flow._initiate_login()
            assert otp_needed

            # Now mock re-initiate to return error
            async def fail_on_reinit(otp_callback=None):
                raise QuiltAuthError("cannot connect")

            client.login = AsyncMock(side_effect=fail_on_reinit)

            result = await flow.async_step_otp({"otp": "123456"})
            assert result["type"].value == "form"
            # errors should contain either invalid_auth from first failure
            # or cannot_connect from re-initiate

    @pytest.mark.parametrize("expected_lingering_tasks", [True])
    async def test_otp_retry_reinit_succeeds_without_otp(
        self, hass: HomeAssistant
    ) -> None:
        """After OTP failure, if re-initiate succeeds without OTP, route_after_login."""
        from quilt_hp.exceptions import QuiltAuthError

        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow._email = "test@example.com"
        flow.context = {}

        call_count = 0

        async def login_fn(otp_callback=None):
            nonlocal call_count
            call_count += 1
            if call_count == 1 and otp_callback:
                await otp_callback("send")
                raise QuiltAuthError("bad otp")
            # Second call: succeeds immediately (cached token)

        with (
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.login = AsyncMock(side_effect=login_fn)
            sys_obj = SimpleNamespace(id="sys-001", name="Home")
            client.list_systems = AsyncMock(return_value=[sys_obj])
            mock_cls.return_value = client

            otp_needed, _ = await flow._initiate_login()
            assert otp_needed

            # mock _route_after_login to avoid unique_id issues
            with patch.object(
                flow, "_route_after_login", new_callable=AsyncMock
            ) as mock_route:
                mock_route.return_value = {"type": "create_entry"}
                await flow.async_step_otp({"otp": "123456"})


class TestConfigFlowListSystemsFailure:
    """Test list_systems failure path."""

    pytestmark = pytest.mark.usefixtures("enable_custom_integrations")

    async def test_list_systems_exception_creates_entry_without_system_id(
        self, hass: HomeAssistant
    ) -> None:
        from custom_components.quilt_hp.const import CONF_EMAIL, DOMAIN

        with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)

            async def login_with_otp(otp_callback=None):
                if otp_callback:
                    await otp_callback("send")

            client.login = AsyncMock(side_effect=login_with_otp)
            client.list_systems = AsyncMock(side_effect=Exception("API down"))
            mock_cls.return_value = client

            result = await hass.config_entries.flow.async_init(
                DOMAIN, context={"source": "user"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
            )
            result = await hass.config_entries.flow.async_configure(
                result["flow_id"], user_input={"otp": "123456"}
            )
            assert result["type"].value == "create_entry"
            assert result["data"]["system_id"] is None
            assert result["data"]["home_name"] is None


class TestConfigFlowOptionsFlow:
    """Test options flow."""

    async def test_options_flow_get_handler(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import (
            QuiltConfigFlow,
            QuiltOptionsFlow,
        )

        entry = MagicMock()
        result = QuiltConfigFlow.async_get_options_flow(entry)
        assert isinstance(result, QuiltOptionsFlow)

    async def test_options_flow_shows_form(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltOptionsFlow

        entry = MagicMock()
        entry.options = {}
        entry.entry_id = "test-entry"

        flow = QuiltOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        with patch.object(
            hass.config_entries, "async_get_known_entry", return_value=entry
        ):
            result = await flow.async_step_init(None)
            assert result["type"].value == "form"
            assert result["step_id"] == "init"

    async def test_options_flow_saves(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltOptionsFlow
        from custom_components.quilt_hp.const import CONF_POLLING_INTERVAL

        entry = MagicMock()
        entry.options = {}
        entry.entry_id = "test-entry"

        flow = QuiltOptionsFlow()
        flow.hass = hass
        flow.handler = entry.entry_id

        with patch.object(
            hass.config_entries, "async_get_known_entry", return_value=entry
        ):
            result = await flow.async_step_init({CONF_POLLING_INTERVAL: 10})
            assert result["type"].value == "create_entry"


class TestConfigFlowReauth:
    """Test reauth flow."""

    @pytest.mark.parametrize("expected_lingering_tasks", [True])
    async def test_reauth_prefills_email(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.const import CONF_EMAIL, CONF_SYSTEM_ID

        # Create an existing entry
        entry = MagicMock()
        entry.data = {CONF_EMAIL: "existing@example.com", CONF_SYSTEM_ID: "sys-001"}
        entry.entry_id = "test-entry-id"

        with patch.object(hass.config_entries, "async_get_entry", return_value=entry):
            from custom_components.quilt_hp.config_flow import QuiltConfigFlow

            flow = QuiltConfigFlow()
            flow.hass = hass
            flow.context = {"entry_id": "test-entry-id"}
            result = await flow.async_step_reauth()
            assert flow._email == "existing@example.com"
            assert result["type"].value == "form"
            assert result["step_id"] == "reauth_confirm"

    @pytest.mark.parametrize("expected_lingering_tasks", [True])
    async def test_reauth_confirm_initiates_login(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow._email = "test@example.com"
        flow.context = {"entry_id": "test-entry-id"}

        with (
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)

            async def login_with_otp(otp_callback=None):
                if otp_callback:
                    await otp_callback("send")

            client.login = AsyncMock(side_effect=login_with_otp)
            mock_cls.return_value = client

            result = await flow.async_step_reauth_confirm(user_input={})
            assert result["type"].value == "form"
            assert result["step_id"] == "otp"

    async def test_reauth_confirm_shows_form_on_error(
        self, hass: HomeAssistant
    ) -> None:
        from quilt_hp.exceptions import QuiltAuthError

        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow._email = "test@example.com"
        flow.context = {"entry_id": "test-entry-id"}

        with (
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.login = AsyncMock(side_effect=QuiltAuthError("fail"))
            mock_cls.return_value = client

            result = await flow.async_step_reauth_confirm(user_input={})
            assert result["type"].value == "form"
            assert result["errors"]["base"] == "cannot_connect"

    async def test_reauth_confirm_cached_token(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow._email = "test@example.com"
        flow.context = {"entry_id": "test-entry-id"}

        with (
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.login = AsyncMock()  # succeeds immediately
            mock_cls.return_value = client

            with patch.object(
                flow, "_route_after_login", new_callable=AsyncMock
            ) as mock_route:
                mock_route.return_value = {"type": "create_entry"}
                await flow.async_step_reauth_confirm(user_input={})
                mock_route.assert_awaited_once()


class TestConfigFlowReconfigure:
    """Test reconfigure flow."""

    async def test_reconfigure_no_entry(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow.context = {"entry_id": "missing"}

        with patch.object(hass.config_entries, "async_get_entry", return_value=None):
            result = await flow.async_step_reconfigure()
            assert result["type"].value == "abort"

    async def test_reconfigure_shows_form(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow
        from custom_components.quilt_hp.const import CONF_EMAIL, CONF_SYSTEM_ID

        entry = MagicMock()
        entry.data = {CONF_EMAIL: "old@example.com", CONF_SYSTEM_ID: "sys-001"}
        entry.entry_id = "test-entry"

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow.context = {"entry_id": "test-entry"}

        with patch.object(hass.config_entries, "async_get_entry", return_value=entry):
            result = await flow.async_step_reconfigure()
            assert result["type"].value == "form"
            assert result["step_id"] == "reconfigure"

    @pytest.mark.parametrize("expected_lingering_tasks", [True])
    async def test_reconfigure_otp_path(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow
        from custom_components.quilt_hp.const import CONF_EMAIL, CONF_SYSTEM_ID

        entry = MagicMock()
        entry.data = {CONF_EMAIL: "old@example.com", CONF_SYSTEM_ID: "sys-001"}
        entry.entry_id = "test-entry"

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow.context = {"entry_id": "test-entry"}

        with (
            patch.object(hass.config_entries, "async_get_entry", return_value=entry),
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)

            async def login_otp(otp_callback=None):
                if otp_callback:
                    await otp_callback("send")

            client.login = AsyncMock(side_effect=login_otp)
            mock_cls.return_value = client

            result = await flow.async_step_reconfigure({CONF_EMAIL: "new@example.com"})
            assert result["type"].value == "form"
            assert result["step_id"] == "otp"

    async def test_reconfigure_cached_token(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow
        from custom_components.quilt_hp.const import CONF_EMAIL, CONF_SYSTEM_ID

        entry = MagicMock()
        entry.data = {CONF_EMAIL: "old@example.com", CONF_SYSTEM_ID: "sys-001"}
        entry.entry_id = "test-entry"

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow.context = {"entry_id": "test-entry"}

        with (
            patch.object(hass.config_entries, "async_get_entry", return_value=entry),
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.login = AsyncMock()  # succeeds immediately
            mock_cls.return_value = client

            with patch.object(
                flow,
                "async_update_reload_and_abort",
            ) as mock_update:
                mock_update.return_value = {"type": "abort"}
                await flow.async_step_reconfigure({CONF_EMAIL: "new@example.com"})
                mock_update.assert_called_once()

    async def test_reconfigure_login_error(self, hass: HomeAssistant) -> None:
        from quilt_hp.exceptions import QuiltAuthError

        from custom_components.quilt_hp.config_flow import QuiltConfigFlow
        from custom_components.quilt_hp.const import CONF_EMAIL, CONF_SYSTEM_ID

        entry = MagicMock()
        entry.data = {CONF_EMAIL: "old@example.com", CONF_SYSTEM_ID: "sys-001"}
        entry.entry_id = "test-entry"

        flow = QuiltConfigFlow()
        flow.hass = hass
        flow.context = {"entry_id": "test-entry"}

        with (
            patch.object(hass.config_entries, "async_get_entry", return_value=entry),
            patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls,
            patch("custom_components.quilt_hp.config_flow.HATokenStore"),
        ):
            client = MagicMock()
            client.__aenter__ = AsyncMock(return_value=client)
            client.__aexit__ = AsyncMock(return_value=False)
            client.login = AsyncMock(side_effect=QuiltAuthError("no"))
            mock_cls.return_value = client

            result = await flow.async_step_reconfigure({CONF_EMAIL: "new@example.com"})
            assert result["type"].value == "form"
            assert result["errors"]["base"] == "cannot_connect"


class TestConfigFlowCleanup:
    """Test cleanup of login tasks."""

    async def test_cleanup_cancels_running_task(self, hass: HomeAssistant) -> None:
        from custom_components.quilt_hp.config_flow import QuiltConfigFlow

        flow = QuiltConfigFlow()
        flow.hass = hass

        task = asyncio.create_task(asyncio.sleep(100))
        flow._login_task = task
        flow._otp_future = asyncio.get_running_loop().create_future()
        client = MagicMock()
        client.__aexit__ = AsyncMock(return_value=False)
        flow._client = client

        await flow._cleanup_login()
        assert flow._login_task is None
        assert flow._otp_future is None
        assert flow._client is None
        assert task.cancelled()
