"""Authenticated, time-bounded WebSocket bridge to the container VNC socket."""
from __future__ import annotations

import asyncio
import base64
import contextlib
import os

from fastapi import WebSocket
from starlette.websockets import WebSocketDisconnect

from backend.infrastructure.browser.console_access import (
    authorize_grant,
    grant_remaining_seconds,
)
from backend.infrastructure.runtime.webauth import gateway_authorized


async def proxy_browser_console(websocket: WebSocket) -> None:
    headers = websocket.headers
    cookie_header = headers.get("x-infoos-original-cookie", "")
    edge_authenticated = headers.get("x-infoos-edge-authenticated") == "1"
    token = headers.get("x-infoos-console-grant", "")
    allowed = await asyncio.to_thread(
        gateway_authorized, cookie_header, edge_authenticated=edge_authenticated
    )
    allowed = allowed and authorize_grant(
        token,
        origin=headers.get("x-infoos-original-origin", ""),
        referer=headers.get("x-infoos-original-referer", ""),
        forwarded_host=headers.get("x-forwarded-host", ""),
        forwarded_proto=headers.get("x-forwarded-proto", ""),
    )
    if not allowed:
        await websocket.close(code=1008)
        return

    protocols = {
        item.strip()
        for item in headers.get("sec-websocket-protocol", "").split(",")
        if item.strip()
    }
    protocol = "binary" if "binary" in protocols else "base64" if "base64" in protocols else None
    socket_path = os.environ.get("INFOOS_VNC_SOCKET", "/tmp/vnc.sock")
    try:
        reader, writer = await asyncio.open_unix_connection(socket_path)
    except OSError:
        await websocket.close(code=1011)
        return

    await websocket.accept(subprotocol=protocol)
    tasks = {
        asyncio.create_task(_client_to_vnc(websocket, writer, protocol)),
        asyncio.create_task(_vnc_to_client(websocket, reader, protocol)),
        asyncio.create_task(_authorization_guard(websocket, token, cookie_header, edge_authenticated)),
    }
    try:
        _, pending = await asyncio.wait(tasks, return_when=asyncio.FIRST_COMPLETED)
        for task in pending:
            task.cancel()
        await asyncio.gather(*pending, return_exceptions=True)
    finally:
        writer.close()
        with contextlib.suppress(OSError):
            await writer.wait_closed()
        with contextlib.suppress(RuntimeError):
            await websocket.close()


async def _client_to_vnc(websocket: WebSocket, writer: asyncio.StreamWriter, protocol: str | None) -> None:
    try:
        while True:
            message = await websocket.receive()
            if message["type"] == "websocket.disconnect":
                return
            payload = message.get("bytes")
            if payload is None and message.get("text") is not None:
                text = message["text"]
                payload = base64.b64decode(text) if protocol == "base64" else text.encode()
            if payload:
                writer.write(payload)
                await writer.drain()
    except WebSocketDisconnect:
        return


async def _vnc_to_client(websocket: WebSocket, reader: asyncio.StreamReader, protocol: str | None) -> None:
    while payload := await reader.read(65536):
        if protocol == "base64":
            await websocket.send_text(base64.b64encode(payload).decode())
        else:
            await websocket.send_bytes(payload)


async def _authorization_guard(
    websocket: WebSocket, token: str, cookie_header: str, edge_authenticated: bool = False
) -> None:
    while True:
        remaining = grant_remaining_seconds(token)
        if remaining <= 0 or not await asyncio.to_thread(
            gateway_authorized, cookie_header, edge_authenticated=edge_authenticated
        ):
            await websocket.close(code=1008, reason="Browser Console authorization expired")
            return
        await asyncio.sleep(min(5.0, remaining))
