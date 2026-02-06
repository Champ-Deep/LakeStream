import asyncio
from collections.abc import Callable, Coroutine
from typing import Any

import structlog

log = structlog.get_logger()


async def retry_async[T](
    fn: Callable[..., Coroutine[Any, Any, T]],
    *args: Any,
    max_retries: int = 3,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
    retry_on: tuple[type[Exception], ...] = (Exception,),
    **kwargs: Any,
) -> T:
    """Retry an async function with exponential backoff."""
    last_error: Exception | None = None

    for attempt in range(max_retries + 1):
        try:
            return await fn(*args, **kwargs)
        except retry_on as e:
            last_error = e
            if attempt == max_retries:
                break
            delay = min(base_delay * (2**attempt), max_delay)
            log.warning(
                "retry_attempt",
                attempt=attempt + 1,
                max_retries=max_retries,
                delay=delay,
                error=str(e),
            )
            await asyncio.sleep(delay)

    raise last_error  # type: ignore[misc]
