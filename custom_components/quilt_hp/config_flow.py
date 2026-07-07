"""Config flow for the Quilt Heat Pump integration."""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any, override

from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowError
from homeassistant.helpers.selector import (
    NumberSelector,
    NumberSelectorConfig,
    NumberSelectorMode,
)
import voluptuous as vol

from quilt_hp import QuiltClient
from quilt_hp.exceptions import QuiltAuthError

from .const import (
    CONF_EMAIL,
    CONF_HOME_NAME,
    CONF_POLLING_INTERVAL,
    CONF_SYSTEM_ID,
    COORDINATOR_UPDATE_INTERVAL_MAX,
    COORDINATOR_UPDATE_INTERVAL_MIN,
    COORDINATOR_UPDATE_INTERVAL_MINUTES,
    DOMAIN,
)
from .token_store import HATokenStore

_LOGGER = logging.getLogger(__name__)


class QuiltConfigFlow(config_entries.ConfigFlow, domain=DOMAIN):
    """Config flow: email → OTP → (home selection if multiple) → done."""

    VERSION: int = 1

    @staticmethod
    def async_get_options_flow(
        config_entry: config_entries.ConfigEntry,
    ) -> QuiltOptionsFlow:
        """Return the options flow handler."""
        return QuiltOptionsFlow()

    def __init__(self) -> None:
        """Initialize the config flow."""
        self._email: str = ""
        self._client: QuiltClient | None = None
        self._systems: list[tuple[str, str]] = []
        # Login task kept alive across steps so the Cognito challenge session
        # is preserved. Resolved via _otp_future when the user submits the OTP.
        self._login_task: asyncio.Task[None] | None = None
        self._otp_future: asyncio.Future[str] | None = None

    # ------------------------------------------------------------------
    # Step 1: collect the email address and trigger the OTP send
    # ------------------------------------------------------------------

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Collect email and initiate OTP delivery."""
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip().lower()
            otp_needed, error_key = await self._initiate_login()
            if error_key:
                errors["base"] = error_key
            elif otp_needed:
                return await self.async_step_otp()
            else:
                return await self._route_after_login()

        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema({vol.Required(CONF_EMAIL): str}),
            errors=errors,
        )

    async def _initiate_login(self) -> tuple[bool, str | None]:
        """Create a client and start the login task.

        Returns ``(otp_needed, error_key)``.  When *otp_needed* is ``True``
        the login task is paused waiting for the OTP future and the caller
        should show the OTP form.  *error_key* is non-``None`` on failure.
        """
        token_store = HATokenStore(self.hass)
        self._client = QuiltClient(self._email, token_store=token_store)
        _ = await self._client.__aenter__()

        otp_ready: asyncio.Event = asyncio.Event()
        self._otp_future = asyncio.get_running_loop().create_future()
        otp_future = self._otp_future  # capture for the closure

        async def _otp_callback(_: str) -> str:
            otp_ready.set()
            return await otp_future

        self._login_task = asyncio.create_task(
            self._client.login(otp_callback=_otp_callback)
        )

        # Race: did the login finish immediately (valid cached token) or did
        # it pause waiting for the OTP?
        otp_ready_task = asyncio.create_task(otp_ready.wait())
        done, _ = await asyncio.wait(
            {self._login_task, otp_ready_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        otp_ready_task.cancel()

        if self._login_task not in done:
            # Cognito sent the OTP email; login is paused on the future.
            return True, None

        # Login completed without OTP (cached token) or failed.
        try:
            self._login_task.result()
            return False, None
        except QuiltAuthError:
            await self._cleanup_login()
            return False, "invalid_auth"
        except Exception:
            _LOGGER.exception("Unexpected error initiating Quilt login")
            await self._cleanup_login()
            return False, "unknown"

    # ------------------------------------------------------------------
    # Step 2: collect the OTP and resume the paused login task
    # ------------------------------------------------------------------

    async def async_step_otp(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Resume the login task with the user-supplied OTP."""
        errors: dict[str, str] = {}

        if user_input is not None:
            otp = user_input["otp"].strip()
            if self._otp_future is None or self._login_task is None:
                return self.async_show_form(
                    step_id="otp",
                    data_schema=vol.Schema({vol.Required("otp"): str}),
                    errors={"base": "unknown"},
                    description_placeholders={"email": self._email},
                )

            self._otp_future.set_result(otp)
            try:
                await self._login_task
                return await self._route_after_login()
            except QuiltAuthError:
                errors["base"] = "invalid_auth"
            except FlowError:
                # HA raises FlowError (e.g. AbortFlow) to signal successful
                # completion in reauth/reconfigure contexts — let it propagate.
                raise
            except Exception:
                _LOGGER.exception("Unexpected error completing Quilt OTP login")
                errors["base"] = "unknown"

            # Restart so a fresh OTP is sent for the next attempt.
            await self._cleanup_login()
            otp_needed, error_key = await self._initiate_login()
            if error_key:
                errors["base"] = error_key
            elif not otp_needed:
                return await self._route_after_login()

        return self.async_show_form(
            step_id="otp",
            data_schema=vol.Schema({vol.Required("otp"): str}),
            errors=errors,
            description_placeholders={"email": self._email},
        )

    async def _route_after_login(self) -> config_entries.ConfigFlowResult:
        """After successful login, finish according to the flow context.

        Reauth and reconfigure update the existing entry; the user flow
        continues to home selection / entry creation.
        """
        if self.source == config_entries.SOURCE_REAUTH:
            entry = self._get_reauth_entry()
            await self._cleanup_login()
            return self.async_update_reload_and_abort(entry, reason="reauth_successful")

        if self.source == config_entries.SOURCE_RECONFIGURE:
            return await self._finish_reconfigure()

        try:
            assert self._client is not None
            systems = await self._client.list_systems()
        except Exception:
            _LOGGER.exception("Could not list Quilt systems")
            # Fall back: create entry without a system_id; coordinator will
            # use the default (first) system.
            return await self._create_entry(system_id=None, home_name=None)

        self._systems = [(s.id, s.name) for s in systems]

        if len(self._systems) <= 1:
            # Single home — no need to ask.
            sid, name = self._systems[0] if self._systems else (None, None)
            return await self._create_entry(system_id=sid, home_name=name)

        # Multiple homes — show a selector.
        return await self.async_step_home()

    async def _finish_reconfigure(self) -> config_entries.ConfigFlowResult:
        """Update the reconfigured entry, keeping its unique ID consistent."""
        entry = self._get_reconfigure_entry()
        system_id: str | None = entry.data.get(CONF_SYSTEM_ID)

        if self._email != entry.data.get(CONF_EMAIL) and system_id:
            # Email changed — the entry's system must exist on the new account,
            # otherwise the entry would point at a system it cannot reach.
            try:
                assert self._client is not None
                systems = await self._client.list_systems()
            except Exception:
                _LOGGER.exception("Could not list Quilt systems during reconfigure")
                await self._cleanup_login()
                return self.async_abort(reason="reconfigure_failed")
            if system_id not in {s.id for s in systems}:
                await self._cleanup_login()
                return self.async_abort(reason="unique_id_mismatch")

        unique_id = f"{self._email}_{system_id}" if system_id else self._email
        existing = self.hass.config_entries.async_entry_for_domain_unique_id(
            DOMAIN, unique_id
        )
        if existing is not None and existing.entry_id != entry.entry_id:
            await self._cleanup_login()
            return self.async_abort(reason="already_configured")

        await self._cleanup_login()
        return self.async_update_reload_and_abort(
            entry,
            unique_id=unique_id,
            data_updates={CONF_EMAIL: self._email},
            reason="reconfigure_successful",
        )

    # ------------------------------------------------------------------
    # Step 3 (conditional): pick which home to use
    # ------------------------------------------------------------------

    async def async_step_home(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Let the user choose which Quilt home to integrate."""
        # Build a display-label → (system_id, name) mapping.  When duplicate
        # names exist, append a suffix so each label is unique.
        name_counts: dict[str, int] = {}
        for _, name in self._systems:
            name_counts[name] = name_counts.get(name, 0) + 1

        label_to_system: dict[str, tuple[str, str]] = {}
        name_seen: dict[str, int] = {}
        for sid, name in self._systems:
            if name_counts[name] > 1:
                idx = name_seen.get(name, 0) + 1
                name_seen[name] = idx
                label = f"{name} ({idx})"
            else:
                label = name
            label_to_system[label] = (sid, name)

        if user_input is not None:
            chosen_label = user_input[CONF_HOME_NAME]
            chosen_sid: str | None
            chosen_name: str
            chosen_sid, chosen_name = label_to_system.get(
                chosen_label, (None, chosen_label)
            )
            return await self._create_entry(system_id=chosen_sid, home_name=chosen_name)

        labels = list(label_to_system.keys())
        return self.async_show_form(
            step_id="home",
            data_schema=vol.Schema({vol.Required(CONF_HOME_NAME): vol.In(labels)}),
            description_placeholders={"count": str(len(self._systems))},
        )

    # ------------------------------------------------------------------
    # Shared entry creation
    # ------------------------------------------------------------------

    async def _create_entry(
        self, system_id: str | None, home_name: str | None
    ) -> config_entries.ConfigFlowResult:
        """Create the config entry, preventing duplicates per system."""
        # Unique ID: email + system_id so each home gets its own entry.
        unique_id = f"{self._email}_{system_id}" if system_id else self._email
        _ = await self.async_set_unique_id(unique_id)
        # Clean up before the abort check so a duplicate add doesn't leak
        # the paused login task and its gRPC channel.
        await self._cleanup_login()
        self._abort_if_unique_id_configured()

        title = home_name or self._email
        return self.async_create_entry(
            title=title,
            data={
                CONF_EMAIL: self._email,
                CONF_SYSTEM_ID: system_id,
                CONF_HOME_NAME: home_name,
            },
        )

    # ------------------------------------------------------------------
    # Re-authentication flow (token expired)
    # ------------------------------------------------------------------

    async def async_step_reauth(
        self, _: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Re-authentication entry point — prefill email and re-run OTP flow."""
        self._email = self._get_reauth_entry().data[CONF_EMAIL]
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show re-auth confirmation form (email is pre-filled)."""
        errors: dict[str, str] = {}

        if user_input is not None:
            otp_needed, error_key = await self._initiate_login()
            if error_key:
                errors["base"] = error_key
            elif otp_needed:
                return await self.async_step_otp()
            else:
                return await self._route_after_login()

        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=vol.Schema({}),
            errors=errors,
            description_placeholders={"email": self._email},
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Allow user to reconfigure the integration (change email or re-authenticate).

        This is different from reauth - it allows changing the email address,
        not just re-authenticating with the same email.
        """
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            self._email = user_input[CONF_EMAIL].strip().lower()
            otp_needed, error_key = await self._initiate_login()
            if error_key:
                errors["base"] = error_key
            elif otp_needed:
                return await self.async_step_otp()
            else:
                # Login succeeded without OTP — update the entry directly.
                return await self._finish_reconfigure()

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_EMAIL,
                        default=entry.data.get(CONF_EMAIL, ""),
                    ): str,
                }
            ),
            errors=errors,
            description_placeholders={"email": entry.data.get(CONF_EMAIL, "")},
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    async def _cleanup_login(self) -> None:
        """Cancel any in-flight login task and close the client."""
        if self._login_task is not None and not self._login_task.done():
            self._login_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._login_task
        self._login_task = None
        self._otp_future = None
        if self._client is not None:
            with contextlib.suppress(Exception):
                await self._client.__aexit__(None, None, None)
            self._client = None

    @override
    def async_remove(self) -> None:
        """Clean up when the flow is aborted or abandoned.

        A user who dismisses the OTP dialog would otherwise leave the login
        task parked on the OTP future and the gRPC channel open forever.
        """
        if self._login_task is not None or self._client is not None:
            _ = self.hass.async_create_task(self._cleanup_login())


class QuiltOptionsFlow(config_entries.OptionsFlow):
    """Options flow: lets the user adjust the polling fallback interval."""

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> config_entries.ConfigFlowResult:
        """Show (and process) the options form."""
        if user_input is not None:
            return self.async_create_entry(data=user_input)

        current: int = self.config_entry.options.get(
            CONF_POLLING_INTERVAL, COORDINATOR_UPDATE_INTERVAL_MINUTES
        )
        return self.async_show_form(
            step_id="init",
            data_schema=vol.Schema(
                {
                    vol.Required(
                        CONF_POLLING_INTERVAL, default=current
                    ): NumberSelector(
                        NumberSelectorConfig(
                            min=COORDINATOR_UPDATE_INTERVAL_MIN,
                            max=COORDINATOR_UPDATE_INTERVAL_MAX,
                            step=1,
                            mode=NumberSelectorMode.BOX,
                            unit_of_measurement="minutes",
                        )
                    ),
                }
            ),
        )
