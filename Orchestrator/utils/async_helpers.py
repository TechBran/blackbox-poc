"""Async utilities for offloading blocking I/O to thread pools.

The BlackBox Orchestrator uses synchronous `requests` library for LLM API calls.
These block the uvicorn event loop, freezing all concurrent users.
These helpers offload blocking calls to a dedicated thread pool.
"""

import asyncio
from concurrent.futures import ThreadPoolExecutor

# Dedicated thread pool for LLM API calls (8 threads = 8 concurrent LLM calls)
# Separate from uvicorn's default pool to prevent starvation
_llm_executor = ThreadPoolExecutor(max_workers=8, thread_name_prefix="llm-io")

# Lighter pool for web tools, TTS, and other I/O
_io_executor = ThreadPoolExecutor(max_workers=4, thread_name_prefix="misc-io")


async def run_blocking(func, *args, executor=None, **kwargs):
    """Run a blocking function in a thread pool without blocking the event loop.

    Usage:
        # Instead of:
        result = requests.post(url, json=payload, timeout=200)

        # Do:
        result = await run_blocking(requests.post, url, json=payload, timeout=200)

    Args:
        func: Synchronous callable
        *args: Positional arguments
        executor: ThreadPoolExecutor (default: _llm_executor)
        **kwargs: Keyword arguments

    Returns:
        Whatever func returns
    """
    loop = asyncio.get_running_loop()
    pool = executor or _llm_executor
    return await loop.run_in_executor(pool, lambda: func(*args, **kwargs))
