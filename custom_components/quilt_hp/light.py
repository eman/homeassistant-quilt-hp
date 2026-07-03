"""Light platform for Quilt Heat Pump — one entity per IndoorUnit (LED)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, override

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_EFFECT,
    ATTR_RGBW_COLOR,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from quilt_hp.models.enums import LedAnimation

from .coordinator import QuiltCoordinator
from .entity import QuiltIDUEntity, async_setup_dynamic_entities
from .utils import normalize_float

if TYPE_CHECKING:
    from . import QuiltConfigEntry

# Limit concurrent updates to avoid overwhelming the device
PARALLEL_UPDATES = 1


def _encode_rgbw(r: int, g: int, b: int, w: int) -> int:
    """Pack RGBW bytes into Quilt's int32 color code (0xRRGGBBWW)."""
    return (r << 24) | (g << 16) | (b << 8) | w


def _decode_rgbw(code: int) -> tuple[int, int, int, int]:
    """Unpack Quilt's int32 color code to (R, G, B, W) bytes."""
    r = (code >> 24) & 0xFF
    g = (code >> 16) & 0xFF
    b = (code >> 8) & 0xFF
    w = code & 0xFF
    return r, g, b, w


_EFFECT_TO_ANIMATION: dict[str, LedAnimation] = {
    "none": LedAnimation.NONE,
    "sparkle_fade": LedAnimation.SPARKLE_FADE,
    "twinkle_fade": LedAnimation.TWINKLE_FADE,
    "dance": LedAnimation.DANCE,
    "chase": LedAnimation.CHASE,
}

_ANIMATION_TO_EFFECT: dict[LedAnimation, str] = {
    animation: effect for effect, animation in _EFFECT_TO_ANIMATION.items()
}

_SUPPORTED_COLOR_MODES: set[ColorMode] = {ColorMode.RGBW}


async def async_setup_entry(
    _hass: HomeAssistant,
    entry: QuiltConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up light entities from a config entry."""
    coordinator = entry.runtime_data

    def _build_new(known: set[str]) -> list[tuple[str, QuiltLightEntity]]:
        return [
            (idu.id, QuiltLightEntity(coordinator, idu.id))
            for idu in coordinator.data.indoor_units
            if idu.id not in known
        ]

    async_setup_dynamic_entities(entry, coordinator, async_add_entities, _build_new)


class QuiltLightEntity(QuiltIDUEntity, LightEntity):
    """Light entity representing an indoor unit's LED light."""

    _attr_color_mode: ColorMode = ColorMode.RGBW
    _attr_translation_key: str = "led"
    _attr_supported_features: LightEntityFeature = LightEntityFeature.EFFECT
    _attr_supported_color_modes: set[ColorMode] = _SUPPORTED_COLOR_MODES

    def __init__(self, coordinator: QuiltCoordinator, idu_id: str) -> None:
        """Initialize the light entity."""
        super().__init__(coordinator, idu_id)
        self._attr_unique_id: str = f"quilt_idu_light_{idu_id}"

    @property
    @override
    def is_on(self) -> bool:
        return self._idu.led_on

    @property
    @override
    def brightness(self) -> int | None:
        brightness = normalize_float(self._idu.controls.led_brightness)
        return round(brightness * 255) if brightness is not None else None

    @property
    @override
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        return _decode_rgbw(self._idu.controls.led_color_code)

    @property
    @override
    def effect_list(self) -> list[str]:
        return list(_EFFECT_TO_ANIMATION.keys())

    @property
    @override
    def effect(self) -> str | None:
        return _ANIMATION_TO_EFFECT.get(self._idu.controls.led_animation)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        rgbw = kwargs.get(ATTR_RGBW_COLOR)
        effect = kwargs.get(ATTR_EFFECT)

        brightness_pct = (brightness / 255) if brightness is not None else None
        color_code = _encode_rgbw(*rgbw) if rgbw is not None else None
        animation = _EFFECT_TO_ANIMATION.get(effect) if effect is not None else None

        # Note: quilt-hp-python 0.5.4 does not support explicit led_state
        # ON/OFF in set_indoor_unit; the LED is controlled via brightness.
        # The device may report led_state OFF while retaining a nonzero
        # brightness, so key the restore on the actual on/off state.
        target_brightness = brightness_pct
        if target_brightness is None and not self._idu.led_on:
            target_brightness = 1.0

        await self.coordinator.async_set_indoor_unit(
            self._idu,
            led_brightness=target_brightness,
            led_color_code=color_code,
            led_animation=animation,
        )
        await self._async_refresh_if_not_streaming()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        # Note: quilt-hp-python 0.5.4 does not support explicit led_state
        # ON/OFF in set_indoor_unit.
        await self.coordinator.async_set_indoor_unit(
            self._idu,
            led_brightness=0.0,
        )
        await self._async_refresh_if_not_streaming()
