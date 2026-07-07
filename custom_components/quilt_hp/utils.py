"""Shared utilities for the Quilt Heat Pump integration."""

from __future__ import annotations

import math


def normalize_float(value: float | None) -> float | None:
    """Return *value* unchanged, or ``None`` if it is ``None`` or ``NaN``.

    The Quilt API uses ``NaN`` as a sentinel for "no reading available".
    Home Assistant expects ``None`` for unknown numeric sensor values and
    raises ``ValueError`` when a state write contains ``NaN``.

    Per :pep:`484`'s numeric tower, ``int`` values are also accepted here
    (``float`` covers them) and are returned unchanged since ``math.isnan``
    never raises for them. Any other, genuinely non-numeric input that
    ``math.isnan`` cannot handle is treated defensively: it is normalized
    to ``None`` rather than raising, matching the ``NaN`` case.
    """
    if value is None:
        return None
    try:
        if math.isnan(value):
            return None
    except TypeError:
        return None
    return value
