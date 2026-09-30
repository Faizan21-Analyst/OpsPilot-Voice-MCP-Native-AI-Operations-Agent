from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.api.routes import health, auth, admin
from src.api.routes import voice_ws
from src.core.config import load_settings
from src.core.logging import configure_logging, get_logger
from src.core.container import Container
from src.api.routes import health, auth
from fastapi.staticfiles import StaticFiles
from src.api.routes import chat

log = get_logger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = load_settings()
    configure_logging(settings.log_level)
    app.state.container = Container(settings)
    log.info("app_started", env=settings.env)
    yield
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
app.mount("/", StaticFiles(directory="static", html=True), name="static")
