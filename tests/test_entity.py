"""Tests for the entity module."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock

from homeassistant.helpers import device_registry as dr
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.quilt_hp.entity import (
    QuiltControllerEntity,
    QuiltEntity,
    QuiltIDUEntity,
    _clean,
    async_setup_dynamic_entities,
    async_via_device_id,
    controller_device_info,
    ctrl_remote_sensor_device_info,
    idu_device_info,
    location_device_info,
    odu_device_info,
    remote_sensor_device_info,
)

from .conftest import (
    make_controller,
    make_ctrl_remote_sensor,
    make_idu,
    make_location,
    make_mock_coordinator,
    make_odu,
    make_remote_sensor,
    make_snapshot,
    make_space,
)


async def test_clean_none(hass) -> None:
    """Test _clean with None."""
    assert _clean(None) is None


async def test_clean_sentinel_values(hass) -> None:
    """Test _clean with sentinel values."""
    assert _clean("N/A") is None
    assert _clean("n/a") is None
    assert _clean("NA") is None
    assert _clean("") is None


async def test_clean_valid_string(hass) -> None:
    """Test _clean with valid string."""
    assert _clean("valid") == "valid"
    assert _clean("123") == "123"


async def test_quilt_entity_init(hass) -> None:
    """Test QuiltEntity initialization."""
    snapshot = make_snapshot()
    coordinator = make_mock_coordinator(hass, snapshot)

    entity = QuiltEntity(coordinator)
    assert entity.coordinator == coordinator
    assert entity._attr_has_entity_name is True


async def test_quilt_entity_refresh_when_not_streaming(hass) -> None:
    """Test refresh request when not streaming."""
    snapshot = make_snapshot()
    coordinator = make_mock_coordinator(hass, snapshot)
    coordinator.is_streaming = False

    entity = QuiltEntity(coordinator)
    await entity._async_refresh_if_not_streaming()

    coordinator.async_request_refresh.assert_awaited_once()


async def test_quilt_entity_no_refresh_when_streaming(hass) -> None:
    """Test no refresh when streaming is active."""
    snapshot = make_snapshot()
    coordinator = make_mock_coordinator(hass, snapshot)
    coordinator.is_streaming = True

    entity = QuiltEntity(coordinator)
    await entity._async_refresh_if_not_streaming()

    coordinator.async_request_refresh.assert_not_awaited()


async def test_idu_device_info_with_name(hass) -> None:
    """Test IDU device info with configured name."""
    idu = make_idu()
    idu.settings.name = "Master Bedroom IDU"
    space = make_space(name="Master Bedroom")

    info = idu_device_info(idu, space)

    assert info["name"] == "Master Bedroom IDU"
    assert info["manufacturer"] == "Quilt"
    assert info["model"] == "Indoor Unit"
    assert info["serial_number"] == "QS1-IDU0001"
    assert info["sw_version"] == "43"
    assert info["suggested_area"] == "Master Bedroom"
    assert ("quilt_hp", f"i_{idu.id}") in info["identifiers"]


async def test_idu_device_info_omits_missing_hardware(hass) -> None:
    """Serial/firmware are omitted when the hardware map had no data."""
    idu = make_idu(serial_number=None, firmware_version=None, model_sku="N/A")
    info = idu_device_info(idu, make_space())
    assert "serial_number" not in info
    assert "sw_version" not in info
    assert info["model"] == "Indoor Unit"


async def test_idu_device_info_without_name(hass) -> None:
    """Test IDU device info without configured name."""
    idu = make_idu()
    idu.settings.name = ""
    space = make_space(name="Living Room")

    info = idu_device_info(idu, space)

    assert info["name"] == "Living Room Indoor Unit"
    assert info["suggested_area"] == "Living Room"


async def test_idu_device_info_no_space(hass) -> None:
    """Test IDU device info without space."""
    idu = make_idu()
    idu.settings.name = None

    info = idu_device_info(idu, None)

    assert info["name"].startswith("Indoor Unit")
    assert "suggested_area" not in info


async def test_idu_device_info_prefers_room_over_serial_default(hass) -> None:
    """A serial-based default IDU name is replaced by the room name."""
    idu = make_idu(serial_number="QS1-ABC123")
    idu.settings.name = "Indoor Unit QS1-ABC123"
    space = make_space(name="Family Room")

    info = idu_device_info(idu, space)

    assert info["name"] == "Family Room Indoor Unit"


async def test_idu_device_info_keeps_serial_default_without_space(hass) -> None:
    """Without a room, a serial-default name is kept rather than dropped."""
    idu = make_idu(serial_number="QS1-ABC123")
    idu.settings.name = "Indoor Unit QS1-ABC123"

    info = idu_device_info(idu, None)

    assert info["name"] == "Indoor Unit QS1-ABC123"


async def test_odu_device_info(hass) -> None:
    """Test ODU device info."""
    odu = make_odu()
    odu.model_sku = "QHP-36K"
    odu.serial_number = "SN123456"
    odu.firmware_version = "1.2.3"

    info = odu_device_info(odu, "parent-device-id")

    assert info["manufacturer"] == "Quilt"
    assert "QHP-36K" in info["model"]
    assert info["serial_number"] == "SN123456"
    assert info["sw_version"] == "1.2.3"
    assert ("quilt_hp", f"u_{odu.id}") in info["identifiers"]
    assert info["via_device_id"] == "parent-device-id"


async def test_odu_device_info_no_idu(hass) -> None:
    """Test ODU device info without IDU."""
    odu = make_odu()

    info = odu_device_info(odu, None)

    assert "via_device_id" not in info


async def test_controller_device_info(hass) -> None:
    """Test controller device info."""
    ctrl = make_controller()
    ctrl.name = "Kitchen Dial"
    ctrl.model_sku = "DIAL-V2"
    ctrl.serial_number = "CTRL-123"
    ctrl.firmware_version = "2.0.1"

    info = controller_device_info(ctrl, "parent-device-id")

    assert info["name"] == "Kitchen Dial"
    assert info["manufacturer"] == "Quilt"
    assert info["model"] == "DIAL-V2"
    assert info["serial_number"] == "CTRL-123"
    assert info["sw_version"] == "2.0.1"
    assert ("quilt_hp", f"c_{ctrl.id}") in info["identifiers"]
    assert info["via_device_id"] == "parent-device-id"


async def test_controller_device_info_no_idu(hass) -> None:
    """Test controller device info without IDU."""
    ctrl = make_controller()

    info = controller_device_info(ctrl, None)

    assert "via_device_id" not in info


async def test_controller_device_info_prefers_room_over_serial_default(hass) -> None:
    """A serial-based default Dial name is replaced by the room name."""
    ctrl = make_controller()
    ctrl.name = "Dial QD1-XYZ789"
    ctrl.serial_number = "QD1-XYZ789"
    space = make_space(name="Guest Bedroom")

    info = controller_device_info(ctrl, None, space)

    assert info["name"] == "Guest Bedroom Dial"


async def test_remote_sensor_device_info(hass) -> None:
    """Test remote sensor device info."""
    rs = make_remote_sensor()

    info = remote_sensor_device_info(rs, "parent-device-id")

    assert info["manufacturer"] == "Quilt"
    assert info["model"] == "Remote Sensor"
    assert ("quilt_hp", f"rs_{rs.id}") in info["identifiers"]
    assert info["via_device_id"] == "parent-device-id"


async def test_controller_remote_sensor_device_info(hass) -> None:
    """Test controller remote sensor device info."""
    crs = make_ctrl_remote_sensor()
    ctrl = make_controller()

    info = ctrl_remote_sensor_device_info(crs, ctrl, "parent-device-id")

    assert info["manufacturer"] == "Quilt"
    assert "Zone Sensor" in info["model"]
    assert ("quilt_hp", f"crs_{crs.id}") in info["identifiers"]
    assert info["via_device_id"] == "parent-device-id"


async def test_controller_remote_sensor_device_info_with_name(hass) -> None:
    """Test controller remote sensor device info with controller name."""
    crs = make_ctrl_remote_sensor()
    ctrl = make_controller()
    ctrl.name = "Kitchen Dial"

    info = ctrl_remote_sensor_device_info(crs, ctrl)

    assert "Kitchen Dial" in info["name"]


async def test_location_device_info(hass) -> None:
    """Test location device info."""
    loc = make_location(name="My Home")

    info = location_device_info(loc)

    assert info["name"] == "My Home"
    assert info["manufacturer"] == "Quilt"
    assert "System" in info["model"]
    assert ("quilt_hp", f"loc_{loc.id}") in info["identifiers"]


# ── QuiltIDUEntity / QuiltControllerEntity ────────────────────────────────────


async def test_idu_entity_availability(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    entity = QuiltIDUEntity(coordinator, "idu-001")
    assert entity.available is True

    coordinator.idu_by_id = {}
    assert entity.available is False


async def test_idu_entity_unavailable_when_offline(hass) -> None:
    idu = make_idu(online=False)
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    entity = QuiltIDUEntity(coordinator, "idu-001")
    assert entity.available is False


async def test_idu_entity_device_info(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    entity = QuiltIDUEntity(coordinator, "idu-001")
    assert ("quilt_hp", "i_idu-001") in entity.device_info["identifiers"]


async def test_controller_entity_availability(hass) -> None:
    ctrl = make_controller()
    coordinator = make_mock_coordinator(hass, make_snapshot(controllers=[ctrl]))
    entity = QuiltControllerEntity(coordinator, "ctrl-001")
    assert entity.available is True

    coordinator.ctrl_by_id = {}
    assert entity.available is False


async def test_controller_entity_unavailable_when_offline(hass) -> None:
    ctrl = make_controller()
    # A stale timestamp is positive evidence of being offline (None fails open).
    ctrl.state_updated_at = datetime.now(tz=UTC) - timedelta(hours=1)
    coordinator = make_mock_coordinator(hass, make_snapshot(controllers=[ctrl]))
    entity = QuiltControllerEntity(coordinator, "ctrl-001")
    assert entity.available is False


async def test_controller_entity_available_without_timestamp(hass) -> None:
    """No state timestamp → assume online (fail-open, server omits the field)."""
    ctrl = make_controller(online=False)  # state_updated_at=None
    coordinator = make_mock_coordinator(hass, make_snapshot(controllers=[ctrl]))
    entity = QuiltControllerEntity(coordinator, "ctrl-001")
    assert entity.available is True


def _register_device(hass, entry, identifier: str) -> str:
    """Register a Quilt device under *entry* and return its registry id."""
    return (
        dr.async_get(hass)
        .async_get_or_create(
            config_entry_id=entry.entry_id,
            identifiers={("quilt_hp", identifier)},
        )
        .id
    )


async def test_controller_entity_device_info(hass) -> None:
    """The Dial links to the IDU device serving the same space."""
    ctrl = make_controller()
    coordinator = make_mock_coordinator(hass, make_snapshot(controllers=[ctrl]))
    idu_device_id = _register_device(hass, coordinator.config_entry, "i_idu-001")
    entity = QuiltControllerEntity(coordinator, "ctrl-001")
    info = entity.device_info
    assert ("quilt_hp", "c_ctrl-001") in info["identifiers"]
    assert info["via_device_id"] == idu_device_id


async def test_controller_entity_device_info_unregistered_idu(hass) -> None:
    """No link when the IDU device is not in the registry yet.

    Passing a via_device_id the registry does not know raises, so the link has
    to be dropped rather than guessed.
    """
    ctrl = make_controller()
    coordinator = make_mock_coordinator(hass, make_snapshot(controllers=[ctrl]))
    entity = QuiltControllerEntity(coordinator, "ctrl-001")
    assert "via_device_id" not in entity.device_info


# ── async_via_device_id ───────────────────────────────────────────────────────


async def test_via_device_id_resolves_registered_device(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    device_id = _register_device(hass, coordinator.config_entry, "i_idu-001")
    assert async_via_device_id(coordinator, "i_idu-001") == device_id


async def test_via_device_id_unknown_identifier(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    assert async_via_device_id(coordinator, "i_nope") is None


async def test_via_device_id_ignores_other_config_entry(hass) -> None:
    """Identifiers are only unique within a config entry, so scope the lookup."""
    coordinator = make_mock_coordinator(hass, make_snapshot())
    other = MockConfigEntry(domain="quilt_hp", title="Other Quilt")
    other.add_to_hass(hass)
    _register_device(hass, other, "i_idu-001")
    assert async_via_device_id(coordinator, "i_idu-001") is None


async def test_via_device_id_without_config_entry(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    coordinator.config_entry = None
    assert async_via_device_id(coordinator, "i_idu-001") is None


# ── async_setup_dynamic_entities ──────────────────────────────────────────────


async def test_dynamic_entities_initial_add_and_listener(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    entry = MagicMock()
    added: list[list] = []

    def build_new(known: set[str]):
        return [(k, MagicMock()) for k in ("a", "b") if k not in known]

    async_setup_dynamic_entities(
        entry, coordinator, lambda ents: added.append(list(ents)), build_new
    )

    assert len(added) == 1
    assert len(added[0]) == 2
    entry.async_on_unload.assert_called_once()
    coordinator.async_add_listener.assert_called_once()


async def test_dynamic_entities_adds_only_new_keys_on_update(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    entry = MagicMock()
    added: list[list] = []
    keys = ["a"]

    def build_new(known: set[str]):
        return [(k, MagicMock()) for k in keys if k not in known]

    async_setup_dynamic_entities(
        entry, coordinator, lambda ents: added.append(list(ents)), build_new
    )
    assert len(added) == 1

    # Coordinator update with no new devices: nothing added.
    for listener in coordinator.listeners:
        listener()
    assert len(added) == 1

    # New device appears: exactly one new entity added.
    keys.append("b")
    for listener in coordinator.listeners:
        listener()
    assert len(added) == 2
    assert len(added[1]) == 1
