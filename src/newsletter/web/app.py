from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from newsletter.auth import LoginLimiter
from newsletter.config import Settings, get_settings
from newsletter.db import make_engine, make_sessionmaker
from newsletter.http import PoliteClient
from newsletter.ingest.base import Adapter, default_adapters
from newsletter.web import api, pages

HERE = Path(__file__).parent


def create_app(settings: Settings | None = None, session_factory=None, client: PoliteClient | None = None,
               adapters: list[Adapter] | None = None,
               now: Callable[[], datetime] | None = None) -> FastAPI:
    settings = settings or get_settings()
    if not settings.session_secret or not settings.link_signing_secret:
        raise ValueError("SESSION_SECRET and LINK_SIGNING_SECRET must be set")
    app = FastAPI(title="newsletter", docs_url=None, redoc_url=None, openapi_url=None)
    app.state.settings = settings
    app.state.session_factory = session_factory or make_sessionmaker(make_engine(settings.database_url))
    app.state.client = client or PoliteClient()
    app.state.adapters = adapters if adapters is not None else default_adapters()
    app.state.now = now or (lambda: datetime.now(UTC))
    app.state.limiter = LoginLimiter()
    app.state.templates = Jinja2Templates(directory=HERE / "templates")
    app.mount("/static", StaticFiles(directory=HERE / "static"), name="static")
    app.include_router(pages.router)
    app.include_router(api.router)
    return app
