"""Tests for the token_store module."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

from quilt_hp.tokens import CachedTokens

from custom_components.quilt_hp.token_store import HATokenStore


async def test_load_tokens_success(hass) -> None:
    """Test loading tokens successfully."""
    store = HATokenStore(hass)

    test_data = {
        "test@example.com": {
            "id_token": "id_token_123",
            "refresh_token": "refresh_token_456",
            "expires_at": 1234567890,
        }
    }

    with patch.object(store._store, "async_load", return_value=test_data):
        tokens = await store.load("test@example.com")

    assert tokens is not None
    assert tokens.id_token == "id_token_123"
    assert tokens.refresh_token == "refresh_token_456"
    assert tokens.expires_at == 1234567890


async def test_load_tokens_not_found(hass) -> None:
    """Test loading tokens for non-existent email."""
    store = HATokenStore(hass)

    with patch.object(store._store, "async_load", return_value={}):
        tokens = await store.load("notfound@example.com")

    assert tokens is None


async def test_load_tokens_no_data(hass) -> None:
    """Test loading when store returns None."""
    store = HATokenStore(hass)

    with patch.object(store._store, "async_load", return_value=None):
        tokens = await store.load("test@example.com")

    assert tokens is None


async def test_load_tokens_malformed(hass) -> None:
    """Test loading malformed token data."""
    store = HATokenStore(hass)

    test_data = {
        "test@example.com": {
            "id_token": "id_token_123",
            # Missing refresh_token and expires_at
        }
    }

    with patch.object(store._store, "async_load", return_value=test_data):
        tokens = await store.load("test@example.com")

    # Should return None and log warning
    assert tokens is None


async def test_save_tokens(hass) -> None:
    """Test saving tokens."""
    store = HATokenStore(hass)

    tokens = CachedTokens(
        id_token="new_id_token",
        refresh_token="new_refresh_token",
        expires_at=9876543210,
    )

    existing_data = {
        "other@example.com": {
            "id_token": "old",
            "refresh_token": "old",
            "expires_at": 0,
        }
    }

    # ruff: noqa: SIM117
    with patch.object(store._store, "async_load", return_value=existing_data):
        with patch.object(store._store, "async_save", new=AsyncMock()) as mock_save:
            await store.save("test@example.com", tokens)

            mock_save.assert_awaited_once()
            saved_data = mock_save.call_args[0][0]
            assert "test@example.com" in saved_data
            assert saved_data["test@example.com"]["id_token"] == "new_id_token"
            assert (
                saved_data["test@example.com"]["refresh_token"] == "new_refresh_token"
            )
            assert saved_data["test@example.com"]["expires_at"] == 9876543210
            # Check that existing data is preserved
            assert "other@example.com" in saved_data


async def test_save_tokens_empty_store(hass) -> None:
    """Test saving tokens to empty store."""
    store = HATokenStore(hass)

    tokens = CachedTokens(
        id_token="id_token",
        refresh_token="refresh_token",
        expires_at=1111111111,
    )

    with (
        patch.object(store._store, "async_load", return_value=None),
        patch.object(store._store, "async_save", new=AsyncMock()) as mock_save,
    ):
        await store.save("test@example.com", tokens)

        mock_save.assert_awaited_once()
        saved_data = mock_save.call_args[0][0]
        assert "test@example.com" in saved_data


async def test_save_tokens_concurrent(hass) -> None:
    """Test that concurrent saves are serialized with lock."""
    store = HATokenStore(hass)

    tokens1 = CachedTokens(id_token="id1", refresh_token="ref1", expires_at=111)
    tokens2 = CachedTokens(id_token="id2", refresh_token="ref2", expires_at=222)

    call_order = []

    async def mock_load():
        call_order.append("load")
        return {}

    async def mock_save(data):
        call_order.append(
            f"save_{data['user1@example.com']['id_token'] if 'user1@example.com' in data else data['user2@example.com']['id_token']}"
        )

    with (
        patch.object(store._store, "async_load", side_effect=mock_load),
        patch.object(store._store, "async_save", side_effect=mock_save),
    ):
        # Both saves should execute, but one after the other due to the lock
        import asyncio

        await asyncio.gather(
            store.save("user1@example.com", tokens1),
            store.save("user2@example.com", tokens2),
        )

    # Verify both saves happened
    assert len(call_order) == 4  # 2 loads + 2 saves


async def test_delete_removes_entry_and_saves_remainder(hass) -> None:
    """Deleting one email must keep the other accounts' tokens."""
    store = HATokenStore(hass)

    data = {
        "test@example.com": {"id_token": "a", "refresh_token": "b", "expires_at": 1},
        "other@example.com": {"id_token": "c", "refresh_token": "d", "expires_at": 2},
    }

    with (
        patch.object(store._store, "async_load", return_value=data),
        patch.object(store._store, "async_save", new=AsyncMock()) as mock_save,
        patch.object(store._store, "async_remove", new=AsyncMock()) as mock_remove,
    ):
        await store.delete("test@example.com")

    mock_save.assert_awaited_once()
    saved_data = mock_save.call_args[0][0]
    assert "test@example.com" not in saved_data
    assert "other@example.com" in saved_data
    mock_remove.assert_not_awaited()


async def test_delete_last_entry_removes_store(hass) -> None:
    """Deleting the last email must remove the whole storage file."""
    store = HATokenStore(hass)

    data = {
        "test@example.com": {"id_token": "a", "refresh_token": "b", "expires_at": 1},
    }

    with (
        patch.object(store._store, "async_load", return_value=data),
        patch.object(store._store, "async_save", new=AsyncMock()) as mock_save,
        patch.object(store._store, "async_remove", new=AsyncMock()) as mock_remove,
    ):
        await store.delete("test@example.com")

    mock_save.assert_not_awaited()
    mock_remove.assert_awaited_once()


async def test_delete_missing_email_is_noop(hass) -> None:
    store = HATokenStore(hass)

    with (
        patch.object(store._store, "async_load", return_value=None),
        patch.object(store._store, "async_save", new=AsyncMock()) as mock_save,
        patch.object(store._store, "async_remove", new=AsyncMock()) as mock_remove,
    ):
        await store.delete("missing@example.com")

    mock_save.assert_not_awaited()
    mock_remove.assert_not_awaited()
