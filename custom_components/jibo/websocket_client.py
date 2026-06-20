import asyncio
import json
import logging
from collections.abc import Awaitable, Callable
from typing import Any
from urllib.parse import urlparse, urlunparse

import aiohttp

from .const import WS_PATH

_LOGGER = logging.getLogger(__name__)

MessageHandler = Callable[[dict[str, Any]], Awaitable[None]]


def build_websocket_url(server_url: str) -> str:
    parsed = urlparse(server_url.strip())
    scheme = "wss" if parsed.scheme == "https" else "ws"
    netloc = parsed.netloc or parsed.path
    path = WS_PATH if WS_PATH.startswith("/") else f"/{WS_PATH}"
    return urlunparse((scheme, netloc, path, "", "", ""))


class OpenJiboWebSocketClient:
    """Maintains an outbound WebSocket to the OpenJibo server."""

    def __init__(
        self,
        server_url: str,
        instance_id: str,
        on_message: MessageHandler,
        link_id: str | None = None,
    ) -> None:
        self._server_url = server_url
        self._instance_id = instance_id
        self._link_id = link_id
        self._on_message = on_message
        self._session: aiohttp.ClientSession | None = None
        self._ws: aiohttp.ClientWebSocketResponse | None = None
        self._task: asyncio.Task | None = None
        self._stop_event = asyncio.Event()
        self._connected = False

    @property
    def connected(self) -> bool:
        return self._connected

    async def start(self) -> None:
        if self._task is not None:
            return

        self._stop_event.clear()
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is not None:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        if self._ws is not None and not self._ws.closed:
            await self._ws.close()
        self._ws = None

        if self._session is not None and not self._session.closed:
            await self._session.close()
        self._session = None

        self._connected = False

    async def _run(self) -> None:
        backoff = 1
        while not self._stop_event.is_set():
            try:
                await self._connect_once()
                backoff = 1
            except asyncio.CancelledError:
                raise
            except Exception as err:  # noqa: BLE001 - reconnect loop
                _LOGGER.warning("OpenJibo WebSocket connection failed: %s", err)
                self._connected = False

            if self._stop_event.is_set():
                break

            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, 60)

    async def _connect_once(self) -> None:
        ws_url = build_websocket_url(self._server_url)
        timeout = aiohttp.ClientTimeout(total=None, connect=15, sock_read=None)
        self._session = aiohttp.ClientSession(timeout=timeout)
        self._ws = await self._session.ws_connect(ws_url, heartbeat=30)

        await self._ws.send_json(
            {
                "type": "register",
                "instanceId": self._instance_id,
                **({"linkId": self._link_id} if self._link_id else {}),
            }
        )

        self._connected = True
        _LOGGER.info("Connected to OpenJibo server at %s", ws_url)

        while not self._stop_event.is_set():
            msg = await self._ws.receive()
            if msg.type in (aiohttp.WSMsgType.CLOSE, aiohttp.WSMsgType.CLOSED, aiohttp.WSMsgType.ERROR):
                break

            if msg.type != aiohttp.WSMsgType.TEXT:
                continue

            try:
                payload = json.loads(msg.data)
            except json.JSONDecodeError:
                _LOGGER.warning("Ignoring non-JSON WebSocket message from OpenJibo server")
                continue

            await self._on_message(payload)

        self._connected = False
