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
_CLIMATE_SUFFIXES = (" thermostat", " hvac", " heat", " ac")
_DEFAULT_CLIMATE_DELTA = 2.0


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

        if message_type == "unpaired":
            notification_id = f"{NOTIFICATION_ID_PREFIX}{self.entry.data[CONF_INSTANCE_ID]}"
            await self.hass.services.async_call(
                "persistent_notification",
                "dismiss",
                {"notification_id": notification_id},
            )

            cleared_data = {
                key: value
                for key, value in self.entry.data.items()
                if key not in {CONF_LINK_ID, CONF_JIBO_FRIENDLY_NAME}
            }
            self.hass.config_entries.async_update_entry(self.entry, data=cleared_data)
            self._client.clear_link_id()
            self.async_set_updated_data(
                {
                    "verification_code": None,
                    "paired": False,
                    "jibo_friendly_name": None,
                    "link_id": None,
                }
            )
            await self._client.force_reconnect()
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

        if command == "climate_set_temperature_current_room":
            await self._handle_climate_room_set_temp(payload.get("temperature"))
            return

        if command == "climate_set_temperature_named":
            await self._handle_climate_named_set_temp(payload.get("targetName"), payload.get("temperature"))
            return

        if command == "climate_cool_down_current_room":
            await self._handle_climate_room_adjust(-self._parse_delta(payload.get("delta")))
            return

        if command == "climate_warm_up_current_room":
            await self._handle_climate_room_adjust(self._parse_delta(payload.get("delta")))
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

    async def _handle_climate_room_set_temp(self, temperature: Any) -> None:
        parsed_temperature = self._parse_temperature(temperature)
        if parsed_temperature is None:
            _LOGGER.warning("OpenJibo climate set command missing valid temperature")
            return

        area_id = self._get_jibo_area_id()
        if area_id is None:
            return

        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"temperature": parsed_temperature},
            target={"area_id": [area_id]},
        )
        _LOGGER.info("Called climate.set_temperature for area %s to %s", area_id, parsed_temperature)

    async def _handle_climate_named_set_temp(self, target_name: str | None, temperature: Any) -> None:
        if not target_name:
            _LOGGER.warning("OpenJibo named climate command missing targetName")
            return

        parsed_temperature = self._parse_temperature(temperature)
        if parsed_temperature is None:
            _LOGGER.warning("OpenJibo named climate command missing valid temperature")
            return

        area_id = self._get_jibo_area_id()
        entity_id = self._find_matching_climate(target_name, area_id)
        if entity_id is None:
            _LOGGER.warning("No climate entity matched target %r", target_name)
            return

        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": parsed_temperature},
        )
        _LOGGER.info(
            "Called climate.set_temperature for entity %s to %s (target %r)",
            entity_id,
            parsed_temperature,
            target_name,
        )

    async def _handle_climate_room_adjust(self, delta: float) -> None:
        from homeassistant.helpers import entity_registry as er

        area_id = self._get_jibo_area_id()
        if area_id is None:
            return

        entity_ids = self._list_climate_entity_ids(er.async_get(self.hass), area_id)
        if not entity_ids:
            _LOGGER.warning("No climate entities found for area %s", area_id)
            return

        for entity_id in entity_ids:
            await self._adjust_climate_entity(entity_id, delta)

    async def _adjust_climate_entity(self, entity_id: str, delta: float) -> None:
        state = self.hass.states.get(entity_id)
        if state is None or state.state in {"unavailable", "unknown"}:
            _LOGGER.warning("Climate entity %s unavailable for adjustment", entity_id)
            return

        current = state.attributes.get("temperature")
        if current is None:
            _LOGGER.warning("Climate entity %s has no setpoint to adjust", entity_id)
            return

        min_temp = state.attributes.get("min_temp")
        max_temp = state.attributes.get("max_temp")
        new_temp = float(current) + delta

        if min_temp is not None:
            new_temp = max(float(min_temp), new_temp)
        if max_temp is not None:
            new_temp = min(float(max_temp), new_temp)

        await self.hass.services.async_call(
            "climate",
            "set_temperature",
            {"entity_id": entity_id, "temperature": new_temp},
        )
        _LOGGER.info("Adjusted climate entity %s from %s to %s", entity_id, current, new_temp)

    def _get_jibo_area_id(self) -> str | None:
        from homeassistant.helpers import device_registry as dr
        from homeassistant.helpers import entity_registry as er

        device_registry = dr.async_get(self.hass)
        device = device_registry.async_get_device(identifiers={(DOMAIN, self.entry.entry_id)})
        if device is not None and device.area_id:
            return device.area_id

        entity_registry = er.async_get(self.hass)
        for entity in entity_registry.entities.values():
            if entity.config_entry_id != self.entry.entry_id or entity.platform != DOMAIN:
                continue

            area_id = self._resolve_entity_area_id(entity_registry, device_registry, entity)
            if area_id:
                _LOGGER.info("Resolved OpenJibo area %s from entity %s", area_id, entity.entity_id)
                return area_id

        if device is None:
            _LOGGER.warning("OpenJibo device entry not found; cannot control room lights")
        else:
            _LOGGER.warning("OpenJibo device has no area assigned; cannot control room lights")
        return None

    def _resolve_entity_area_id(self, entity_registry, device_registry, entity) -> str | None:
        if entity.area_id:
            return entity.area_id

        if not entity.device_id:
            return None

        device = device_registry.async_get(entity.device_id)
        if device is None or not device.area_id:
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
        from homeassistant.helpers import device_registry as dr

        device_registry = dr.async_get(self.hass)
        candidates: list[str] = []
        for entity in entity_registry.entities.values():
            if entity.domain != "light":
                continue

            if area_id is not None:
                entity_area_id = self._resolve_entity_area_id(entity_registry, device_registry, entity)
                if entity_area_id != area_id:
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

    def _find_matching_climate(self, target_name: str, area_id: str | None) -> str | None:
        from homeassistant.helpers import entity_registry as er

        entity_registry = er.async_get(self.hass)
        normalized_target = _normalize_climate_name(target_name)
        if not normalized_target:
            return None

        area_candidates = self._list_climate_entity_ids(entity_registry, area_id)
        match = self._match_climate_entity(normalized_target, area_candidates)
        if match is not None:
            return match

        if area_id is not None:
            all_candidates = self._list_climate_entity_ids(entity_registry, None)
            return self._match_climate_entity(normalized_target, all_candidates)

        return None

    def _list_climate_entity_ids(
        self,
        entity_registry: Any,
        area_id: str | None,
    ) -> list[str]:
        from homeassistant.helpers import device_registry as dr

        device_registry = dr.async_get(self.hass)
        candidates: list[str] = []
        for entity in entity_registry.entities.values():
            if entity.domain != "climate":
                continue

            if area_id is not None:
                entity_area_id = self._resolve_entity_area_id(entity_registry, device_registry, entity)
                if entity_area_id != area_id:
                    continue

            candidates.append(entity.entity_id)
        return candidates

    def _match_climate_entity(self, normalized_target: str, entity_ids: list[str]) -> str | None:
        exact_match: str | None = None
        partial_match: str | None = None

        for entity_id in entity_ids:
            state = self.hass.states.get(entity_id)
            friendly_name = state.name if state is not None else entity_id
            normalized_friendly = _normalize_climate_name(friendly_name)
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

    @staticmethod
    def _parse_temperature(value: Any) -> float | None:
        if value is None:
            return None

        try:
            return float(value)
        except (TypeError, ValueError):
            return None

    @staticmethod
    def _parse_delta(value: Any) -> float:
        if value is None:
            return _DEFAULT_CLIMATE_DELTA

        try:
            return float(value)
        except (TypeError, ValueError):
            return _DEFAULT_CLIMATE_DELTA


def _normalize_light_name(value: str) -> str:
    normalized = value.lower().strip()
    normalized = normalized.replace("'", "").replace("’", "")
    normalized = re.sub(r"\s+", " ", normalized)
    for suffix in _LIGHT_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized


def _normalize_climate_name(value: str) -> str:
    normalized = value.lower().strip()
    normalized = normalized.replace("'", "").replace("’", "")
    normalized = re.sub(r"\s+", " ", normalized)
    for suffix in _CLIMATE_SUFFIXES:
        if normalized.endswith(suffix):
            normalized = normalized[: -len(suffix)].strip()
    return normalized
