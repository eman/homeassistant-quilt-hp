"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest

from custom_components.quilt_hp.const import CONF_EMAIL, DOMAIN

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


@pytest.fixture
def mock_quilt_client():
    """Patch QuiltClient used by the config flow."""
    with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        async def mock_login(otp_callback=None):
            """Mock login matching quilt-hp-python 0.3.0 callback pattern.

            Simulates the OTP flow by calling the callback and waiting
            for the OTP to be provided via the returned future.
            """
            if otp_callback:
                # Call the callback, which returns a future that we await
                await otp_callback("send otp")

        client.login = AsyncMock(side_effect=mock_login)
        mock_cls.return_value = client
        yield client


async def test_user_step_shows_form(hass: HomeAssistant) -> None:
    """Step 1 should render the email form."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"


@pytest.mark.parametrize("expected_lingering_tasks", [True])
async def test_user_step_proceeds_to_otp(
    hass: HomeAssistant, mock_quilt_client
) -> None:
    """Valid email should proceed to the OTP step."""
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "otp"


async def test_otp_step_creates_entry(hass: HomeAssistant, mock_quilt_client) -> None:
    """Valid OTP should create the config entry (single home path)."""
    sys = MagicMock()
    sys.id = "sys-001"
    sys.name = "My Home"
    mock_quilt_client.list_systems = AsyncMock(return_value=[sys])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "My Home"
    assert result["data"] == {
        "email": "user@example.com",
        "system_id": "sys-001",
        "home_name": "My Home",
    }


async def test_multi_home_shows_selector(
    hass: HomeAssistant, mock_quilt_client
) -> None:
    """When multiple homes exist, a home selection step should be shown."""
    s1 = MagicMock()
    s1.id = "sys-001"
    s1.name = "Primary Home"

    s2 = MagicMock()
    s2.id = "sys-002"
    s2.name = "Vacation Home"

    mock_quilt_client.list_systems = AsyncMock(return_value=[s1, s2])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "home"


async def test_multi_home_creates_entry_for_chosen_home(
    hass: HomeAssistant, mock_quilt_client
) -> None:
    """Selecting a home should create an entry with the correct system_id."""
    s1 = MagicMock()
    s1.id = "sys-001"
    s1.name = "Primary Home"

    s2 = MagicMock()
    s2.id = "sys-002"
    s2.name = "Vacation Home"

    mock_quilt_client.list_systems = AsyncMock(return_value=[s1, s2])

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"home_name": "Vacation Home"}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["system_id"] == "sys-002"
    assert result["title"] == "Vacation Home"


@pytest.mark.parametrize("expected_lingering_tasks", [True])
async def test_otp_invalid_auth_shows_error(
    hass: HomeAssistant, mock_quilt_client
) -> None:
    """Bad OTP should surface an invalid_auth error."""
    from quilt_hp.exceptions import QuiltAuthError

    async def mock_login_with_error(otp_callback=None):
        """Mock login that raises QuiltAuthError on bad OTP."""
        if otp_callback:
            await otp_callback("ignored")
            raise QuiltAuthError("bad otp")

    mock_quilt_client.login = AsyncMock(side_effect=mock_login_with_error)

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "654321"}
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"]["base"] == "invalid_auth"


async def test_cannot_connect_error(hass: HomeAssistant) -> None:
    """Connection failure should surface a cannot_connect error."""
    from quilt_hp.exceptions import QuiltAuthError

    with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.login = AsyncMock(side_effect=QuiltAuthError("no network"))
        mock_cls.return_value = client

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"]["base"] == "cannot_connect"


# ── Reauth flow tests ─────────────────────────────────────────────────────────


@pytest.fixture
def existing_entry(hass: HomeAssistant):
    """Create and register a pre-existing config entry for reauth/reconfigure tests."""
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="user@example.com_sys-001",
        data={
            CONF_EMAIL: "user@example.com",
            "system_id": "sys-001",
            "home_name": "My Home",
        },
        title="My Home",
    )
    entry.add_to_hass(hass)
    return entry


@pytest.mark.parametrize("expected_lingering_tasks", [True])
async def test_reauth_otp_flow_completes(
    hass: HomeAssistant, mock_quilt_client, existing_entry
) -> None:
    """Reauth via OTP should abort the flow and preserve the existing entry."""
    from homeassistant.data_entry_flow import FlowResultType as FRT
    from pytest_homeassistant_custom_component.common import start_reauth_flow

    sys = MagicMock()
    sys.id = "sys-001"
    sys.name = "My Home"
    mock_quilt_client.list_systems = AsyncMock(return_value=[sys])

    result = await start_reauth_flow(hass, existing_entry)
    assert result["type"] is FRT.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] is FRT.FORM
    assert result["step_id"] == "otp"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )
    assert result["type"] is FRT.ABORT
    assert result["reason"] == "already_configured"


@pytest.mark.parametrize("expected_lingering_tasks", [True])
async def test_reconfigure_no_otp_updates_entry(
    hass: HomeAssistant, existing_entry
) -> None:
    """Reconfigure without OTP (cached token) should update the entry email."""
    with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        # Simulate no OTP needed (token already valid)
        client.login = AsyncMock(return_value=None)
        mock_cls.return_value = client

        result = await existing_entry.start_reconfigure_flow(hass)
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "reconfigure"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: "new@example.com"}
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert existing_entry.data[CONF_EMAIL] == "new@example.com"


@pytest.mark.parametrize("expected_lingering_tasks", [True])
async def test_reconfigure_otp_updates_entry_not_creates_new(
    hass: HomeAssistant, mock_quilt_client, existing_entry
) -> None:
    """Reconfigure with OTP must update the existing entry, not create a new one."""
    result = await existing_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "new@example.com"}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "otp"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )

    # Should update the entry, not create a new one
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert existing_entry.data[CONF_EMAIL] == "new@example.com"
    # Only the original entry should exist
    entries = hass.config_entries.async_entries(DOMAIN)
    assert len(entries) == 1
