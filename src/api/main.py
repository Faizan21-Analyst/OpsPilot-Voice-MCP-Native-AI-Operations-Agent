from contextlib import asynccontextmanager
from fastapi import FastAPI

from src.core.config import load_settings
from src.core.logging import configure_logging, get_logger
from src.core.container import Container
from src.api.routes import health

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
app.include_router(health.router)