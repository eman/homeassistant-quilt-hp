"""HA-backed token store for the Quilt Heat Pump integration."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.storage import Store

from quilt_hp.tokens import CachedTokens

_LOGGER = logging.getLogger(__name__)

_STORE_KEY: str = "quilt_hp_tokens"
_STORE_VERSION: int = 1


class HATokenStore:
    """``TokenStore`` backed by Home Assistant's async persistent JSON storage.

    Tokens are keyed by email address so multiple accounts can coexist in a
    single HA installation.

    A write lock serialises concurrent ``save()`` calls to prevent one coroutine
    from overwriting another's changes when two accounts authenticate at the
    same time.
    """

    def __init__(self, hass: HomeAssistant) -> None:
        """Initialize the token store."""
        self._store: Store[dict[str, Any]] = Store(hass, _STORE_VERSION, _STORE_KEY)
        self._write_lock: asyncio.Lock = asyncio.Lock()

    async def load(self, email: str) -> CachedTokens | None:
        """Load cached tokens for *email*, or ``None`` if not present."""
        data: dict[str, Any] = await self._store.async_load() or {}
        entry = data.get(email)
        if entry is None:
            return None
        try:
            return CachedTokens(
                id_token=entry["id_token"],
                refresh_token=entry["refresh_token"],
                expires_at=entry["expires_at"],
            )
        except KeyError, TypeError, AttributeError, ValueError:
            _LOGGER.warning("Malformed token cache for %s; will re-authenticate", email)
            return None

    async def save(self, email: str, tokens: CachedTokens) -> None:
        """Persist *tokens* for *email*."""
        async with self._write_lock:
            data: dict[str, Any] = await self._store.async_load() or {}
            data[email] = {
                "id_token": tokens.id_token,
                "refresh_token": tokens.refresh_token,
                "expires_at": tokens.expires_at,
            }
            await self._store.async_save(data)

    async def delete(self, email: str) -> None:
        """Remove any cached tokens for *email*."""
        async with self._write_lock:
            data: dict[str, Any] = await self._store.async_load() or {}
            if email not in data:
                return
            del data[email]
            if data:
                await self._store.async_save(data)
            else:
                await self._store.async_remove()
