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
            return

        if message_type == "command":
            await self._handle_command(payload.get("command"))
            return

    async def _handle_command(self, command: str | None) -> None:
        if command == "lights_off_current_room":
            await self._handle_lights_off_current_room()
            return

        _LOGGER.warning("OpenJibo server sent unknown command: %s", command)

    async def _handle_lights_off_current_room(self) -> None:
        from homeassistant.helpers import device_registry as dr

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, self.entry.entry_id)})
        if device is None:
            _LOGGER.warning("OpenJibo device entry not found; cannot turn off room lights")
            return

        if not device.area_id:
            _LOGGER.warning("OpenJibo device has no area assigned; cannot turn off room lights")
            return

        await self.hass.services.async_call(
            "light",
            "turn_off",
            target={"area_id": [device.area_id]},
        )
        _LOGGER.info("Turned off lights in area %s for OpenJibo device", device.area_id)
