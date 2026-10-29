"""Run synchronous MCP tools outside the event loop."""
import functools

import anyio


def threaded(fn):
    @functools.wraps(fn)
    async def wrapper(*args, **kwargs):
        return await anyio.to_thread.run_sync(functools.partial(fn, *args, **kwargs))
    return wrapper
