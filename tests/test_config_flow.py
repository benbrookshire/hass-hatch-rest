"""Config flow tests."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_USER
from homeassistant.const import CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResultType
import pytest
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.hatch_rest.api.errors import HatchAuthError, HatchConnectionError
from custom_components.hatch_rest.const import DOMAIN

LOGIN = "custom_components.hatch_rest.config_flow.HatchRestClient.login"
USER_INPUT = {CONF_EMAIL: " parent@example.com ", CONF_PASSWORD: "hunter2"}


@pytest.fixture(autouse=True)
def no_setup():
    with patch("custom_components.hatch_rest.async_setup_entry", return_value=True):
        yield


async def test_user_flow_creates_entry(hass: HomeAssistant):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    with patch(LOGIN, AsyncMock(return_value={"id": 42})):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["title"] == "parent@example.com"
    assert result["data"] == {CONF_EMAIL: "parent@example.com", CONF_PASSWORD: "hunter2"}
    assert result["result"].unique_id == "42"


@pytest.mark.parametrize(
    ("error", "key"),
    [
        (HatchAuthError("bad"), "invalid_auth"),
        (HatchConnectionError("down"), "cannot_connect"),
        (ValueError("boom"), "unknown"),
    ],
)
async def test_user_flow_errors_then_recovers(hass: HomeAssistant, error, key):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(LOGIN, AsyncMock(side_effect=error)):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": key}

    with patch(LOGIN, AsyncMock(return_value={"id": 42})):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_duplicate_account_aborts(hass: HomeAssistant):
    MockConfigEntry(domain=DOMAIN, unique_id="42", data={}).add_to_hass(hass)
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": SOURCE_USER})
    with patch(LOGIN, AsyncMock(return_value={"id": 42})):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"


async def test_reauth_updates_password(hass: HomeAssistant):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id="42",
        data={CONF_EMAIL: "parent@example.com", CONF_PASSWORD: "old"},
    )
    entry.add_to_hass(hass)
    result = await entry.start_reauth_flow(hass)
    assert result["step_id"] == "reauth_confirm"

    with patch(LOGIN, AsyncMock(side_effect=HatchAuthError("bad"))):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "wrong"}
        )
    assert result["errors"] == {"base": "invalid_auth"}

    with patch(LOGIN, AsyncMock(return_value={"id": 42})):
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {CONF_PASSWORD: "new"}
        )
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_PASSWORD] == "new"
