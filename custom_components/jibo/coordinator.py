import logging
import re
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
_LIGHT_SUFFIXES = (" light", " lights", " lamp", " lamps")


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
            entry.data.get(CONF_LINK_ID),
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
            if self.entry.data.get(CONF_LINK_ID):
                _LOGGER.debug("Ignoring verification code; Home Assistant is already paired")
                return

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
            await self._handle_command(payload)
            return

    async def _handle_command(self, payload: dict[str, Any]) -> None:
        command = payload.get("command")
        if command == "lights_off_current_room":
            await self._handle_lights_room("turn_off")
            return

        if command == "lights_on_current_room":
            await self._handle_lights_room("turn_on")
            return

        if command == "lights_off_named":
            await self._handle_lights_named("turn_off", payload.get("targetName"))
            return

        if command == "lights_on_named":
            await self._handle_lights_named("turn_on", payload.get("targetName"))
            return

        _LOGGER.warning("OpenJibo server sent unknown command: %s", command)

    async def _handle_lights_room(self, service: str) -> None:
        area_id = self._get_jibo_area_id()
        if area_id is None:
            return

        await self.hass.services.async_call(
            "light",
            service,
            target={"area_id": [area_id]},
        )
        _LOGGER.info("Called light.%s for area %s", service, area_id)

    async def _handle_lights_named(self, service: str, target_name: str | None) -> None:
        if not target_name:
            _LOGGER.warning("OpenJibo named light command missing targetName")
            return

        area_id = self._get_jibo_area_id()
        entity_id = self._find_matching_light(target_name, area_id)
        if entity_id is None:
            _LOGGER.warning("No light matched target %r", target_name)
            return

        await self.hass.services.async_call(
            "light",
            service,
            {"entity_id": entity_id},
        )
        _LOGGER.info("Called light.%s for entity %s (target %r)", service, entity_id, target_name)

    def _get_jibo_area_id(self) -> str | None:
        from homeassistant.helpers import device_registry as dr

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, self.entry.entry_id)})
        if device is None:
            _LOGGER.warning("OpenJibo device entry not found; cannot control room lights")
            return None

        if not device.area_id:
            _LOGGER.warning("OpenJibo device has no area assigned; cannot control room lights")
            return None

        return device.area_id

    def _find_matching_light(self, target_name: str, area_id: str | None) -> str | None:
        from homeassistant.helpers import entity_registry as er

        entity_registry = er.async_get(self.hass)
        normalized_target = _normalize_light_name(target_name)
        if not normalized_target:
            return None

        area_candidates = self._list_light_entity_ids(entity_registry, area_id)
        match = self._match_light_entity(normalized_target, area_candidates)
        if match is not None:
            return match

        if area_id is not None:
            all_candidates = self._list_light_entity_ids(entity_registry, None)
            return self._match_light_entity(normalized_target, all_candidates)

        return None

    def _list_light_entity_ids(
        self,
        entity_registry: Any,
        area_id: str | None,
    ) -> list[str]:
        candidates: list[str] = []
        for entity in entity_registry.entities.values():
            if entity.domain != "light":
                continue
            if area_id is not None and entity.area_id != area_id:
                continue
            candidates.append(entity.entity_id)
        return candidates

    def _match_light_entity(self, normalized_target: str, entity_ids: list[str]) -> str | None:
        exact_match: str | None = None
        partial_match: str | None = None

        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            friendly_name = state.name if state is not None else entity_id
            normalized_friendly = _normalize_light_name(friendly_name)
            if not normalized_friendly:
                continue

            if normalized_friendly == normalized_target:
                exact_match = entity_id
                break

            if (
                normalized_target in normalized_friendly
                or normalized_friendly in normalized_target
            ) and partial_match is None:
                partial_match = entity_id

        return exact_match or partial_match


def _normalize_light_name(value: str) -> str:
    normalized = value.lower().strip()
    normalized = normalized.replace("'", "").replace("’", "")
    normalized = re.sub(r"\s+", " ", normalized)
    for suffix in _LIGHT_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized
