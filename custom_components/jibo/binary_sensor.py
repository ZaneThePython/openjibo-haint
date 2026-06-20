import asyncio
import logging
from datetime import timedelta

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN

_LOGGER = logging.getLogger(__name__)

SCAN_INTERVAL = timedelta(seconds=30)
_CONNECT_TIMEOUT = 5.0
_JIBO_PORT = 8089


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    data = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        [JiboConnectivitySensor(entry, data.get("jibo_ip", ""), data["name"], data.get("coordinator"))],
        update_before_add=True,
    )


class JiboConnectivitySensor(BinarySensorEntity):
    _attr_device_class = BinarySensorDeviceClass.CONNECTIVITY
    _attr_should_poll = True

    def __init__(self, entry: ConfigEntry, ip: str, name: str, coordinator) -> None:
        self._ip = ip
        self._coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_connectivity"
        self._attr_name = f"{name} Online"
        self._attr_is_on = False
        self._attr_device_info = {
            "identifiers": {(DOMAIN, entry.entry_id)},
            "name": name,
            "manufacturer": "Jibo Inc.",
            "model": "Jibo",
        }

    async def async_update(self) -> None:
        if self._ip:
            try:
                _, writer = await asyncio.wait_for(
                    asyncio.open_connection(self._ip, _JIBO_PORT),
                    timeout=_CONNECT_TIMEOUT,
                )
                writer.close()
                try:
                    await writer.wait_closed()
                except Exception:
                    pass
                self._attr_is_on = True
            except (asyncio.TimeoutError, OSError):
                self._attr_is_on = False
            return

        self._attr_is_on = bool(self._coordinator and self._coordinator.connected)
