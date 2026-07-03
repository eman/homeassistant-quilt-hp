"""Shared utilities for the Quilt Heat Pump integration."""

from __future__ import annotations

import math


def normalize_float(value: float | None) -> float | None:
    """Return *value* unchanged, or ``None`` if it is ``None`` or ``NaN``.

    The Quilt API uses ``NaN`` as a sentinel for "no reading available".
    Home Assistant expects ``None`` for unknown numeric sensor values and
    raises ``ValueError`` when a state write contains ``NaN``.
    """
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except TypeError:
        return None
    return value
