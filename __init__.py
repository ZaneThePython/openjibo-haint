import asyncio
import aiohttp
import voluptuous as vol
from homeassistant.core import HomeAssistant
import logging
_LOGGER = logging.getLogger(__name__)

from .const import DOMAIN

async def async_setup(hass: HomeAssistant, config: dict):
    """Set up the Jibo integration."""

    async def handle_say_service(call):
        message = call.data.get("message")
        ip = hass.data[DOMAIN]["jibo_ip"]
        url = f"http://{ip}:8089/tts_speak"
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
            "cached": "TRUE"
        }

        async with aiohttp.ClientSession() as session:
            try:
                async with session.post(url, json=payload) as response:
                    if response.status != 200:
                        _LOGGER.error("Failed to speak: %s", await response.text())
            except aiohttp.ClientError as e:
                _LOGGER.error("Error communicating with Jibo: %s", e)

    hass.services.async_register(DOMAIN, "say", handle_say_service)
    return True

async def async_setup_entry(hass, entry):
    """Store IP from config flow."""
    hass.data[DOMAIN] = {"jibo_ip": entry.data["jibo_ip"]}
    return True