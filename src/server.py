from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from starlette.middleware.sessions import SessionMiddleware

from src.api.router import api_router
from src.db.pool import close_pool, get_pool
from src.utils.logger import setup_logging

# Base directory for templates and static files
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    setup_logging()
    import structlog

    log = structlog.get_logger()

    # Run database migrations on startup
    try:
        from src.db.migrate import run_migrations
        await run_migrations()
        log.info("migrations_complete")
    except Exception as e:
        log.warning("migrations_failed", error=str(e))

    try:
        await get_pool()
        log.info("database_connected")
    except Exception as e:
        # Don't crash on startup — Railway may still be provisioning the DB
        log.warning("database_connection_deferred", error=str(e))
    yield
    await close_pool()


app = FastAPI(
    title="LakeStream",
    version="0.1.0",
    description="B2B web scraping and data extraction platform",
    lifespan=lifespan,
)


@app.get("/ping")
async def root_ping() -> dict:
    """Liveness probe — instant 200, no dependencies. Use for Railway healthcheck."""
    return {"status": "ok"}


# Middleware: order matters — Starlette processes add_middleware in LIFO,
# so the LAST added runs FIRST (outermost). We want:
#   Request → SessionMiddleware (decode cookie) → set_tenant_context (read session) → Route
# So register set_tenant_context first, then SessionMiddleware on top.
from src.api.middleware.auth import TenantContextMiddleware  # noqa: E402
from src.config.settings import get_settings as _get_settings  # noqa: E402

app.add_middleware(TenantContextMiddleware)
app.add_middleware(
    SessionMiddleware,
    secret_key=_get_settings().jwt_secret,
    session_cookie="ls_session",
)

# Mount static files
app.mount("/static", StaticFiles(directory=BASE_DIR / "static"), name="static")

# Configure Jinja2 templates
templates = Jinja2Templates(directory=BASE_DIR / "templates" / "web")


# Custom Jinja2 filters
def timeago_filter(dt: datetime | None) -> str:
    """Convert datetime to human-readable relative time."""
    if not dt:
        return "never"
    now = datetime.now(dt.tzinfo) if dt.tzinfo else datetime.now()
    diff = now - dt
    seconds = diff.total_seconds()

    if seconds < 60:
        return "just now"
    if seconds < 3600:
        minutes = int(seconds / 60)
        return f"{minutes}m ago"
    if seconds < 86400:
        hours = int(seconds / 3600)
        return f"{hours}h ago"
    if seconds < 604800:
        days = int(seconds / 86400)
        return f"{days}d ago"

    return dt.strftime("%b %d")


templates.env.filters["timeago"] = timeago_filter

# Include API routes (under /api prefix)
app.include_router(api_router, prefix="/api")

# Import and include web routes (after templates are configured)
from src.api.routes.web import router as web_router  # noqa: E402

app.include_router(web_router)
