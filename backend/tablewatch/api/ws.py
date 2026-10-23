"""WebSocket fan-out to dashboard clients."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from fastapi import WebSocket

log = logging.getLogger("tablewatch.ws")


class Hub:
    def __init__(self) -> None:
        self.clients: set[WebSocket] = set()

    async def connect(self, ws: WebSocket) -> None:
        await ws.accept()
        self.clients.add(ws)

    def disconnect(self, ws: WebSocket) -> None:
        self.clients.discard(ws)

    async def send(self, ws: WebSocket, msg: dict[str, Any]) -> None:
        await asyncio.wait_for(ws.send_text(json.dumps(msg, default=str)), timeout=2.0)

    async def broadcast(self, messages: list[dict[str, Any]]) -> None:
        if not self.clients or not messages:
            return
        payload = [json.dumps(m, default=str) for m in messages]
        dead = []
        for ws in list(self.clients):
            try:
                for p in payload:
                    await asyncio.wait_for(ws.send_text(p), timeout=2.0)
            except Exception:
                dead.append(ws)
        for ws in dead:
            self.disconnect(ws)
