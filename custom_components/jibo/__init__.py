import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant, ServiceCall
import logging

from .const import DOMAIN, PLATFORMS

_LOGGER = logging.getLogger(__name__)

_SAY_SCHEMA = vol.Schema({
    vol.Required("message"): str,
    vol.Optional("robot"): str,
})


async def async_setup(hass: HomeAssistant, config: dict):
    hass.data.setdefault(DOMAIN, {})
    return True


async def async_setup_entry(hass: HomeAssistant, entry):
    hass.data.setdefault(DOMAIN, {})
    hass.data[DOMAIN][entry.entry_id] = {
        "jibo_ip": entry.data["jibo_ip"],
        "name": entry.data.get("name", entry.title),
    }

    if not hass.services.has_service(DOMAIN, "say"):
        async def handle_say(call: ServiceCall):
            message = call.data["message"]
            robot_filter = call.data.get("robot")

            targets = [
                data["jibo_ip"]
                for data in hass.data[DOMAIN].values()
                if robot_filter is None or data["name"] == robot_filter
            ]

            if not targets:
                _LOGGER.warning(
                    "No Jibo robot matched filter %r. Configured robots: %s",
                    robot_filter,
                    [d["name"] for d in hass.data[DOMAIN].values()],
                )
                return

            payload = {
                "prompt": message,
                "locale": "en-us",
                "voice": "griffin",
                "duration_stretch": 1,
                "pitch": 3,
                "pitchBandwidth": 0.4,
                "mode": "text",
                "outputMode": "stream",
                "timeout": None,
                "volume": 0,
                "whisper": "FALSE",
                "samplerate": 48000,
                "postfilter": 0.4,
                "framerate": 240,
                "unvoicedvoiced": 0.35,
                "allPass": 0.76,
                "gvMCEP": 0.9,
                "cached": "TRUE",
            }

            async with aiohttp.ClientSession() as session:
                for ip in targets:
                    url = f"http://{ip}:8089/tts_speak"
                    try:
                        async with session.post(url, json=payload) as response:
                            if response.status != 200:
                                _LOGGER.error(
                                    "Jibo at %s returned %s: %s",
                                    ip, response.status, await response.text(),
                                )
                    except aiohttp.ClientError as e:
                        _LOGGER.error("Error communicating with Jibo at %s: %s", ip, e)

        hass.services.async_register(DOMAIN, "say", handle_say, schema=_SAY_SCHEMA)

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def async_unload_entry(hass: HomeAssistant, entry):
    unloaded = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)

    if unloaded:
        hass.data[DOMAIN].pop(entry.entry_id, None)

        if not hass.data[DOMAIN]:
            hass.services.async_remove(DOMAIN, "say")

    return unloaded
