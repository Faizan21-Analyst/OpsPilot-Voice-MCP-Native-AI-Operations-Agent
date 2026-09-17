"""
Structured logging setup. Call configure_logging() once at app startup.
"""
import logging
import structlog


def configure_logging(log_level: str = "INFO") -> None:
    logging.basicConfig(level=log_level, format="%(message)s")

    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.processors.JSONRenderer(),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(logging.getLevelName(log_level)),
    )


def get_logger(name: str):
    return structlog.get_logger(name)