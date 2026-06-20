import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.config_entries import ConfigEntry
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .const import (
    CONF_INSTANCE_ID,
    CONF_JIBO_FRIENDLY_NAME,
    CONF_LINK_ID,
    CONF_SERVER_URL,
    DOMAIN,
    NOTIFICATION_ID_PREFIX,
)
from .websocket_client import OpenJiboWebSocketClient

_LOGGER = logging.getLogger(__name__)


class JiboCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinates the OpenJibo server WebSocket connection."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{DOMAIN}_{entry.entry_id}",
        )
        self.entry = entry
        self._client = OpenJiboWebSocketClient(
            entry.data[CONF_SERVER_URL],
            entry.data[CONF_INSTANCE_ID],
            self._handle_message,
        )

    @property
    def connected(self) -> bool:
        return self._client.connected

    async def async_start(self) -> None:
        await self._client.start()

    async def async_shutdown(self) -> None:
        await self._client.stop()

    async def _handle_message(self, payload: dict[str, Any]) -> None:
        message_type = payload.get("type")
        if message_type == "verification_code":
            code = payload.get("code", "")
            notification_id = f"{NOTIFICATION_ID_PREFIX}{self.entry.data[CONF_INSTANCE_ID]}"
            await self.hass.services.async_call(
                "persistent_notification",
                "create",
                {
                    "title": "OpenJibo Pairing Code",
                    "message": (
                        f"Your OpenJibo verification code is **{code}**. "
                        "Enter this code in the OpenJibo portal after verifying your Jibo."
                    ),
                    "notification_id": notification_id,
                },
            )
            self.async_set_updated_data(
                {
                    "verification_code": code,
                    "paired": False,
                }
            )
            return

        if message_type == "paired":
            notification_id = f"{NOTIFICATION_ID_PREFIX}{self.entry.data[CONF_INSTANCE_ID]}"
            await self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": notification_id},
            )

            updates = {
                CONF_LINK_ID: payload.get("linkId"),
                CONF_JIBO_FRIENDLY_NAME: payload.get("jiboFriendlyName"),
            }
            self.hass.config_entries.async_update_entry(
                self.entry,
                data={**self.entry.data, **{k: v for k, v in updates.items() if v}},
            )
            self.async_set_updated_data(
                {
                    "verification_code": None,
                    "paired": True,
                    "jibo_friendly_name": payload.get("jiboFriendlyName"),
                    "link_id": payload.get("linkId"),
                }
            )
            return

        if message_type == "error":
            _LOGGER.error("OpenJibo server error: %s", payload.get("message"))
