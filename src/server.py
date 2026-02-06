from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from src.api.router import api_router
from src.db.pool import close_pool, get_pool
from src.utils.logger import setup_logging

# Base directory for templates and static files
BASE_DIR = Path(__file__).resolve().parent


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncGenerator[None]:
    setup_logging()
    await get_pool()
    yield
    await close_pool()


app = FastAPI(
    title="Lake B2B Scraper",
    version="0.1.0",
    description="Template-based web scraping platform for B2B data enrichment",
    lifespan=lifespan,
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
