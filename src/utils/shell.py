import asyncio

import structlog

log = structlog.get_logger()


async def run_command(
    *args: str,
    timeout: int = 60,
    cwd: str | None = None,
) -> tuple[str, str, int]:
    """Run a shell command asynchronously and return (stdout, stderr, returncode)."""
    log.debug("shell_exec", command=args)

    proc = await asyncio.create_subprocess_exec(
        *args,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except TimeoutError:
        proc.kill()
        await proc.communicate()
        raise TimeoutError(f"Command timed out after {timeout}s: {' '.join(args)}")

    stdout = stdout_bytes.decode("utf-8", errors="replace")
    stderr = stderr_bytes.decode("utf-8", errors="replace")

    if proc.returncode != 0:
        log.warning(
            "shell_error",
            command=args,
            returncode=proc.returncode,
            stderr=stderr[:500],
        )

    return stdout, stderr, proc.returncode or 0
