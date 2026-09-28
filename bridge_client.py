"""Async local RPC client. Never retries a command whose delivery is uncertain."""

import asyncio
import json
import re
import time

from bridge_core import runtime_directory


async def exchange(path, payload):
    """Open one socket, send one request, read one response, then close it."""
    # JSON can use twelve bytes for one Unicode character. Allow a full page.
    reader, writer = await asyncio.open_unix_connection(str(path), limit=1_000_000)
    try:
        message = json.dumps(payload) + "\n"
        writer.write(message.encode("utf-8"))
        await writer.drain()
        raw = await reader.readline()
        if not raw:
            raise ConnectionError("Bridge closed without responding")
        return json.loads(raw)
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except OSError:
            pass


async def request(session_id: str, operation: str, **arguments) -> dict:
    if not re.fullmatch(r"[0-9a-f]{32}", session_id):
        raise ValueError("Invalid session ID; use list_sessions")
    path = runtime_directory() / f"{session_id}.sock"
    payload = {"operation": operation, "expires_at": time.time() + 4}
    payload.update(arguments)

    try:
        response = await asyncio.wait_for(exchange(path, payload), timeout=8)
    except (OSError, asyncio.TimeoutError, ValueError) as exc:
        raise RuntimeError(
            f"SecureCRT bridge unavailable: {exc}. Run securecrt_bridge.py in the tab. "
            "If this was a write, delivery may be uncertain: inspect list_sessions and "
            "read_output before retrying; a command may already have executed."
        ) from exc
    if "error" in response:
        raise RuntimeError(response["error"])
    return response["result"]


async def inspect_session(path):
    """A stale socket should not prevent us from listing the other tabs."""
    try:
        session = await request(path.stem, "status")
        return session, None
    except (RuntimeError, ValueError) as exc:
        error = {"session_id": path.stem, "error": str(exc)}
        return None, error


async def sessions() -> dict:
    pending = []
    for path in runtime_directory().glob("*.sock"):
        pending.append(inspect_session(path))

    # Check tabs together so one unresponsive tab does not delay each other tab.
    results = await asyncio.gather(*pending)
    connected = []
    unavailable = []
    for session, error in results:
        if session:
            connected.append(session)
        if error:
            unavailable.append(error)

    return {
        "sessions": connected,
        "unavailable_bridges": unavailable,
        "setup": "In each tab to share: Script > Run > MCP/securecrt_bridge.py",
    }


async def wait_for_job(session_id: str, job_id: str, wait_seconds: float) -> dict:
    deadline = time.monotonic() + wait_seconds
    while True:
        result = await request(session_id, "output", job_id=job_id)
        if result["status"] != "running" or time.monotonic() >= deadline:
            return result
        await asyncio.sleep(0.2)
