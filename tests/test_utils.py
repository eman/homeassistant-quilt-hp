"""Tests for the utils module."""

from __future__ import annotations

import math

from custom_components.quilt_hp.utils import normalize_float


def test_normalize_float_none() -> None:
    assert normalize_float(None) is None


def test_normalize_float_nan() -> None:
    assert normalize_float(math.nan) is None


def test_normalize_float_valid() -> None:
    assert normalize_float(21.5) == 21.5


def test_normalize_float_zero() -> None:
    assert normalize_float(0.0) == 0.0


def test_normalize_float_int() -> None:
    assert normalize_float(42) == 42


def test_normalize_float_negative() -> None:
    assert normalize_float(-10.5) == -10.5


def test_normalize_float_non_numeric() -> None:
    assert normalize_float("not-a-number") is None  # type: ignore[arg-type]
