"""Tests for the config flow."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    start_reauth_flow,
)
from quilt_hp.exceptions import QuiltAuthError

from custom_components.quilt_hp.const import (
    CONF_EMAIL,
    CONF_POLLING_INTERVAL,
    DOMAIN,
)

pytestmark = pytest.mark.usefixtures("enable_custom_integrations")


def _make_system(system_id: str, name: str) -> MagicMock:
    system = MagicMock()
    system.id = system_id
    system.name = name
    return system


@pytest.fixture
def mock_quilt_client():
    """Patch QuiltClient used by the config flow."""
    with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)

        async def mock_login(otp_callback=None):
            """Simulate the OTP flow: call the callback and await the OTP."""
            if otp_callback:
                await otp_callback("send otp")

        client.login = AsyncMock(side_effect=mock_login)
        mock_cls.return_value = client
        yield client


@pytest.fixture
def existing_entry(hass: HomeAssistant) -> MockConfigEntry:
    """Register a pre-existing config entry for reauth/reconfigure tests."""
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


# ── User flow ─────────────────────────────────────────────────────────────────


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
    mock_quilt_client.list_systems = AsyncMock(
        return_value=[_make_system("sys-001", "My Home")]
    )

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
    # The paused login task and its channel must be cleaned up.
    mock_quilt_client.__aexit__.assert_awaited()


async def test_cached_token_skips_otp(hass: HomeAssistant) -> None:
    """A still-valid cached token should create the entry without an OTP step."""
    with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.login = AsyncMock(return_value=None)  # never calls otp_callback
        client.list_systems = AsyncMock(return_value=[_make_system("sys-001", "Home")])
        mock_cls.return_value = client

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["system_id"] == "sys-001"


async def test_multi_home_shows_selector(
    hass: HomeAssistant, mock_quilt_client
) -> None:
    """When multiple homes exist, a home selection step should be shown."""
    mock_quilt_client.list_systems = AsyncMock(
        return_value=[
            _make_system("sys-001", "Primary Home"),
            _make_system("sys-002", "Vacation Home"),
        ]
    )

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
    mock_quilt_client.list_systems = AsyncMock(
        return_value=[
            _make_system("sys-001", "Primary Home"),
            _make_system("sys-002", "Vacation Home"),
        ]
    )

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


async def test_duplicate_home_names_get_unique_labels(
    hass: HomeAssistant, mock_quilt_client
) -> None:
    """Two homes with the same name must produce distinguishable labels."""
    mock_quilt_client.list_systems = AsyncMock(
        return_value=[
            _make_system("sys-001", "Home"),
            _make_system("sys-002", "Home"),
        ]
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )
    assert result["step_id"] == "home"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"home_name": "Home (2)"}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"]["system_id"] == "sys-002"
    assert result["data"]["home_name"] == "Home"


async def test_list_systems_failure_creates_entry_without_system_id(
    hass: HomeAssistant, mock_quilt_client
) -> None:
    """If homes cannot be listed, fall back to an entry without a system_id."""
    mock_quilt_client.list_systems = AsyncMock(side_effect=Exception("api down"))

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
    assert result["title"] == "user@example.com"
    assert result["data"]["system_id"] is None


async def test_duplicate_entry_aborts_and_cleans_up_login(
    hass: HomeAssistant, mock_quilt_client, existing_entry
) -> None:
    """Adding the same home twice must abort AND close the login client."""
    mock_quilt_client.list_systems = AsyncMock(
        return_value=[_make_system("sys-001", "My Home")]
    )

    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    mock_quilt_client.__aexit__.assert_awaited()


# ── Email-step error mapping ──────────────────────────────────────────────────


async def test_auth_error_shows_invalid_auth(hass: HomeAssistant) -> None:
    """QuiltAuthError during login initiation maps to invalid_auth."""
    with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.login = AsyncMock(side_effect=QuiltAuthError("rejected"))
        mock_cls.return_value = client

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"]["base"] == "invalid_auth"
        client.__aexit__.assert_awaited()


async def test_unexpected_error_shows_unknown(hass: HomeAssistant) -> None:
    """Non-auth failures during login initiation map to unknown."""
    with patch("custom_components.quilt_hp.config_flow.QuiltClient") as mock_cls:
        client = MagicMock()
        client.__aenter__ = AsyncMock(return_value=client)
        client.__aexit__ = AsyncMock(return_value=False)
        client.login = AsyncMock(side_effect=RuntimeError("boom"))
        mock_cls.return_value = client

        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
        )

        assert result["type"] is FlowResultType.FORM
        assert result["errors"]["base"] == "unknown"


@pytest.mark.parametrize("expected_lingering_tasks", [True])
async def test_otp_invalid_auth_shows_error(
    hass: HomeAssistant, mock_quilt_client
) -> None:
    """Bad OTP should surface an invalid_auth error."""

    async def mock_login_with_error(otp_callback=None):
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


# ── Reauth flow ───────────────────────────────────────────────────────────────


async def test_reauth_otp_flow_completes(
    hass: HomeAssistant, mock_quilt_client, existing_entry
) -> None:
    """Reauth via OTP should update+reload the entry and abort with success."""
    result = await start_reauth_flow(hass, existing_entry)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={}
    )
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "otp"

    with patch(
        "custom_components.quilt_hp.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"otp": "123456"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    # Reauth must not create a second entry.
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1
    mock_quilt_client.__aexit__.assert_awaited()


# ── Reconfigure flow ──────────────────────────────────────────────────────────


async def test_reconfigure_same_email_succeeds(
    hass: HomeAssistant, mock_quilt_client, existing_entry
) -> None:
    """Reconfiguring with the unchanged email must succeed without verification."""
    result = await existing_entry.start_reconfigure_flow(hass)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"

    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "user@example.com"}
    )
    assert result["step_id"] == "otp"

    with patch(
        "custom_components.quilt_hp.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"otp": "123456"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert existing_entry.data[CONF_EMAIL] == "user@example.com"
    assert existing_entry.unique_id == "user@example.com_sys-001"
    # No system verification needed when the account is unchanged.
    mock_quilt_client.list_systems.assert_not_called()


async def test_reconfigure_changed_email_updates_entry(
    hass: HomeAssistant, mock_quilt_client, existing_entry
) -> None:
    """Changing the email must verify the system exists on the new account."""
    mock_quilt_client.list_systems = AsyncMock(
        return_value=[_make_system("sys-001", "My Home")]
    )

    result = await existing_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "new@example.com"}
    )
    assert result["step_id"] == "otp"

    with patch(
        "custom_components.quilt_hp.async_setup_entry",
        new=AsyncMock(return_value=True),
    ):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], user_input={"otp": "123456"}
        )
        await hass.async_block_till_done()

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert existing_entry.data[CONF_EMAIL] == "new@example.com"
    assert existing_entry.unique_id == "new@example.com_sys-001"
    assert len(hass.config_entries.async_entries(DOMAIN)) == 1


async def test_reconfigure_changed_email_system_missing_aborts(
    hass: HomeAssistant, mock_quilt_client, existing_entry
) -> None:
    """The entry's system must exist on the new account."""
    mock_quilt_client.list_systems = AsyncMock(
        return_value=[_make_system("sys-999", "Other Home")]
    )

    result = await existing_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "new@example.com"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "unique_id_mismatch"
    assert existing_entry.data[CONF_EMAIL] == "user@example.com"
    mock_quilt_client.__aexit__.assert_awaited()


async def test_reconfigure_changed_email_list_failure_aborts(
    hass: HomeAssistant, mock_quilt_client, existing_entry
) -> None:
    mock_quilt_client.list_systems = AsyncMock(side_effect=Exception("api down"))

    result = await existing_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "new@example.com"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_failed"
    assert existing_entry.data[CONF_EMAIL] == "user@example.com"
    mock_quilt_client.__aexit__.assert_awaited()


async def test_reconfigure_duplicate_unique_id_aborts(
    hass: HomeAssistant, mock_quilt_client, existing_entry
) -> None:
    """Reconfiguring onto another entry's identity must abort."""
    other = MockConfigEntry(
        domain=DOMAIN,
        unique_id="new@example.com_sys-001",
        data={
            CONF_EMAIL: "new@example.com",
            "system_id": "sys-001",
            "home_name": "Other",
        },
        title="Other",
    )
    other.add_to_hass(hass)
    mock_quilt_client.list_systems = AsyncMock(
        return_value=[_make_system("sys-001", "My Home")]
    )

    result = await existing_entry.start_reconfigure_flow(hass)
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={CONF_EMAIL: "new@example.com"}
    )
    result = await hass.config_entries.flow.async_configure(
        result["flow_id"], user_input={"otp": "123456"}
    )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    assert existing_entry.data[CONF_EMAIL] == "user@example.com"
    mock_quilt_client.__aexit__.assert_awaited()


# ── Flow abandonment cleanup ──────────────────────────────────────────────────


async def test_async_remove_schedules_login_cleanup(hass: HomeAssistant) -> None:
    """Abandoning the flow mid-OTP must close the client and login task."""
    from custom_components.quilt_hp.config_flow import QuiltConfigFlow

    flow = QuiltConfigFlow()
    flow.hass = hass
    client = MagicMock()
    client.__aexit__ = AsyncMock(return_value=False)
    flow._client = client

    flow.async_remove()
    await hass.async_block_till_done()

    client.__aexit__.assert_awaited_once()
    assert flow._client is None


async def test_async_remove_noop_without_login(hass: HomeAssistant) -> None:
    from custom_components.quilt_hp.config_flow import QuiltConfigFlow

    flow = QuiltConfigFlow()
    flow.hass = hass
    flow.async_remove()  # must not raise or schedule anything
    await hass.async_block_till_done()


# ── Options flow ──────────────────────────────────────────────────────────────


async def test_options_flow_sets_polling_interval(
    hass: HomeAssistant, existing_entry
) -> None:
    result = await hass.config_entries.options.async_init(existing_entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], user_input={CONF_POLLING_INTERVAL: 10}
    )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert existing_entry.options[CONF_POLLING_INTERVAL] == 10
