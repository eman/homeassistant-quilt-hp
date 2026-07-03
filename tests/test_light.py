"""Tests for the light platform."""

from __future__ import annotations

import math

import pytest
from quilt_hp.models.enums import LedAnimation

from custom_components.quilt_hp.const import DOMAIN
from custom_components.quilt_hp.light import (
    QuiltLightEntity,
    _decode_rgbw,
    _encode_rgbw,
)

from .conftest import make_idu, make_mock_coordinator, make_snapshot


@pytest.fixture
def coordinator(hass):
    idu = make_idu(led_on=True, led_brightness=0.8, led_color_code=0xFF460064)
    return make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))


@pytest.fixture
def off_coordinator(hass):
    idu = make_idu(led_on=False, led_brightness=0.5)
    return make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))


def _entity(coordinator) -> QuiltLightEntity:
    return QuiltLightEntity(coordinator, "idu-001")


# ── Codec helpers ─────────────────────────────────────────────────────────────


def test_encode_decode_rgbw_roundtrip() -> None:
    code = _encode_rgbw(255, 70, 0, 100)
    assert code == 0xFF460064
    assert _decode_rgbw(code) == (255, 70, 0, 100)


def test_decode_rgbw_zero() -> None:
    assert _decode_rgbw(0) == (0, 0, 0, 0)


# ── Properties ────────────────────────────────────────────────────────────────


def test_is_on(coordinator) -> None:
    assert _entity(coordinator).is_on is True


def test_is_off(off_coordinator) -> None:
    assert _entity(off_coordinator).is_on is False


def test_brightness(coordinator) -> None:
    assert _entity(coordinator).brightness == round(0.8 * 255)


def test_brightness_nan_returns_none(hass) -> None:
    idu = make_idu(led_brightness=math.nan)
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    assert _entity(coordinator).brightness is None


def test_rgbw_color(coordinator) -> None:
    assert _entity(coordinator).rgbw_color == (255, 70, 0, 100)


def test_effect(coordinator) -> None:
    entity = _entity(coordinator)
    assert entity.effect == "none"
    assert entity.effect_list == [
        "none",
        "sparkle_fade",
        "twinkle_fade",
        "dance",
        "chase",
    ]


def test_unique_id(coordinator) -> None:
    assert _entity(coordinator).unique_id == "quilt_idu_light_idu-001"


def test_device_info(coordinator) -> None:
    assert (DOMAIN, "i_idu-001") in _entity(coordinator).device_info["identifiers"]


def test_unavailable_when_offline(hass) -> None:
    idu = make_idu(online=False)
    coordinator = make_mock_coordinator(hass, make_snapshot(indoor_units=[idu]))
    assert not _entity(coordinator).available


# ── Writes ────────────────────────────────────────────────────────────────────


async def test_turn_on_with_brightness(coordinator) -> None:
    entity = _entity(coordinator)
    await entity.async_turn_on(brightness=128)
    coordinator.async_set_indoor_unit.assert_awaited_once()
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert abs(call_kwargs["led_brightness"] - (128 / 255)) < 0.01


async def test_turn_on_with_rgbw(coordinator) -> None:
    entity = _entity(coordinator)
    await entity.async_turn_on(rgbw_color=(255, 70, 0, 100))
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["led_color_code"] == 0xFF460064


async def test_turn_on_with_effect(coordinator) -> None:
    entity = _entity(coordinator)
    await entity.async_turn_on(effect="dance")
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["led_animation"] == LedAnimation.DANCE


async def test_turn_on_restores_brightness_when_led_off(off_coordinator) -> None:
    """turn_on without brightness on an off LED must restore full brightness."""
    entity = _entity(off_coordinator)
    await entity.async_turn_on()
    call_kwargs = off_coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["led_brightness"] == 1.0


async def test_turn_on_keeps_brightness_when_already_on(coordinator) -> None:
    """turn_on without brightness on an already-on LED must not change it."""
    entity = _entity(coordinator)
    await entity.async_turn_on()
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["led_brightness"] is None


async def test_turn_off(coordinator) -> None:
    entity = _entity(coordinator)
    await entity.async_turn_off()
    call_kwargs = coordinator.async_set_indoor_unit.call_args[1]
    assert call_kwargs["led_brightness"] == 0.0
