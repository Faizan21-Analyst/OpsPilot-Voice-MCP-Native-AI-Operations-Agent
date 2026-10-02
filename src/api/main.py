import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

from src.api.routes import health, auth, admin, chat, portal
from src.api.routes import voice_ws
from src.core.config import load_settings
from src.core.container import Container
from src.core.logging import configure_logging, get_logger
from src.mcp_servers.sql.database import build_engine, build_session_factory
from src.mcp_servers.sql.migrate import migrate_database

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    configure_logging(settings.log_level)
    app.state.container = Container(settings)

    # Portal database (employees, tickets, projects, accounts, audit log).
    # Migration is idempotent: it only adds what is missing and never drops or rewrites existing data.
    # If the database is unavailable the original chat/voice features keep working with the legacy demo logins.
    engine = None
    try:
        result = await asyncio.to_thread(migrate_database, settings.database.url)
        engine = build_engine(settings.database)
        app.state.session_factory = build_session_factory(engine)
        log.info("portal_db_ready", columns_added=result.get("columns_added"), seeded=result.get("seeded"))
    except Exception as e:
        log.error("portal_db_unavailable", error=str(e))

    log.info("app_started", env=settings.env)
    yield
    if engine is not None:
        await engine.dispose()
    log.info("app_shutdown")


app = FastAPI(title="MCP Agent Platform", lifespan=lifespan)


@app.middleware("http")
async def disable_browser_cache(request, call_next):
    """Stop browsers (normal tabs, not just incognito) from serving stale HTML/JS/CSS."""
    response = await call_next(request)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


app.include_router(health.router)
app.include_router(auth.router)
app.include_router(admin.router)
app.include_router(voice_ws.router)
app.include_router(chat.router)
app.include_router(portal.router)
# Must stay last: the static mount at "/" would otherwise shadow the API routes above.
app.mount("/", StaticFiles(directory="static", html=True), name="static")
