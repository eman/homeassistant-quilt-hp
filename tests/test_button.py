"""Tests for the button platform."""

from __future__ import annotations

from unittest.mock import MagicMock

from homeassistant.const import EntityCategory

from custom_components.quilt_hp.button import (
    IDU_BUTTON_DESCRIPTIONS,
    IDUButtonDescription,
    QuiltIDUButton,
    async_setup_entry,
)

from .conftest import make_idu, make_mock_coordinator, make_snapshot


def _desc(key: str) -> IDUButtonDescription:
    return next(d for d in IDU_BUTTON_DESCRIPTIONS if d.key == key)


async def test_async_setup_entry(hass) -> None:
    coordinator = make_mock_coordinator(
        hass, make_snapshot(indoor_units=[make_idu(), make_idu(idu_id="idu-002")])
    )
    entry = MagicMock()
    entry.runtime_data = coordinator
    entities = []
    await async_setup_entry(hass, entry, entities.extend)
    assert len(entities) == 2 * len(IDU_BUTTON_DESCRIPTIONS)


async def test_self_test_buttons_disabled_by_default(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    for desc in IDU_BUTTON_DESCRIPTIONS:
        button = QuiltIDUButton(coordinator, "idu-001", desc)
        assert button.entity_registry_enabled_default is False
        assert button.entity_category is EntityCategory.DIAGNOSTIC
        assert button.unique_id == f"quilt_idu_idu-001_{desc.key}"


async def test_start_self_test_press(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    coordinator.is_streaming = True
    button = QuiltIDUButton(coordinator, "idu-001", _desc("start_self_test"))

    await button.async_press()

    coordinator.async_start_self_test.assert_awaited_once_with(
        coordinator.idu_by_id["idu-001"]
    )
    coordinator.async_cancel_self_test.assert_not_awaited()
    coordinator.async_request_refresh.assert_not_awaited()


async def test_cancel_self_test_press_polls_without_stream(hass) -> None:
    coordinator = make_mock_coordinator(hass, make_snapshot())
    coordinator.is_streaming = False
    button = QuiltIDUButton(coordinator, "idu-001", _desc("cancel_self_test"))

    await button.async_press()

    coordinator.async_cancel_self_test.assert_awaited_once_with(
        coordinator.idu_by_id["idu-001"]
    )
    coordinator.async_request_refresh.assert_awaited_once()


async def test_button_unavailable_when_idu_offline(hass) -> None:
    coordinator = make_mock_coordinator(
        hass, make_snapshot(indoor_units=[make_idu(online=False)])
    )
    button = QuiltIDUButton(coordinator, "idu-001", _desc("start_self_test"))
    assert button.available is False
